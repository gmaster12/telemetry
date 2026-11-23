"""Watch history from the Greystar Emby server, kept in the same `plays` table as Plex.

Live: every poll the poller asks Emby for its sessions (GET /Sessions) and opens, extends and closes one play per
Emby session and item, the same way `Poller.track_plays` does for Plex. Rows have server "Emby", no server_id,
session_key "emby-<session id>", the Emby item id as rating_key and the cover's item id (the show for an episode)
as art, so `/api/emby/poster/<id>` can show it.

Backfill: Emby keeps every start and stop in its activity log (GET /System/ActivityLog/Entries, newest first).
Each "playback.stop" is paired with the same person's latest "playback.start" of the same item before it; the
time between them is the watched time. Only stops from before live tracking began are imported (`emby_live_since`),
so nothing is counted twice; session_key "emby-hist-<entry id>" makes importing again add nothing.

Each Emby person who plays something gets a users row (source "emby", see emby.py) under their member id, so their
history shows on My history and in the admin's People page like a Plex viewer's.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import re
import time

import httpx

from .emby import EmbyAuth, member_id

log = logging.getLogger("telemetry.emby")

SERVER = "Emby"
TICKS = 10_000_000  # Emby ticks per second
METHOD = {"Transcode": "Transcode", "DirectStream": "Direct stream", "DirectPlay": "Direct play"}
PAIR_SECONDS = 12 * 3600  # a start more than this before its stop is not the same play
MAX_POLL_GAP = 30


def res_label(height) -> str | None:
    try:
        h = int(height or 0)
    except (TypeError, ValueError):
        return None
    if not h:
        return None
    if h >= 1600:
        return "4K"
    for cut, name in ((1000, "1080p"), (700, "720p"), (560, "576p"), (470, "480p")):
        if h >= cut:
            return name
    return "SD"


def describe(item: dict) -> dict:
    """Title, subtitle, kind and cover for an Emby item, in the shape Plex rows use (plex.normalize_session)."""
    typ = item.get("Type") or ""
    if typ == "Episode":
        return {"kind": "Episode", "title": item.get("SeriesName") or item.get("Name") or "",
                "subtitle": f"S{item.get('ParentIndexNumber', '?')} · E{item.get('IndexNumber', '?')} · {item.get('Name') or ''}",
                "art": str(item.get("SeriesId") or item.get("Id") or "") or None,
                "parent_key": str(item.get("SeriesId") or ""), "season": item.get("ParentIndexNumber"), "tmdb_id": None}
    if typ in ("Audio", "AudioBook"):
        return {"kind": "Audiobook" if typ == "AudioBook" else "Music", "title": item.get("AlbumArtist") or item.get("Album") or item.get("Name") or "",
                "subtitle": item.get("Name") or "", "art": str(item.get("AlbumId") or item.get("Id") or "") or None,
                "parent_key": "", "season": None, "tmdb_id": None}
    tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
    return {"kind": "Movie", "title": item.get("Name") or "", "subtitle": str(item.get("ProductionYear") or ""),
            "art": str(item.get("Id") or "") or None, "parent_key": "", "season": None,
            "tmdb_id": int(tmdb) if tmdb and str(tmdb).isdigit() else None}


_EP = re.compile(r"^(?P<show>.+?) - S(?P<s>\d+), Ep(?P<e>\d+) - (?P<name>.*)$")


def from_text(what: str) -> dict:
    """An item described only by the activity log's words (it was deleted since): "Show - S6, Ep2 - Name" or a movie."""
    m = _EP.match(what)
    if m:
        return {"kind": "Episode", "title": m["show"], "subtitle": f"S{m['s']} · E{m['e']} · {m['name']}", "art": None,
                "parent_key": "", "season": int(m["s"]), "tmdb_id": None}
    return {"kind": "Movie", "title": what, "subtitle": "", "art": None, "parent_key": "", "season": None, "tmdb_id": None}


def _private(ip: str | None) -> bool:
    try:
        return ipaddress.ip_address((ip or "").strip()).is_private
    except ValueError:
        return False


def _ts(s: str) -> int:
    """Emby's "2026-11-23T04:10:51.3170000Z" (UTC, seven fraction digits) as unix seconds."""
    return int(dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())


class EmbyPlays:
    def __init__(self, auth: EmbyAuth, directory, store):
        self.auth = auth
        self.directory = directory
        self.store = store
        self.active: dict[str, dict] = {}  # session key -> {id, rating_key, last}
        self.error: str | None = None

    def enabled(self) -> bool:
        return self.auth.enabled()

    async def _get(self, path: str, params: dict | None = None):
        r = await self.auth._client().get(self.auth.url + path, params=params, headers={"X-Emby-Token": self.auth.api_key}, timeout=20)
        r.raise_for_status()
        return r.json()

    def _person(self, emby_id: str, name: str | None) -> str:
        member = member_id(emby_id)
        self.directory.upsert_emby_user(member, emby_id, name, (name or "").lower() if "@" in (name or "") else None, seen=True)
        return member

    # ---------- live ----------
    def row(self, s: dict) -> dict | None:
        item = s.get("NowPlayingItem") or {}
        if not item.get("Id") or not s.get("UserId") or item.get("MediaType") not in ("Video", "Audio", None):
            return None
        ps = s.get("PlayState") or {}
        ti = s.get("TranscodingInfo") or {}
        runtime = int(item.get("RunTimeTicks") or 0)
        pos = int(ps.get("PositionTicks") or 0)
        d = describe(item)
        method = METHOD.get(ps.get("PlayMethod") or "", "Transcode" if ti and not (ti.get("IsVideoDirect") and ti.get("IsAudioDirect")) else "Direct play")
        height = ti.get("Height") if method == "Transcode" and ti.get("Height") else item.get("Height")
        return {**d, "server": SERVER, "server_id": None, "session_key": f"emby-{s.get('Id')}", "rating_key": str(item["Id"]),
                "emby_user": s["UserId"], "user": s.get("UserName"), "device": s.get("DeviceName"), "player": s.get("Client"),
                "platform": s.get("Client"), "location": "LAN" if _private(s.get("RemoteEndPoint")) else "WAN",
                "method": method, "resolution": "Audio" if item.get("MediaType") == "Audio" else res_label(height),
                "progress_pct": round(pos * 100 / runtime) if runtime else None,
                "duration_min": round(runtime / TICKS / 60) if runtime else None,
                "state": "Paused" if ps.get("IsPaused") else "Playing"}

    async def poll(self, now: int | None = None) -> int:
        """One round of live tracking. Returns how many Emby streams are playing. A failed round keeps plays open."""
        if not self.enabled():
            return 0
        try:
            sessions = await self._get("/Sessions")
        except (httpx.HTTPError, ValueError) as e:
            self.error = str(e)
            log.warning("Emby sessions failed: %s", e)
            return 0
        self.error = None
        now = now or int(time.time())
        if not self.store.get_setting("emby_live_since"):
            self.store.set_setting("emby_live_since", str(now))
        seen = set()
        for s in sessions or []:
            r = self.row(s)
            if not r:
                continue
            key = r["session_key"]
            seen.add(key)
            r["user_id"] = self._person(r.pop("emby_user"), r.get("user"))
            act = self.active.get(key)
            if act and act["rating_key"] != r["rating_key"]:
                self.directory.stop_play(act["id"])
                act = None
            if act is None:
                existing = self.directory.open_play(SERVER, key, r["rating_key"])
                pid = existing["id"] if existing else self.directory.start_play(r, r.get("art"), now)
                act = {"id": pid, "rating_key": r["rating_key"], "last": now}
                self.active[key] = act
                add = 0
            else:
                add = min(now - act["last"], MAX_POLL_GAP) if r["state"] != "Paused" else 0
            self.directory.touch_play(act["id"], r, now, add)
            act["last"] = now
        for key in list(self.active):
            if key not in seen:
                self.directory.stop_play(self.active.pop(key)["id"])
        return len(seen)

    # ---------- backfill from Emby's activity log ----------
    async def import_history(self, page: int = 5000, max_entries: int = 500_000) -> dict:
        if not self.enabled():
            return {"added": 0, "skipped": 0, "errors": ["Emby is not set up."]}
        cutoff = int(self.store.get_setting("emby_live_since") or time.time())
        users = await self.auth.server_users()
        names = sorted(((u.get("Name") or "", u["Id"]) for u in users if u.get("Name")), key=lambda x: -len(x[0]))
        entries: list[dict] = []
        start = 0
        while start < max_entries:
            data = await self._get("/System/ActivityLog/Entries", {"Limit": page, "StartIndex": start})
            items = (data or {}).get("Items") or []
            if not items:
                break
            entries += [e for e in items if e.get("Type") in ("playback.start", "playback.stop")]
            start += len(items)
        entries.sort(key=lambda e: (e["Date"], e["Id"]))
        # Who: the activity log names the person at the start of its words ("<name> is playing …").
        open_: dict[tuple, int] = {}
        plays = []
        for e in entries:
            text = e.get("Name") or ""
            who = next(((n, uid) for n, uid in names if text.startswith(n + " ")), None)
            if not who or not e.get("ItemId"):
                continue
            at = _ts(e["Date"])
            k = (who[1], str(e["ItemId"]))
            if e["Type"] == "playback.start":
                open_[k] = at
                continue
            began = open_.pop(k, None)
            if began is not None and at - began > PAIR_SECONDS:
                began = None
            if at >= cutoff:
                continue
            m = re.search(r" has finished playing (.+?) on (.+)$", text)
            plays.append({"entry": e["Id"], "emby_user": who[1], "user": who[0], "item": str(e["ItemId"]),
                          "started": began or at, "stopped": at, "sec": (at - began) if began else 0,
                          "what": m[1] if m else "", "device": m[2] if m else None})
        # What: ask Emby about the items in hundreds; deleted ones keep the log's words.
        info: dict[str, dict] = {}
        ids = sorted({p["item"] for p in plays})
        for i in range(0, len(ids), 100):
            try:
                data = await self._get("/Items", {"Ids": ",".join(ids[i:i + 100]), "Recursive": "true",
                                                  "Fields": "ProviderIds,ProductionYear,RunTimeTicks,SeriesId,SeriesName,ParentIndexNumber,IndexNumber,Height"})
            except httpx.HTTPError as e:
                log.warning("Emby item lookup failed: %s", e)
                continue
            for it in (data or {}).get("Items") or []:
                info[str(it["Id"])] = it
        added = skipped = 0
        members: dict[str, str] = {}
        for p in plays:
            if p["emby_user"] not in members:
                members[p["emby_user"]] = self._person(p["emby_user"], p["user"])
            it = info.get(p["item"])
            d = describe(it) if it else from_text(p["what"])
            runtime = int((it or {}).get("RunTimeTicks") or 0)
            sec = min(p["sec"], int(runtime / TICKS * 1.5)) if runtime else p["sec"]
            rec = {**d, "server": SERVER, "session_key": f"emby-hist-{p['entry']}", "user_id": members[p["emby_user"]],
                   "username": p["user"], "rating_key": p["item"], "device": p["device"], "started_at": p["started"],
                   "stopped_at": p["stopped"], "watched_sec": max(0, sec),
                   "progress_pct": min(100, round(sec * 100 / (runtime / TICKS))) if runtime and sec else None,
                   "duration_min": round(runtime / TICKS / 60) if runtime else None,
                   "resolution": res_label((it or {}).get("Height"))}
            if self.directory.import_emby_play(rec):
                added += 1
            else:
                skipped += 1
        self.store.set_setting("emby_history_imported_at", str(int(time.time())))
        log.info("Emby history import: %d added, %d already there", added, skipped)
        return {"added": added, "skipped": skipped, "errors": []}
