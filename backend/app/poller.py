"""Background poller: live Plex sessions from every server, Netdata telemetry,
and one history sample per minute."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import AppCfg, ServerCfg
from .netdata import NetdataClient
from .plex import PlexClient, normalize_session
from .store import Store

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
        self._wake = asyncio.Event()

    # ---------- config ----------
    def reload(self) -> None:
        """Called after any Settings change."""
        self.cfg = self.store.app_config()
        live = {s.id for s in self.cfg.servers}
        self.clients = {k: v for k, v in self.clients.items() if k in live}
        names = {s.name for s in self.cfg.servers}
        self.server_state = {k: v for k, v in self.server_state.items() if k in names}
        self._wake.set()

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
            rows = []
            for it in items:
                source = it
                if it.get("TranscodeSession") and it.get("ratingKey"):
                    try:
                        source = await c.metadata(str(it["ratingKey"]))
                    except Exception as e:  # source details are a nice-to-have
                        log.debug("metadata failed: %s", e)
                row = normalize_session(s, it, source)
                art = row.pop("_art", None)
                if art and row["rating_key"]:
                    self.art[(s.id, row["rating_key"])] = art
                    row["poster"] = f"/api/poster/{s.id}/{row['rating_key']}"
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
        self.streams = [r for rows in results for r in rows]
        self.last_poll = time.time()

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
        last_tel = 0.0
        while True:
            try:
                if not self.cfg.use_sample:
                    await self.poll_plex()
                    if time.time() - last_tel >= self.cfg.telemetry_seconds:
                        await self.poll_netdata()
                        last_tel = time.time()
                    self.record_minute()
            except Exception:
                log.exception("poll cycle failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.poll_seconds)
                last_tel = 0.0  # settings changed: refresh telemetry right away
            except asyncio.TimeoutError:
                pass

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
