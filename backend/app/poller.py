"""Background poller: live Plex sessions from every server, Netdata telemetry,
and one history sample per minute."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

from .billing import Billing, StripeClient
from .clips import VIDEO_KINDS, Clips
from .config import AppCfg, ServerCfg
from .netdata import NetdataClient
from .plex import PlexAccount, PlexClient, normalize_session
from .store import Directory, Store

log = logging.getLogger("telemetry.poller")


class Poller:
    def __init__(self, store: Store):
        self.store = store
        self.http = httpx.AsyncClient(follow_redirects=True)
        self.cfg: AppCfg = store.app_config()
        self.clients: dict[int, PlexClient] = {}
        self.streams: list[dict] = []
        self.server_state: dict[str, dict] = {}
        self.telemetry: dict[str, dict] = {}
        self.netdata_error: str | None = None
        self.netdata_ok_at: float | None = None
        self.art: dict[tuple[int, str], str] = {}
        self.last_poll: float | None = None
        self._last_minute = 0
        self._wake = asyncio.Event()         # settings changed: poll Plex now
        self._tel_wake = asyncio.Event()     # refresh Netdata now
        self._upkeep_wake = asyncio.Event()  # run the account syncs now
        self._source_cache: dict[tuple[int, str], dict] = {}  # (server id, ratingKey) -> source metadata
        # Looping card clips, cut from each title's own file (app/clips.py); kept next to the database.
        self.clips = Clips(os.environ.get("CLIPS_DIR") or Path(getattr(store, "path", "data/x.db")).parent / "clips")
        self.directory = Directory(store)
        self.active_plays: dict[tuple[str, str], dict] = {}
        # Remembered across restarts, so a restart does not redo a full plex.tv and Stripe copy.
        self.users_synced_at: float = self._saved_time("users_synced_at")
        self.users_sync_error: str | None = None
        self.client_id: str = "greystar-telemetry"
        self.owner_by_server: dict[str, str] = {}
        self.billing = Billing(store)
        self.billing_synced_at: float = self._saved_time("billing_synced_at")
        self.billing_error: str | None = None
        self.billing_counts: dict = {}
        self._load_owners()
        self.directory.close_stale_plays()

    def _saved_time(self, key: str) -> float:
        try:
            return float(self.store.get_setting(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def kick(self) -> None:
        """Run the account syncs (plex.tv users, Stripe) now, in the background."""
        self._upkeep_wake.set()

    # ---------- config ----------
    def _load_owners(self) -> None:
        self.owner_by_server = {s["name"]: s["owner_plex_id"] for s in self.store.list_servers() if s.get("owner_plex_id")}

    def owner_for(self, server: str) -> str | None:
        """plex.tv id of the account that owns this server (Plex reports its owner as local account 1)."""
        oid = self.owner_by_server.get(server)
        if oid:
            return oid
        only = self.directory.owner()
        return only["plex_id"] if only else None

    def reload(self) -> None:
        """Called after any Settings change."""
        self.cfg = self.store.app_config()
        self._load_owners()
        live = {s.id for s in self.cfg.servers}
        self.clients = {k: v for k, v in self.clients.items() if k in live}
        names = {s.name for s in self.cfg.servers}
        self.server_state = {k: v for k, v in self.server_state.items() if k in names}
        self._wake.set()
        self._tel_wake.set()

    def client(self, s: ServerCfg) -> PlexClient:
        c = self.clients.get(s.id)
        if c is None or c.server != s:
            c = PlexClient(s, self.http)
            self.clients[s.id] = c
        return c

    # ---------- polling ----------
    async def _poll_server(self, s: ServerCfg) -> list[dict]:
        c = self.client(s)
        try:
            items = await c.sessions()

            async def source_for(it: dict) -> dict:
                # Transcodes report the stream, not the file: ask once per title for the source details.
                if not (it.get("TranscodeSession") and it.get("ratingKey")):
                    return it
                key = (s.id, str(it["ratingKey"]))
                if key not in self._source_cache:
                    try:
                        self._source_cache[key] = await c.metadata(key[1])
                    except Exception as e:  # source details are a nice-to-have
                        log.debug("metadata failed: %s", e)
                        return it
                    if len(self._source_cache) > 2000:
                        self._source_cache.pop(next(iter(self._source_cache)))
                return self._source_cache[key]

            sources = await asyncio.gather(*(source_for(it) for it in items))
            rows = []
            for it, source in zip(items, sources):
                row = normalize_session(s, it, source)
                row["session_key"] = str(it.get("sessionKey") or (it.get("Session") or {}).get("id") or "")
                art = row.pop("_art", None)
                row["_art_path"] = art
                if art and row["rating_key"]:
                    self.art[(s.id, row["rating_key"])] = art
                    row["poster"] = f"/api/poster/{s.id}/{row['rating_key']}"
                row["clip"] = None
                if row.get("kind") in VIDEO_KINDS and row.get("rating_key"):
                    if self.clips.ready(s.id, row["rating_key"]):
                        row["clip"] = self.clips.url(s.id, row["rating_key"])
                    else:
                        self.clips.want(s.id, row["rating_key"])  # made in the background; shows from the next poll
                rows.append(row)
            self.server_state[s.name] = {"online": True, "error": None, "ok_at": time.time()}
            return rows
        except Exception as e:
            prev = self.server_state.get(s.name, {})
            self.server_state[s.name] = {"online": False, "error": str(e), "ok_at": prev.get("ok_at")}
            log.warning("Plex poll failed for %s: %s", s.name, e)
            return []

    async def poll_plex(self) -> None:
        servers = [s for s in self.cfg.servers if s.enabled]
        results = await asyncio.gather(*(self._poll_server(s) for s in servers))
        rows = [r for rs in results for r in rs]
        online = {name for name, st in self.server_state.items() if st.get("online")}
        try:
            self.track_plays(rows, online)
        except Exception:
            log.exception("history tracking failed")
        for r in rows:
            r.pop("_art_path", None)
        self.streams = rows
        self.last_poll = time.time()

    def track_plays(self, rows: list[dict], online_servers: set[str]) -> None:
        """Open, extend and close playback history rows from consecutive polls."""
        now = int(time.time())
        seen: set[tuple[str, str]] = set()
        for r in rows:
            if not r.get("session_key"):
                continue
            key = (r["server"], r["session_key"])
            seen.add(key)
            if r.get("user_id") == "1":
                # Plex reports the server owner as local account 1, not their plex.tv id.
                r["user_id"] = self.owner_for(r["server"])
            if r.get("user_id"):
                self.directory.saw_user(r["user_id"], r.get("user"), r.get("user_thumb"))
                self.directory.add_user_server(r["user_id"], r["server"])
            act = self.active_plays.get(key)
            if act and act["rating_key"] != r.get("rating_key"):
                self.directory.stop_play(act["id"])
                act = None
            if act is None:
                existing = self.directory.open_play(r["server"], r["session_key"], r.get("rating_key", ""))
                pid = existing["id"] if existing else self.directory.start_play(r, r.get("_art_path"), now)
                act = {"id": pid, "rating_key": r.get("rating_key"), "last": now}
                self.active_plays[key] = act
                add = 0
            else:
                add = min(now - act["last"], 3 * self.cfg.poll_seconds) if r["state"] != "Paused" else 0
            self.directory.touch_play(act["id"], r, now, add)
            act["last"] = now
        for key in list(self.active_plays):
            # A server that failed to answer this round keeps its plays open.
            if key not in seen and key[0] in online_servers:
                self.directory.stop_play(self.active_plays.pop(key)["id"])

    async def sync_billing(self) -> dict:
        """Copy Stripe customers, subscriptions and recent payments (read-only key from Settings, Billing)."""
        key = self.store.get_setting("stripe_key")
        if not key:
            self.billing_error = None
            return {}
        try:
            snap = await StripeClient(key, self.http).snapshot()
            self.billing_counts = self.billing.save_snapshot(snap)
            self.billing_error = None
        except Exception as e:
            self.billing_error = str(e)
            log.warning("Stripe sync failed: %s", e)
            raise
        finally:
            self.billing_synced_at = time.time()
            self.store.set_setting("billing_synced_at", str(self.billing_synced_at))
        return self.billing_counts

    async def migrate_legacy_account(self) -> None:
        """Older versions kept one Plex account in the settings table; move it to plex_accounts."""
        token = self.store.get_setting("plex_account_token")
        if not token:
            return
        if not self.store.list_accounts():
            info = await PlexAccount(self.http, self.client_id).user_info(token)
            self.store.save_account(info, token)
        self.store.set_setting("plex_account_token", None)

    async def sync_users(self) -> int:
        """For every linked Plex account: which monitored servers it owns, and everyone it shares
        them with (names and emails). One person shared by several owners gets all those servers."""
        accounts = self.store.account_tokens()
        if not accounts:
            self.users_sync_error = "Link the Plex account of each server under Settings, Plex servers, to load the people they share with."
            return 0
        acct = PlexAccount(self.http, self.client_id)
        mine = self.store.list_servers()
        by_mid = {s["machine_id"]: s for s in mine if s.get("machine_id")}
        by_name = {s["name"].lower(): s["name"] for s in mine}
        people: dict[str, dict] = {}
        owners: set[str] = set()
        errors = []
        for a, token in accounts:
            try:
                info = await acct.user_info(token)
                self.store.update_account_info(info)
                owned = [r for r in await acct.servers(token) if r.get("owned")]
                owned_names = []
                for r in owned:
                    s = by_mid.get(r["machine_id"])
                    if s:
                        owned_names.append(s["name"])
                        if s.get("owner_plex_id") != str(info["id"]):
                            self.store.update_server(s["id"], {"owner_plex_id": str(info["id"])})
                if owned_names:
                    owners.add(str(info["id"]))
                people.setdefault(str(info["id"]), {**info, "servers": set()})["servers"] |= set(owned_names)
                for u in await acct.shared_users(token):
                    on = {by_mid[m]["name"] for m in u.pop("server_ids", []) if m in by_mid}
                    on |= {by_name[n.lower()] for n in u.pop("server_names", []) if n.lower() in by_name}
                    p = people.setdefault(str(u["id"]), {**u, "servers": set()})
                    p["servers"] |= on
            except Exception as e:  # one account failing should not stop the others
                errors.append(f"{a.get('email') or a.get('username') or a['plex_id']}: {e}")
                log.warning("User sync failed for one Plex account: %s", e)
        for pid, p in people.items():
            if errors:  # an account did not answer: keep the servers we knew instead of dropping them
                old = self.directory.get_user(pid)
                if old and old.get("servers"):
                    p["servers"] |= set(json.loads(old["servers"]))
            self.directory.upsert_user({**p, "servers": sorted(p["servers"])}, shared=True, is_owner=(pid in owners) or None if errors else pid in owners)
        self._load_owners()
        self.users_synced_at = time.time()
        self.store.set_setting("users_synced_at", str(self.users_synced_at))
        self.users_sync_error = "; ".join(errors) or None
        return len(people)

    async def import_history(self, size: int = 1000) -> dict:
        """Backfill playback history from each server's own watch history."""
        added = skipped = 0
        errors = []
        for s in [x for x in self.cfg.servers if x.enabled]:
            c = self.client(s)
            try:
                accounts = await c.accounts()
                items = await c.history(size)
            except Exception as e:
                errors.append(f"{s.name}: {e}")
                continue
            for it in items:
                account = str(it.get("accountID", ""))
                name = accounts.get(account, "")
                user = self.directory.get_user(account) if account and account != "1" else None
                if account == "1":
                    oid = self.owner_for(s.name)
                    user = self.directory.get_user(oid) if oid else None
                if user is None and name:
                    m = self.directory.db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE OR title = ? COLLATE NOCASE", (name, name)).fetchone()
                    user = dict(m) if m else None
                typ = it.get("type")
                if typ == "episode":
                    title, sub = it.get("grandparentTitle") or it.get("title"), f"S{it.get('parentIndex', '?')} \u00b7 E{it.get('index', '?')} \u00b7 {it.get('title', '')}"
                    art, kind = it.get("grandparentThumb") or it.get("thumb"), "Episode"
                elif typ == "track":
                    title, sub, art, kind = it.get("grandparentTitle") or it.get("title"), it.get("title"), it.get("parentThumb") or it.get("thumb"), "Audio"
                else:
                    title, sub, art, kind = it.get("title"), str(it.get("year") or ""), it.get("thumb"), "Movie"
                ok = self.directory.import_play({
                    "server": s.name, "rating_key": str(it.get("ratingKey") or it.get("key", "")), "account": account,
                    "user_id": user["plex_id"] if user else None, "username": (user or {}).get("title") or name or None,
                    "title": title, "subtitle": sub, "kind": kind, "art": art, "viewed_at": int(it.get("viewedAt") or 0),
                })
                added += int(ok)
                if user:
                    self.directory.add_user_server(user["plex_id"], s.name)
                skipped += int(not ok)
        return {"added": added, "already_had": skipped, "errors": errors}

    async def poll_netdata(self) -> None:
        nd = NetdataClient(self.cfg, self.http)
        if not nd.enabled:
            self.telemetry, self.netdata_error = {}, None
            return
        try:
            self.telemetry = await nd.snapshot()
            self.netdata_error, self.netdata_ok_at = None, time.time()
        except Exception as e:
            self.netdata_error = str(e)
            log.warning("Netdata poll failed: %s", e)

    def record_minute(self) -> None:
        minute = int(time.time()) // 60 * 60
        if minute == self._last_minute:
            return
        self._last_minute = minute
        rows = []
        for s in self.cfg.servers:
            if not s.enabled:
                continue
            mine = [r for r in self.streams if r["server"] == s.name]
            t = self.telemetry.get(s.name, {})
            rows.append({
                "server": s.name,
                "online": self.server_state.get(s.name, {}).get("online", False),
                "streams": len(mine),
                "bandwidth_mbps": round(sum(r["bitrate_mbps"] for r in mine if r["state"] != "Paused"), 2),
                "cpu_pct": t.get("cpu_pct"),
                "net_out_mbps": t.get("net_out_mbps"),
            })
        if rows:
            self.store.write_minute(rows, minute)
        if minute % 3600 == 0:
            self.store.prune(self.cfg.history_days)

    async def run(self) -> None:
        """Live streams every poll_seconds. Netdata and the slow account syncs (plex.tv users,
        Stripe) run in loops of their own, so neither can hold up the streams, at startup or later."""
        self.clips.prune()
        side = [asyncio.create_task(self._telemetry_loop()), asyncio.create_task(self._upkeep_loop()),
                asyncio.create_task(self.clips.worker(self._clip_source))]
        try:
            while True:
                try:
                    if not self.cfg.use_sample:
                        await self.poll_plex()
                        self.record_minute()
                except Exception:
                    log.exception("poll cycle failed")
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            for t in side:
                t.cancel()

    async def _telemetry_loop(self) -> None:
        while True:
            try:
                if not self.cfg.use_sample:
                    await self.poll_netdata()
            except Exception:
                log.exception("Netdata cycle failed")
            self._tel_wake.clear()
            try:
                await asyncio.wait_for(self._tel_wake.wait(), timeout=self.cfg.telemetry_seconds)
            except asyncio.TimeoutError:
                pass

    async def _upkeep_loop(self) -> None:
        await asyncio.sleep(2)  # let the first Plex poll go out first
        while True:
            try:
                await self.upkeep()
            except Exception:
                log.exception("background sync failed")
            self._upkeep_wake.clear()
            try:
                await asyncio.wait_for(self._upkeep_wake.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def _clip_source(self, server_id: int, rating_key: str) -> tuple[str, str, float] | None:
        """Where a title's file lives on its Plex server, for the clip maker: (file url, token, duration in seconds)."""
        s = next((x for x in self.cfg.servers if x.id == server_id and x.enabled), None)
        if not s or not s.token:
            return None
        meta = await self.client(s).metadata(str(rating_key))
        media = (meta.get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        key = str(part.get("key") or "")
        if not key.startswith("/library/parts/"):
            return None
        duration = float(meta.get("duration") or media.get("duration") or part.get("duration") or 0) / 1000
        return s.plex_url.rstrip("/") + key, s.token, duration

    async def upkeep(self) -> None:
        """Account syncs that only need to run now and then: plex.tv users every 6 hours, Stripe every 15 minutes."""
        if self.store.get_setting("plex_account_token"):
            try:
                await self.migrate_legacy_account()
            except Exception as e:
                log.warning("Could not move the linked Plex account: %s", e)
        if time.time() - self.users_synced_at > 6 * 3600 and self.store.list_accounts():
            try:
                await self.sync_users()
            except Exception as e:
                self.users_sync_error = str(e)
                self.users_synced_at = time.time() - 5 * 3600  # retry in an hour
                log.warning("User sync failed: %s", e)
        if time.time() - self.billing_synced_at > 15 * 60 and self.store.get_setting("stripe_key"):
            try:
                await self.sync_billing()
            except Exception:
                pass  # recorded in billing_error, shown in Settings

    # ---------- views ----------
    def servers_view(self) -> list[dict]:
        out = []
        for s in self.cfg.servers:
            if not s.enabled:
                continue
            st = self.server_state.get(s.name, {})
            t = self.telemetry.get(s.name, {})
            cpu = t.get("cpu_pct")
            online = st.get("online", False)
            if not online:
                status = "Offline"
            elif cpu is not None and cpu >= self.cfg.hot_cpu_pct:
                status = "Degraded"
            else:
                status = "Healthy"
            out.append({
                "server": s.name,
                "capacity_mbps": s.capacity_mbps,
                "max_streams": s.max_streams,
                "cpu_pct": cpu if cpu is not None else 0,
                "cpu_known": cpu is not None,
                "net_out_mbps": t.get("net_out_mbps"),
                "status": status,
                "hot_for_min": self.store.hot_minutes(s.name, self.cfg.hot_cpu_pct) if online else 0,
                "error": st.get("error"),
            })
        return out
