"""Unwatched cleanup (Glenn's ask, Nov 2026; admin board is his pick B).

Movies and shows requested through this site, after the person ticked "I understand it will be removed if it is not
watched within 14 days", are tracked here (`cleanup_items`, one row per request). Books, comics and audiobooks never.

- The clock starts when the title ARRIVES on Plex (Seerr says available or partly available), not when it is asked for.
- One real play by anyone, of the movie or of any episode of the requested seasons, keeps it for good ("watched").
  A play counts when it lasted `cleanup_min_watch` seconds (default 120) or came from Plex's own history.
- A title nobody played is removed after `cleanup_days` (14) at `cleanup_hour` (3:00 AM, `cleanup_tz`): through Radarr
  or Sonarr with its files (so it is not downloaded again), then cleared in Seerr so anyone can request it again.
- Mode `cleanup_mode`: review (default: nothing is deleted, it only shows what would be), auto, or off (no tick box,
  nothing tracked).
- Admins can Keep (forever), add 7 days, Delete now, or Track again (a fresh clock). Members see days left, never who
  asked (`clock_for`).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time
from typing import Any

from .arr import Arr, ArrError
from .seerr import MEDIA_STATUS, REQUEST_STATUS, SeerrError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

log = logging.getLogger("telemetry.cleanup")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cleanup_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER UNIQUE,
  media_id INTEGER,
  kind TEXT NOT NULL,
  tmdb_id INTEGER,
  tvdb_id INTEGER,
  is4k INTEGER NOT NULL DEFAULT 0,
  title TEXT,
  year TEXT,
  poster TEXT,
  seasons TEXT,
  requested_by TEXT,
  requested_name TEXT,
  requested_at INTEGER NOT NULL,
  agreed_at INTEGER,
  rating_key TEXT,
  available_at INTEGER,
  deadline INTEGER,
  extra_days INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'waiting',
  watched_at INTEGER,
  plays INTEGER NOT NULL DEFAULT 0,
  size_bytes INTEGER,
  size_at INTEGER,
  kept_by TEXT,
  kept_note TEXT,
  kept_at INTEGER,
  would_delete_at INTEGER,
  deleted_at INTEGER,
  deleted_by TEXT,
  freed_bytes INTEGER,
  error TEXT,
  checked_at INTEGER
);
CREATE INDEX IF NOT EXISTS cleanup_title ON cleanup_items (kind, tmdb_id);
CREATE INDEX IF NOT EXISTS cleanup_state ON cleanup_items (state);
"""

OPEN = ("waiting", "running")
DAY = 86400


def norm(t: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(t or "").lower())


def iso(v: Any) -> int | None:
    if not v:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def season_of(play: dict) -> int | None:
    if play.get("season") is not None:
        return int(play["season"])
    m = re.match(r"S(\d+)", str(play.get("subtitle") or ""))
    return int(m.group(1)) if m else None


class Cleanup:
    def __init__(self, store, seerr, arr: Arr, poller=None):
        self.store = store
        self.seerr = seerr
        self.arr = arr
        self.poller = poller
        self._busy = asyncio.Lock()
        self.last_error: str | None = None
        with store.lock:
            store.db.executescript(SCHEMA)
            store.db.commit()

    # ---------- settings ----------
    def mode(self) -> str:
        m = self.store.get_setting("cleanup_mode") or "review"
        return m if m in ("off", "review", "auto") else "review"

    def enabled(self) -> bool:
        return self.mode() != "off"

    def days(self) -> int:
        return max(1, min(90, int(self.store.get_setting("cleanup_days") or 14)))

    def hour(self) -> int:
        return max(0, min(23, int(self.store.get_setting("cleanup_hour") or 3)))

    def min_watch(self) -> int:
        return max(0, int(self.store.get_setting("cleanup_min_watch") or 120))

    def tz(self):
        name = self.store.get_setting("cleanup_tz") or "America/Chicago"
        try:
            return ZoneInfo(name) if ZoneInfo else dt.timezone(dt.timedelta(hours=-6))
        except Exception:  # noqa: BLE001  (unknown zone, or no tzdata)
            return dt.timezone(dt.timedelta(hours=-6))

    def settings(self) -> dict:
        return {"mode": self.mode(), "days": self.days(), "hour": self.hour(), "tz": self.store.get_setting("cleanup_tz") or "America/Chicago",
                "minWatch": self.min_watch(), "arr": self.arr.configured(), "radarr": self.arr.configured("radarr"),
                "sonarr": self.arr.configured("sonarr"), "lastRun": self.store.get_setting("cleanup_last_run"), "error": self.last_error}

    def set_mode(self, mode: str) -> None:
        if mode not in ("off", "review", "auto"):
            raise ValueError("Pick off, review or auto.")
        self.store.set_setting("cleanup_mode", mode)

    # ---------- rows ----------
    def _rows(self, where: str = "1", args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.store.db.execute(f"SELECT * FROM cleanup_items WHERE {where} ORDER BY id DESC", args)]

    def _get(self, item_id: int) -> dict:
        rows = self._rows("id = ?", (int(item_id),))
        if not rows:
            raise KeyError(item_id)
        return rows[0]

    def _set(self, item_id: int, **cols) -> None:
        if not cols:
            return
        with self.store.lock:
            self.store.db.execute(f"UPDATE cleanup_items SET {', '.join(k + ' = ?' for k in cols)} WHERE id = ?", (*cols.values(), int(item_id)))
            self.store.db.commit()

    def track(self, *, request_id: int, kind: str, tmdb_id: int, is4k: bool, seasons: list[int] | None, info: dict,
              plex_id: str | None, name: str | None) -> None:
        """A new request, made after the person ticked the box. Kind movie or tv; nothing else is tracked."""
        if kind not in ("movie", "tv") or not self.enabled():
            return
        now = int(time.time())
        with self.store.lock:
            self.store.db.execute(
                """INSERT OR IGNORE INTO cleanup_items (request_id, kind, tmdb_id, is4k, title, year, poster, seasons, requested_by,
                   requested_name, requested_at, agreed_at, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'waiting')""",
                (int(request_id), kind, int(tmdb_id), 1 if is4k else 0, info.get("title"), str(info.get("year") or "") or None,
                 info.get("poster"), json.dumps(sorted(int(s) for s in seasons or [])), plex_id, name, now, now))
            self.store.db.commit()

    # ---------- when it goes ----------
    def delete_at(self, deadline: int) -> int:
        """The nightly run that removes it: the first cleanup_hour at or after the deadline, local time."""
        tz = self.tz()
        d = dt.datetime.fromtimestamp(deadline, tz)
        run = d.replace(hour=self.hour(), minute=0, second=0, microsecond=0)
        if run.timestamp() < deadline:
            run += dt.timedelta(days=1)
        return int(run.timestamp())

    def phase(self, row: dict, now: float | None = None) -> dict:
        """Where a row stands: state plus days left and the removal time, for admins and members alike."""
        now = now or time.time()
        st = row["state"]
        out = {"state": st, "daysLeft": None, "deleteAt": None}
        if st == "running" and row.get("deadline"):
            at = self.delete_at(int(row["deadline"]))
            left = at - now
            out.update(deleteAt=at, daysLeft=max(0, int(-(-left // DAY))))
            out["state"] = "due" if left <= 3 * DAY else "running"
        return out

    # ---------- watch check ----------
    def plays_of(self, row: dict, since: int) -> list[dict]:
        """Plays by anyone, on any server, of this title (the requested seasons of a show) since `since`."""
        kind = "Movie" if row["kind"] == "movie" else "Episode"
        cur = self.store.db.execute(
            "SELECT title, subtitle, kind, rating_key, parent_key, season, tmdb_id, watched_sec, source, started_at FROM plays "
            "WHERE kind = ? AND started_at >= ?", (kind, int(since)))
        want_title = norm(row.get("title"))
        seasons = set(json.loads(row.get("seasons") or "[]"))
        rk = str(row.get("rating_key") or "")
        out = []
        for p in map(dict, cur):
            if kind == "Movie":
                hit = (row.get("tmdb_id") and p.get("tmdb_id") == row["tmdb_id"]) or (rk and p.get("rating_key") == rk) or \
                      (want_title and norm(p.get("title")) == want_title and (not row.get("year") or not p.get("subtitle") or str(p["subtitle"]) == str(row["year"])))
            else:
                hit = (rk and p.get("parent_key") == rk) or (want_title and norm(p.get("title")) == want_title)
                sn = season_of(p)
                if hit and seasons and sn is not None and sn not in seasons:
                    hit = False
            if hit:
                out.append(p)
        return out

    def watched(self, row: dict, since: int) -> list[dict]:
        need = self.min_watch()
        return [p for p in self.plays_of(row, since) if p.get("source") == "plex" or int(p.get("watched_sec") or 0) >= need]

    def streaming_now(self, row: dict) -> bool:
        streams = getattr(self.poller, "streams", None) or []
        kind = "Movie" if row["kind"] == "movie" else "Episode"
        t = norm(row.get("title"))
        return any(s.get("kind") == kind and norm(s.get("title")) == t for s in streams)

    # ---------- keeping rows up to date ----------
    async def refresh(self) -> None:
        """Arrival, plays and size for every open row. Runs every 10 minutes and when the board opens."""
        rows = self._rows("state IN ('waiting', 'running')")
        if not rows or not self.seerr.configured():
            return
        try:
            every = await self.seerr.all_requests()
        except SeerrError as e:
            self.last_error = "Seerr: " + e.message
            return
        now = int(time.time())
        for row in rows:
            q = every.get(int(row["request_id"])) if row.get("request_id") is not None else None
            if q is None or REQUEST_STATUS.get(q.get("status")) == "declined":
                if every:  # the request is gone from Seerr (removed, or declined): nothing to track
                    self._set(row["id"], state="gone", checked_at=now)
                continue
            m = q.get("media") or {}
            is4k = bool(row["is4k"])
            status = MEDIA_STATUS.get(m.get("status4k" if is4k else "status"), "unknown")
            cols: dict[str, Any] = {"media_id": m.get("id"), "tvdb_id": m.get("tvdbId") or row.get("tvdb_id"),
                                    "rating_key": m.get("ratingKey4k" if is4k else "ratingKey") or row.get("rating_key"), "checked_at": now}
            arrived = status in ("available", "partial")
            if row["kind"] == "tv" and arrived:
                want = set(json.loads(row.get("seasons") or "[]"))
                ss = [s for s in m.get("seasons") or [] if not want or s.get("seasonNumber") in want]
                if ss:
                    arrived = any(MEDIA_STATUS.get(s.get("status4k" if is4k else "status")) in ("available", "partial") for s in ss)
            if row["state"] == "waiting" and arrived:
                added = iso(m.get("mediaAddedAt"))
                start = added if added and added >= int(row["requested_at"]) else now
                cols.update(state="running", available_at=start, deadline=start + (self.days() + int(row["extra_days"] or 0)) * DAY)
                row.update(cols)
            if row["state"] == "running" or cols.get("state") == "running":
                seen = self.watched({**row, **cols}, int(cols.get("available_at") or row["available_at"] or row["requested_at"]))
                if seen:
                    cols.update(state="watched", watched_at=min(int(p["started_at"]) for p in seen), plays=len(seen))
                if not row.get("size_at") or now - int(row["size_at"]) > 6 * 3600:
                    size = await self.arr.size_of(row["kind"], row.get("tmdb_id"), cols.get("tvdb_id"), json.loads(row.get("seasons") or "[]"), is4k)
                    if size is not None:
                        cols.update(size_bytes=size, size_at=now)
            self._set(row["id"], **cols)
        # Watched titles keep counting plays (shown to admins), cheaply.
        for row in self._rows("state = 'watched' AND (checked_at IS NULL OR checked_at < ?)", (now - 3600,)):
            n = len(self.watched(row, int(row["available_at"] or row["requested_at"])))
            self._set(row["id"], plays=max(n, int(row["plays"] or 0)), checked_at=now)
        self.last_error = None

    async def nightly(self) -> list[dict]:
        """At cleanup_hour (or the first run after it that day): remove what ran out, or in review mode note it."""
        tz = self.tz()
        local = dt.datetime.now(tz)
        today = local.strftime("%Y-%m-%d")
        if local.hour < self.hour() or self.store.get_setting("cleanup_last_run") == today:
            return []
        done = await self.run_due(by="Nightly cleanup")
        self.store.set_setting("cleanup_last_run", today)
        return done

    async def run_due(self, by: str) -> list[dict]:
        await self.refresh()
        now = time.time()
        done = []
        for row in self._rows("state = 'running'"):
            ph = self.phase(row, now)
            if not ph["deleteAt"] or ph["deleteAt"] > now + 60:
                continue
            if self.streaming_now(row):
                continue  # someone is watching it right now: it will count as watched
            if self.mode() == "auto":
                done.append(await self.delete(row["id"], by))
            elif not row.get("would_delete_at"):
                self._set(row["id"], would_delete_at=int(now))
                done.append({"id": row["id"], "review": True})
        return done

    async def delete(self, item_id: int, by: str) -> dict:
        row = self._get(item_id)
        if row["state"] == "deleted":
            return {"id": item_id, "ok": True}
        is4k = bool(row["is4k"])
        try:
            if row["kind"] == "movie":
                freed, whole = await self.arr.delete_movie(int(row["tmdb_id"]), is4k), True
            else:
                if not row.get("tvdb_id"):
                    raise ArrError(409, "Seerr has no TVDB id for this show yet, so Sonarr cannot find it.")
                freed, whole = await self.arr.delete_seasons(int(row["tvdb_id"]), json.loads(row.get("seasons") or "[]"), is4k)
        except ArrError as e:
            self._set(item_id, error=e.message)
            return {"id": item_id, "ok": False, "error": e.message}
        if whole and row.get("media_id") and self.seerr.configured():
            try:  # so it shows as not on Plex and anyone can request it again
                await self.seerr.send("DELETE", f"/media/{int(row['media_id'])}")
            except SeerrError as e:
                log.info("Cleanup: Seerr did not clear media %s: %s", row["media_id"], e.message)
        self._set(item_id, state="deleted", deleted_at=int(time.time()), deleted_by=by, freed_bytes=int(freed or 0), error=None)
        log.info("Cleanup: removed %s (%s) by %s, %s bytes", row["title"], row["kind"], by, freed)
        return {"id": item_id, "ok": True, "freed": freed}

    # ---------- admin actions ----------
    def keep(self, item_id: int, by: str, note: str | None) -> None:
        self._get(item_id)
        self._set(item_id, state="kept", kept_by=by, kept_note=(note or "").strip()[:200] or None, kept_at=int(time.time()), error=None)

    def extend(self, item_id: int, days: int = 7) -> None:
        row = self._get(item_id)
        if row["state"] not in OPEN:
            raise ValueError("Only titles on the clock can get more days.")
        days = max(1, min(60, int(days)))
        cols = {"extra_days": int(row["extra_days"] or 0) + days, "would_delete_at": None}
        if row.get("deadline"):  # an overdue one gets the days from now, not from a deadline already past
            cols["deadline"] = max(int(row["deadline"]), int(time.time())) + days * DAY
        self._set(item_id, **cols)

    def track_again(self, item_id: int) -> None:
        """Back on the clock with a fresh 14 days from now (a kept or watched title)."""
        row = self._get(item_id)
        if row["state"] not in ("kept", "watched"):
            raise ValueError("Only kept or watched titles can be tracked again.")
        now = int(time.time())
        if row.get("available_at"):
            self._set(item_id, state="running", available_at=now, deadline=now + self.days() * DAY, extra_days=0, kept_by=None,
                      kept_note=None, kept_at=None, would_delete_at=None, watched_at=None, plays=0)
        else:
            self._set(item_id, state="waiting", kept_by=None, kept_note=None, kept_at=None)

    # ---------- views ----------
    def admin_item(self, row: dict, now: float) -> dict:
        ph = self.phase(row, now)
        seasons = json.loads(row.get("seasons") or "[]")
        return {"id": row["id"], "requestId": row["request_id"], "kind": row["kind"], "tmdbId": row["tmdb_id"], "title": row["title"],
                "year": row["year"], "poster": row["poster"], "is4k": bool(row["is4k"]), "seasons": seasons,
                "requestedBy": row["requested_name"], "requestedAt": row["requested_at"], "availableAt": row["available_at"],
                "state": ph["state"], "daysLeft": ph["daysLeft"], "deleteAt": ph["deleteAt"], "watchedAt": row["watched_at"],
                "plays": row["plays"], "size": row["size_bytes"], "keptBy": row["kept_by"], "keptNote": row["kept_note"],
                "keptAt": row["kept_at"], "wouldDeleteAt": row["would_delete_at"], "deletedAt": row["deleted_at"],
                "deletedBy": row["deleted_by"], "freed": row["freed_bytes"], "error": row["error"],
                "open": f"#/requests/{'series' if row['kind'] == 'tv' else 'movie'}/{row['tmdb_id']}"}

    def board(self) -> dict:
        now = time.time()
        month = now - 30 * DAY
        items = [self.admin_item(r, now) for r in self._rows("state != 'gone'")]
        items = [x for x in items if x["state"] != "deleted" or (x["deletedAt"] or 0) >= month]
        cols = {"running": [], "due": [], "kept": [], "deleted": []}
        for x in items:
            col = {"waiting": "running", "running": "running", "due": "due", "watched": "kept", "kept": "kept", "deleted": "deleted"}[x["state"]]
            cols[col].append(x)
        cols["running"].sort(key=lambda x: (x["deleteAt"] is None, x["deleteAt"] or 0, x["requestedAt"]))
        cols["due"].sort(key=lambda x: x["deleteAt"] or 0)
        cols["kept"].sort(key=lambda x: -(x["keptAt"] or x["watchedAt"] or 0))
        cols["deleted"].sort(key=lambda x: -(x["deletedAt"] or 0))
        review_hits = [x for x in items if x["wouldDeleteAt"] and x["state"] in ("running", "due")]
        return {"settings": self.settings(), "columns": cols,
                "totals": {"running": len(cols["running"]), "due": len(cols["due"]), "kept": len(cols["kept"]), "deleted": len(cols["deleted"]),
                           "dueBytes": sum(x["size"] or 0 for x in cols["due"]), "freedBytes": sum(x["freed"] or 0 for x in cols["deleted"]),
                           "wouldHave": len(review_hits), "wouldHaveBytes": sum(x["size"] or 0 for x in review_hits)}}

    def due_count(self) -> int:
        now = time.time()
        return sum(1 for r in self._rows("state = 'running'") if self.phase(r, now)["state"] == "due")

    def clock_for(self, kind: str, tmdb_id: int) -> dict | None:
        """What members see for a title (newest tracked request of it): never who asked."""
        kind = "tv" if kind in ("tv", "show", "series") else kind
        rows = self._rows("kind = ? AND tmdb_id = ? AND state != 'gone'", (kind, int(tmdb_id)))
        if not rows:
            return None
        row = rows[0]
        ph = self.phase(row)
        st = ph["state"]
        if st == "waiting":
            word, shape = "Clock starts on arrival", "square"
        elif st in ("running", "due"):
            d = ph["daysLeft"] or 0
            last = ph["deleteAt"] and ph["deleteAt"] - time.time() <= DAY
            word, shape = ("Last day to watch" if last else f"{d} days left to watch" if d != 1 else "1 day left to watch"), ("triangle" if last else "ring")
            st = "last" if last else st
        elif st == "watched":
            word, shape = "Watched, it stays", "circle"
        elif st == "kept":
            word, shape = "Kept", "circle"
        else:
            word, shape = "Removed, not watched", "square"
        return {"state": st, "word": word, "shape": shape, "daysLeft": ph["daysLeft"], "deleteAt": ph["deleteAt"],
                "availableAt": row["available_at"], "deletedAt": row["deleted_at"], "days": self.days(),
                "seasons": json.loads(row.get("seasons") or "[]")}

    async def loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                if self.enabled():
                    async with self._busy:
                        await self.refresh()
                        await self.nightly()
            except Exception:  # noqa: BLE001
                log.exception("Unwatched cleanup run failed")
            await asyncio.sleep(600)
