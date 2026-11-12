"""Is it on Plex yet? Ask the request portal's own Plex server, not only Seerr (Glenn's ask, Nov 12 2026).

Seerr only learns that a title is available from its own library scans: the quick one (every five
minutes) reads Plex's *Recently added* shelf, and the thorough one runs once a day. When a download
lands in a folder Plex already has an entry for (an old item whose file went away), Plex updates
that entry instead of adding a new one, so Recently added never shows it and a finished title can
read "Importing" until the nightly scan. Divergent did exactly that on Nov 12 2026: Radarr imported
it at 12:09 PM, Plex had it seconds later, and Seerr would not have agreed until 3 AM.

So both sides are asked and whichever says it is on Plex first wins. This side is deliberately
stricter than Seerr, which marks a title available on a TMDB id match alone:

- the library item's TMDB id must match (or, for an item an old Plex agent left without ids, the
  file itself must be the one Radarr/Sonarr imported, by size or by name),
- it must have a file with bytes at the resolution the request asked for (4K requests want a 4K
  file, ordinary ones do not, which is Seerr's own rule), and
- for a movie, Plex must say that file exists and is readable right now (`checkFiles`).

A confirmation here can therefore never be weaker evidence than Seerr's. Nothing is written
anywhere: this only answers a question, and when it cannot (Plex quiet, no server linked, turned
off in `onplex_direct`) the answer is None and Seerr stays the only source, as before.
"""
from __future__ import annotations

import asyncio
import logging
import ntpath
import time
from typing import Any, Iterable

log = logging.getLogger("telemetry.onplex")

WINDOW = 120          # items read from the "changed most recently" end of each library
WINDOW_TTL = 30.0     # seconds one of those reads is reused for (every check shares it)
MISS_TTL = 45.0       # how long "not there yet" is kept before asking Plex again
HIT_TTL = 6 * 3600.0  # how long a confirmation is kept (Seerr has caught up long before)
SICK_TTL = 60.0       # how long to leave a Plex server alone after it failed to answer
SERVER_TTL = 600.0    # how long the portal's server is remembered
IDENTITY_TTL = 3600.0

MOVIE, SHOW = 1, 2


def _name(path: Any) -> str:
    return ntpath.basename(str(path or "")).strip().lower()


def ids_of(item: dict, prefix: str) -> set[int]:
    """The item's ids from Plex's Guid list: ids_of(movie, "tmdb") -> {157350}."""
    out = set()
    for g in item.get("Guid") or []:
        gid = str((g or {}).get("id") or "")
        if gid.startswith(prefix + "://"):
            try:
                out.add(int(gid.split("://", 1)[1].split("?")[0]))
            except ValueError:
                pass
    return out


def is_4k(media: dict) -> bool:
    return str(media.get("videoResolution") or "").lower() in ("4k", "2160")


def parts_of(item: dict, want4k: bool) -> list[dict]:
    """Files of the item at the asked-for resolution, biggest first. Seerr decides 4K the same way."""
    out = [p for m in item.get("Media") or [] if is_4k(m) == want4k
           for p in m.get("Part") or [] if int(p.get("size") or 0) > 0]
    return sorted(out, key=lambda p: -int(p.get("size") or 0))


def match_part(item: dict, *, tmdb_id: int | None = None, tvdb_id: int | None = None,
               size: int | None = None, file_name: str | None = None, want4k: bool = False) -> dict | None:
    """The file that proves this library item is the requested title, or None.

    By id when Plex has the ids (the exact imported file first, any other copy of the same title
    after: an older copy is still on Plex). By the file itself when Plex has no ids for the item,
    which is how a library entry from an old Plex agent looks."""
    parts = parts_of(item, want4k)
    if not parts:
        return None
    exact = [p for p in parts
             if (size and int(p.get("size") or 0) == int(size)) or (file_name and _name(p.get("file")) == _name(file_name))]
    if exact:
        return exact[0]
    known = (tmdb_id is not None and tmdb_id in ids_of(item, "tmdb")) or \
            (tvdb_id is not None and tvdb_id in ids_of(item, "tvdb"))
    return parts[0] if known else None


class OnPlex:
    """Asks the Plex server behind the request portal whether a finished title is in its library."""

    def __init__(self, store, seerr, poller):
        self.store = store
        self.seerr = seerr
        self.poller = poller
        self._hits: dict[str, float] = {}
        self._misses: dict[str, float] = {}
        self._window: dict[str, tuple[float, list[Any]]] = {}   # library items, and each library's keys
        self._identity: dict[int, tuple[float, str]] = {}
        self._portal_cache: tuple[float, int | None] = (0.0, None)
        self._sick: dict[int, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ---------- switches and the server to ask ----------
    def enabled(self) -> bool:
        """On unless an admin sets `onplex_direct` to `off` (then Seerr is the only source, as before)."""
        return str(self.store.get_setting("onplex_direct") or "on").lower() != "off"

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def _machine_id(self, server) -> str | None:
        hit = self._identity.get(server.id)
        now = time.time()
        if hit and hit[0] > now:
            return hit[1]
        try:
            ident = await self.poller.client(server).identity()
        except Exception as e:  # noqa: BLE001  (any Plex trouble: this is a best effort)
            log.debug("onplex: %s did not answer /identity: %s", server.name, e)
            self._sick[server.id] = now + SICK_TTL
            return None
        mid = str(ident.get("machineIdentifier") or "")
        self._identity[server.id] = (now + IDENTITY_TTL, mid)
        return mid

    async def _portal_plex(self) -> dict | None:
        """Seerr's own Plex server setting: which machine it scans and which libraries it reads."""
        try:
            return await self.seerr.get("/settings/plex", ttl=SERVER_TTL)
        except Exception as e:  # noqa: BLE001  (SeerrError, or Seerr not connected)
            log.debug("onplex: could not read Seerr's Plex setting: %s", e)
            return None

    async def server(self):
        """The configured Plex server the request portal scans, or None when it cannot be identified.

        `onplex_server_id` names it outright; otherwise it is the server whose machine id (or, failing
        that, whose name) is the one in Seerr's own Plex setting. Never guesses: with several servers
        and no match, nothing is asked and Seerr stays the only source."""
        now = time.time()
        live = [s for s in (self.poller.cfg.servers or []) if s.enabled and s.token]
        if self._portal_cache[0] > now:
            hit = next((s for s in live if s.id == self._portal_cache[1]), None)
            if hit or self._portal_cache[1] is None:
                return hit     # a server that was edited away is worked out again below
        pick = None
        forced = self.store.get_setting("onplex_server_id")
        if forced and str(forced).isdigit():
            pick = next((s for s in live if s.id == int(forced)), None)
        if not pick:
            cfg = await self._portal_plex() or {}
            machine = str(cfg.get("machineId") or "")
            if machine:
                for s in live:
                    if await self._machine_id(s) == machine:
                        pick = s
                        break
            if not pick and cfg.get("name"):
                want = str(cfg["name"]).strip().lower()
                pick = next((s for s in live if s.name.strip().lower() == want), None)
        self._portal_cache = (now + SERVER_TTL, pick.id if pick else None)
        if not pick:
            log.info("onplex: no configured Plex server matches the request portal's own; Seerr stays the only source")
        return pick

    async def _libraries(self, server, kind: int) -> list[str]:
        """The portal's enabled libraries of that kind (Seerr's list when it has one, else all of them)."""
        key = f"lib:{server.id}:{kind}"
        now = time.time()
        hit = self._window.get(key)
        if hit and hit[0] > now:
            return list(hit[1])
        cfg = await self._portal_plex() or {}
        allowed = {str(lib.get("id")) for lib in cfg.get("libraries") or [] if lib.get("enabled")}
        try:
            dirs = await self.poller.client(server).sections()
        except Exception as e:  # noqa: BLE001
            log.debug("onplex: %s did not list its libraries: %s", server.name, e)
            self._sick[server.id] = now + SICK_TTL
            return []
        want = "movie" if kind == MOVIE else "show"
        keys = [str(d.get("key")) for d in dirs if d.get("type") == want and (not allowed or str(d.get("key")) in allowed)]
        self._window[key] = (now + SERVER_TTL, keys)
        return list(keys)

    # ---------- reading the library ----------
    async def _changed(self, server, section: str, kind: int) -> list[dict]:
        """The most recently changed items of one library, shared by every check for 30 seconds.

        Sorting by `updatedAt` is the point: a new file for an item Plex already had bumps that, while
        `addedAt` (what Seerr's five-minute scan reads) keeps the day the old entry was made."""
        key = f"{server.id}:{section}:{kind}"
        async with self._lock("w:" + key):
            hit = self._window.get(key)
            now = time.time()
            if hit and hit[0] > now:
                return hit[1]
            try:
                items = await self.poller.client(server).items(section, kind, size=WINDOW, sort="updatedAt:desc")
            except Exception as e:  # noqa: BLE001
                log.debug("onplex: %s library %s did not answer: %s", server.name, section, e)
                self._sick[server.id] = now + SICK_TTL
                return []
            self._window[key] = (now + WINDOW_TTL, items)
            if len(self._window) > 60:
                for k, (until, _) in list(self._window.items()):
                    if until <= now:
                        self._window.pop(k, None)
            return items

    async def _titled(self, server, section: str, kind: int, title: str | None) -> list[dict]:
        if not title:
            return []
        try:
            return await self.poller.client(server).items(section, kind, size=30, title=title)
        except Exception as e:  # noqa: BLE001
            log.debug("onplex: %s search in library %s failed: %s", server.name, section, e)
            self._sick[server.id] = time.time() + SICK_TTL
            return []

    async def _readable(self, server, rating_key: Any, part: dict) -> bool:
        """Plex says this file exists and it can read it. An unreadable file is not on Plex in any
        useful sense, and this is what catches a library entry whose copy went away."""
        try:
            d = await self.poller.client(server).details(str(rating_key), check_files=True)
        except Exception as e:  # noqa: BLE001
            log.debug("onplex: %s could not re-read item %s: %s", server.name, rating_key, e)
            self._sick[server.id] = time.time() + SICK_TTL
            return False
        for m in d.get("Media") or []:
            for p in m.get("Part") or []:
                same = (part.get("id") is not None and p.get("id") == part.get("id")) or \
                       (part.get("file") and p.get("file") == part.get("file"))
                if not same:
                    continue
                if p.get("exists") is False or p.get("accessible") is False:
                    return False
                return int(p.get("size") or 0) > 0
        return False

    # ---------- answers ----------
    def _answer(self, key: str) -> float | None:
        hit = self._hits.get(key)
        if hit and time.time() - hit < HIT_TTL:
            return hit
        return None

    def _ready(self, key: str, server) -> bool:
        if self._misses.get(key, 0) > time.time():
            return False
        return self._sick.get(server.id, 0) <= time.time()

    def _miss(self, key: str) -> None:
        self._misses[key] = time.time() + MISS_TTL
        if len(self._misses) > 2000:
            now = time.time()
            for k, until in list(self._misses.items()):
                if until <= now:
                    self._misses.pop(k, None)

    def _hit(self, key: str) -> float:
        when = self._hits.setdefault(key, time.time())
        return when

    async def movie(self, tmdb_id: int, *, is4k: bool = False, size: int | None = None,
                    file_name: str | None = None, title: str | None = None) -> float | None:
        """When this movie is really in the portal's Plex library, the time it was first seen there;
        otherwise None. `size`/`file_name` are what Radarr imported, `title` is only a fallback search."""
        if not (self.enabled() and tmdb_id):
            return None
        key = f"movie:{int(tmdb_id)}:{int(bool(is4k))}"
        got = self._answer(key)
        if got:
            return got
        server = await self.server()
        if not server or not self._ready(key, server):
            return None
        async with self._lock(key):
            got = self._answer(key)
            if got:
                return got
            for section in await self._libraries(server, MOVIE):
                for source in ("changed", "title"):
                    if source == "title" and not title:
                        continue
                    items = await self._changed(server, section, MOVIE) if source == "changed" \
                        else await self._titled(server, section, MOVIE, title)
                    for item in items:
                        part = match_part(item, tmdb_id=int(tmdb_id), size=size, file_name=file_name, want4k=bool(is4k))
                        if part and await self._readable(server, item.get("ratingKey"), part):
                            log.info("onplex: %s has TMDB %s (%s) before Seerr said so", server.name, tmdb_id,
                                     "4K" if is4k else "ordinary")
                            return self._hit(key)
            self._miss(key)
            return None

    async def episodes(self, want: Iterable[tuple[int, int]], *, tvdb_id: int | None = None, tmdb_id: int | None = None,
                       sizes: dict[tuple[int, int], int] | None = None, title: str | None = None,
                       is4k: bool = False) -> float | None:
        """When every asked-for episode is in the portal's Plex library, the time they were first all
        seen there; otherwise None. `want` is (season, episode) pairs, `sizes` what Sonarr imported."""
        want = {(int(s), int(e)) for s, e in want}
        if not (self.enabled() and want and (tvdb_id or tmdb_id)):
            return None
        key = f"tv:{tvdb_id or 0}:{tmdb_id or 0}:{int(bool(is4k))}:" + ",".join(f"{s}x{e}" for s, e in sorted(want))
        got = self._answer(key)
        if got:
            return got
        server = await self.server()
        if not server or not self._ready(key, server):
            return None
        async with self._lock(key):
            got = self._answer(key)
            if got:
                return got
            for section in await self._libraries(server, SHOW):
                for source in ("changed", "title"):
                    if source == "title" and not title:
                        continue
                    items = await self._changed(server, section, SHOW) if source == "changed" \
                        else await self._titled(server, section, SHOW, title)
                    for show in items:
                        known = (tvdb_id and tvdb_id in ids_of(show, "tvdb")) or (tmdb_id and tmdb_id in ids_of(show, "tmdb"))
                        if not known:
                            continue
                        if await self._has_all(server, show, want, sizes, bool(is4k)):
                            log.info("onplex: %s has every asked-for episode of %s before Seerr said so",
                                     server.name, show.get("title"))
                            return self._hit(key)
            self._miss(key)
            return None

    async def _has_all(self, server, show: dict, want: set[tuple[int, int]],
                       sizes: dict[tuple[int, int], int] | None, want4k: bool) -> bool:
        try:
            leaves = await self.poller.client(server).leaves(show.get("ratingKey"))
        except Exception as e:  # noqa: BLE001
            log.debug("onplex: %s could not list episodes of %s: %s", server.name, show.get("title"), e)
            self._sick[server.id] = time.time() + SICK_TTL
            return False
        have: dict[tuple[int, int], list[dict]] = {}
        for ep in leaves:
            try:
                at = (int(ep.get("parentIndex")), int(ep.get("index")))
            except (TypeError, ValueError):
                continue
            have.setdefault(at, []).extend(parts_of(ep, want4k))
        for at in want:
            parts = have.get(at) or []
            if not parts:
                return False
            wanted = (sizes or {}).get(at)
            if wanted and not any(int(p.get("size") or 0) == int(wanted) for p in parts):
                return False
        return True
