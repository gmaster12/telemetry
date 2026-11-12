"""Radarr and Sonarr: download progress for members, and removing unwatched requests (Unwatched cleanup).

Connected in Settings, Radarr & Sonarr: an address and API key for each (saved encrypted, write-only), plus
optional separate 4K instances. "Copy from Seerr" reads Seerr's own Radarr/Sonarr settings as its admin and fills
them in (the address Seerr uses may not be reachable from this server; it can be changed afterwards).

Members only ever see plain words (see `progress_word`): never release names, indexers or the download client.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
from typing import Any

import httpx

KINDS = ("radarr", "sonarr")


class ArrError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ArrClient:
    def __init__(self, kind: str, url: str, key: str, http: httpx.AsyncClient):
        self.kind = kind
        self.base = url.rstrip("/") + "/api/v3"
        self.key = key
        self.http = http

    async def call(self, method: str, path: str, *, params: dict | None = None, body: Any = None, timeout: float = 20) -> Any:
        name = "Radarr" if self.kind == "radarr" else "Sonarr"
        try:
            r = await self.http.request(method, self.base + path, params=params, json=body, timeout=timeout,
                                        headers={"X-Api-Key": self.key, "Accept": "application/json"})
        except httpx.HTTPError as e:
            raise ArrError(502, f"Could not reach {name}: {e.__class__.__name__}") from None
        if r.status_code in (401, 403):
            raise ArrError(r.status_code, f"{name} refused the API key.")
        if r.status_code >= 400:
            raise ArrError(r.status_code, f"{name} answered HTTP {r.status_code} on {path}.")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            raise ArrError(502, f"{name} sent something that is not JSON. Check the address.") from None


# ---------- plain words for members ----------
def _quality(rec: dict) -> str | None:
    """'WEBDL-1080p' -> '1080p · WEB'; 'Bluray-2160p' -> '4K · Blu-ray'."""
    name = str((((rec.get("quality") or {}).get("quality") or {}).get("name")) or "")
    if not name:
        return None
    res = re.search(r"(2160|1080|720|576|480)p", name)
    res_word = {"2160": "4K"}.get(res.group(1), res.group(1) + "p") if res else None
    low = name.lower()
    src = "Remux" if "remux" in low else "Blu-ray" if "bluray" in low else "WEB" if "web" in low else "TV" if "hdtv" in low else "DVD" if "dvd" in low else None
    return " · ".join(p for p in (res_word, src) if p) or None


PROBLEMS = [  # (words in Radarr/Sonarr's message, what a member reads)
    (("language", "audio"), "The copy has the wrong language or no usable audio."),
    (("sample",), "Only a short sample was downloaded."),
    (("not an upgrade", "existing file"), "A copy as good or better is already there."),
    (("no files found", "no video", "no eligible"), "There was no video file in the download."),
    (("unable to parse", "unknown movie", "unknown series", "matched to", "manual import", "unable to determine"),
     "The file could not be matched to this title."),
    (("free space", "disk space", "not enough space"), "The server ran short on space."),
]


def problem_text(rec: dict) -> str:
    words = " ".join(m for s in (rec.get("statusMessages") or []) for m in ((s or {}).get("messages") or []) + [(s or {}).get("title") or ""]).lower()
    words += " " + str(rec.get("errorMessage") or "").lower()
    for keys, text in PROBLEMS:
        if any(k in words for k in keys):
            return text
    return "It could not be added to Plex on its own."


def _minutes_left(rec: dict, now: float) -> int | None:
    eta = rec.get("estimatedCompletionTime")
    if eta:
        try:
            t = dt.datetime.fromisoformat(str(eta).replace("Z", "+00:00")).timestamp()
            return max(0, round((t - now) / 60))
        except ValueError:
            pass
    left = str(rec.get("timeleft") or "")
    m = re.match(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)", left)
    if m:
        d, h, mi, s = (int(x or 0) for x in m.groups())
        return round(d * 1440 + h * 60 + mi + s / 60)
    return None


def progress_word(rec: dict, now: float, speeds: dict) -> dict:
    """One Radarr/Sonarr queue record in the words members see.
    stage: queued | downloading | paused | importing | problem | retry."""
    size = float(rec.get("size") or 0)
    left = float(rec.get("sizeleft") or 0)
    done = max(0.0, size - left)
    pct = round(done / size * 100) if size else 0
    status = str(rec.get("status") or "").lower()
    tracked = str(rec.get("trackedDownloadState") or "").lower()
    tstatus = str(rec.get("trackedDownloadStatus") or "").lower()
    out = {"pct": pct, "done": done, "size": size, "quality": _quality(rec), "minutes": _minutes_left(rec, now), "speed": None,
           "added": rec.get("added")}
    # Speed from the last time we saw this download (Radarr and Sonarr do not report it).
    key = str(rec.get("downloadId") or rec.get("id") or "")
    prev = speeds.get(key)
    if prev and now > prev[0] and prev[1] >= left:
        out["speed"] = (prev[1] - left) / (now - prev[0])
    if key and (not prev or now - prev[0] >= 5):
        speeds[key] = (now, left)
    if tracked in ("importblocked",) or (tracked == "importpending" and tstatus in ("warning", "error")):
        return {**out, "stage": "problem", "word": "Import needs a look", "problem": problem_text(rec)}
    if tracked in ("importpending", "importing") or (status == "completed" and tracked != "imported"):
        return {**out, "stage": "importing", "word": "Importing"}
    if tracked in ("failed", "failedpending") or status == "failed":
        return {**out, "stage": "retry", "word": "Looking for another copy"}
    if status in ("paused", "downloadclientunavailable", "warning") and pct < 100:
        return {**out, "stage": "paused", "word": "Paused"}
    if status in ("queued", "delay") or (status == "downloading" and done == 0):
        return {**out, "stage": "queued", "word": "Queued"}
    return {**out, "stage": "downloading", "word": f"Downloading {pct}%"}


def _when(v: Any) -> float | None:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Arr:
    """Radarr and Sonarr connections (settings), with small caches."""

    def __init__(self, store, http: httpx.AsyncClient):
        self.store = store
        self.http = http
        self._cache: dict[str, tuple[float, Any]] = {}
        self._speeds: dict[str, tuple[float, float]] = {}
        self.last_error: dict[str, str | None] = {}

    # ---------- settings ----------
    @staticmethod
    def slot(kind: str, is4k: bool) -> str:
        return kind + ("4k" if is4k else "")

    def settings(self) -> dict:
        out = {}
        for kind in KINDS:
            for is4k in (False, True):
                s = self.slot(kind, is4k)
                out[s] = {"url": self.store.get_setting(s + "_url"), "has_key": bool(self.store.get_setting(s + "_key")),
                          "error": self.last_error.get(s)}
        return out

    def save(self, slot: str, url: str | None, key: str | None) -> None:
        if slot not in ("radarr", "sonarr", "radarr4k", "sonarr4k"):
            raise ValueError("No such connection.")
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Use the full address, for example http://10.0.0.5:7878")
        self.store.set_setting(slot + "_url", url or None)
        if key:
            self.store.set_setting(slot + "_key", key.strip())
        if not url:
            self.store.set_setting(slot + "_key", None)
        self._cache.clear()

    def configured(self, kind: str | None = None) -> bool:
        kinds = [kind] if kind else list(KINDS)
        return any(self.store.get_setting(k + "_url") and self.store.get_setting(k + "_key") for k in kinds)

    def client(self, kind: str, is4k: bool = False) -> ArrClient | None:
        """The 4K instance when asked for and set up, else the standard one; None when neither is."""
        for s in ([self.slot(kind, True)] if is4k else []) + [kind]:
            url, key = self.store.get_setting(s + "_url"), self.store.get_setting(s + "_key")
            if url and key:
                return ArrClient(kind, url, key, self.http)
        return None

    async def test(self, slot: str) -> dict:
        url, key = self.store.get_setting(slot + "_url"), self.store.get_setting(slot + "_key")
        if not (url and key):
            raise ArrError(409, "Not set up yet.")
        kind = "radarr" if slot.startswith("radarr") else "sonarr"
        st = await ArrClient(kind, url, key, self.http).call("GET", "/system/status", timeout=10)
        self.last_error[slot] = None
        return {"version": (st or {}).get("version"), "name": (st or {}).get("appName") or kind.title()}

    async def from_seerr(self, seerr) -> list[dict]:
        """Fill in Radarr and Sonarr from Seerr's own settings (its default standard and 4K servers)."""
        done = []
        for kind in KINDS:
            rows = await seerr.client().call("GET", f"/settings/{kind}")
            for is4k in (False, True):
                pick = [x for x in rows or [] if bool(x.get("is4k")) == is4k]
                if not pick:
                    continue
                x = next((y for y in pick if y.get("isDefault")), pick[0])
                host = str(x.get("hostname") or "").strip()
                if not host or not x.get("apiKey"):
                    continue
                url = f"{'https' if x.get('useSsl') else 'http'}://{host}:{int(x.get('port') or (7878 if kind == 'radarr' else 8989))}{x.get('baseUrl') or ''}"
                slot = self.slot(kind, is4k)
                self.save(slot, url, x["apiKey"])
                try:
                    info = await self.test(slot)
                    done.append({"slot": slot, "url": url, "ok": True, "version": info.get("version")})
                except ArrError as e:
                    self.last_error[slot] = e.message
                    done.append({"slot": slot, "url": url, "ok": False, "error": e.message})
        return done

    # ---------- reads (cached) ----------
    async def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        now = time.time()
        if hit and hit[0] > now:
            return hit[1]
        val = await fn()
        self._cache[key] = (now + ttl, val)
        if len(self._cache) > 500:
            for k in [k for k, (t, _) in self._cache.items() if t <= now]:
                self._cache.pop(k, None)
        return val

    async def queue(self, kind: str, is4k: bool = False) -> list[dict]:
        c = self.client(kind, is4k)
        if not c:
            return []
        params = {"page": 1, "pageSize": 500, "includeUnknownMovieItems": "false"} if kind == "radarr" else \
            {"page": 1, "pageSize": 500, "includeUnknownSeriesItems": "false", "includeEpisode": "true"}

        async def fetch():
            res = await c.call("GET", "/queue", params=params)
            return (res or {}).get("records") or []
        return await self._cached(f"q:{self.slot(kind, is4k)}", 10, fetch)

    async def movie(self, tmdb_id: int, is4k: bool = False) -> dict | None:
        c = self.client("radarr", is4k)
        if not c:
            return None

        async def fetch():
            rows = await c.call("GET", "/movie", params={"tmdbId": int(tmdb_id)})
            return (rows or [None])[0] if isinstance(rows, list) else rows
        return await self._cached(f"m:{is4k}:{tmdb_id}", 30, fetch)

    async def series(self, tvdb_id: int, is4k: bool = False) -> dict | None:
        c = self.client("sonarr", is4k)
        if not c or not tvdb_id:
            return None

        async def fetch():
            rows = await c.call("GET", "/series", params={"tvdbId": int(tvdb_id)})
            return (rows or [None])[0] if isinstance(rows, list) else rows
        return await self._cached(f"s:{is4k}:{tvdb_id}", 30, fetch)

    async def episodes(self, series_id: int, is4k: bool = False) -> list[dict]:
        c = self.client("sonarr", is4k)
        if not c:
            return []
        return await self._cached(f"e:{is4k}:{series_id}", 30, lambda: c.call("GET", "/episode", params={"seriesId": int(series_id)}))

    # ---------- what members see ----------
    async def movie_progress(self, tmdb_id: int, is4k: bool = False) -> dict | None:
        """{stage, word, pct, done, size, speed, minutes, quality, problem?, release?} or None when Radarr is not set up
        or does not have the movie."""
        if not self.client("radarr", is4k):
            return None
        now = time.time()
        q, m = await asyncio.gather(self.queue("radarr", is4k), self.movie(tmdb_id, is4k))
        if not m:
            return None
        recs = [r for r in q if r.get("movieId") == m.get("id")]
        if recs:
            return progress_word(max(recs, key=lambda r: float(r.get("size") or 0)), now, self._speeds)
        if m.get("hasFile"):
            return {"stage": "imported", "word": "On Plex soon", "size": float((m.get("movieFile") or {}).get("size") or m.get("sizeOnDisk") or 0)}
        if m.get("isAvailable") is False:
            rel = min([t for t in (_when(m.get("digitalRelease")), _when(m.get("physicalRelease"))) if t] or [0]) or None
            return {"stage": "release", "word": "Not out yet", "release": rel}
        return {"stage": "searching", "word": "Looking for a copy", "lastSearch": _when(m.get("lastSearchTime"))}

    async def imported_file(self, tmdb_id: int, is4k: bool = False) -> tuple[int | None, str | None, str | None]:
        """(bytes, file name, title) of the copy Radarr imported, from the record already fetched for
        progress. For checking Plex has that exact file (`app/onplex.py`); the name is a release name,
        so it never goes to a browser."""
        m = await self.movie(tmdb_id, is4k)
        if not m:
            return None, None, None
        f = m.get("movieFile") or {}
        size = int(f.get("size") or m.get("sizeOnDisk") or 0) or None
        return size, f.get("relativePath") or None, m.get("title") or None

    async def show_progress(self, tvdb_id: int, seasons: list[int] | None, is4k: bool = False) -> dict | None:
        """Per episode of the requested seasons (all monitored seasons if none given), plus counts."""
        if not self.client("sonarr", is4k):
            return None
        s = await self.series(tvdb_id, is4k)
        if not s:
            return None
        now = time.time()
        q, eps = await asyncio.gather(self.queue("sonarr", is4k), self.episodes(s["id"], is4k))
        by_ep = {}
        for r in q:
            if r.get("seriesId") == s.get("id") and r.get("episodeId") is not None:
                by_ep[r["episodeId"]] = r
        want = set(int(x) for x in seasons or []) or {int(x.get("seasonNumber")) for x in s.get("seasons") or [] if x.get("monitored") and x.get("seasonNumber")}
        out_seasons: dict[int, list] = {}
        counts = {"onplex": 0, "importing": 0, "downloading": 0, "queued": 0, "problem": 0, "searching": 0, "release": 0}
        for e in sorted(eps or [], key=lambda x: (x.get("seasonNumber") or 0, x.get("episodeNumber") or 0)):
            sn = int(e.get("seasonNumber") or 0)
            if sn not in want:
                continue
            row = {"n": e.get("episodeNumber"), "title": e.get("title") or f"Episode {e.get('episodeNumber')}"}
            air = _when(e.get("airDateUtc"))
            rec = by_ep.get(e.get("id"))
            if e.get("hasFile"):
                row.update(stage="onplex", word="On Plex")
            elif rec:
                row.update(progress_word(rec, now, self._speeds))
            elif not air or air > now:
                row.update(stage="release", word="Not out yet", release=air)
            else:
                row.update(stage="searching", word="Looking for a copy")
            st = row["stage"]
            counts["onplex" if st in ("onplex", "imported") else st if st in counts else "queued" if st in ("paused", "retry") else "searching"] += 1
            out_seasons.setdefault(sn, []).append(row)
        mins = [r.get("minutes") for rows in out_seasons.values() for r in rows if r.get("minutes") is not None]
        return {"seasons": [{"number": n, "episodes": rows} for n, rows in sorted(out_seasons.items())], "counts": counts,
                "total": sum(counts.values()), "minutes": max(mins) if mins else None}

    # ---------- removing (Unwatched cleanup) ----------
    async def delete_movie(self, tmdb_id: int, is4k: bool = False) -> int:
        """Remove the movie and its files from Radarr (no import exclusion: it can be requested again). Bytes freed."""
        c = self.client("radarr", is4k)
        if not c:
            raise ArrError(409, "Radarr is not connected (Settings, Radarr & Sonarr).")
        self._cache.pop(f"m:{is4k}:{tmdb_id}", None)
        m = await self.movie(tmdb_id, is4k)
        if not m:
            return 0
        size = int(m.get("sizeOnDisk") or 0)
        await c.call("DELETE", f"/movie/{m['id']}", params={"deleteFiles": "true", "addImportExclusion": "false"})
        self._cache.clear()
        return size

    async def delete_seasons(self, tvdb_id: int, seasons: list[int], is4k: bool = False) -> tuple[int, bool]:
        """Stop monitoring the requested seasons and delete their files; when nothing else of the series is kept,
        remove the series too. Returns (bytes freed, whole series removed)."""
        c = self.client("sonarr", is4k)
        if not c:
            raise ArrError(409, "Sonarr is not connected (Settings, Radarr & Sonarr).")
        self._cache.pop(f"s:{is4k}:{tvdb_id}", None)
        s = await self.series(tvdb_id, is4k)
        if not s:
            return 0, True
        want = {int(x) for x in seasons or []}
        files = await c.call("GET", "/episodefile", params={"seriesId": s["id"]}) or []
        gone = [f for f in files if not want or int(f.get("seasonNumber") or -1) in want]
        kept = [f for f in files if f not in gone]
        others_watched = [x for x in s.get("seasons") or [] if x.get("monitored") and int(x.get("seasonNumber") or 0) not in want and want]
        size = sum(int(f.get("size") or 0) for f in gone)
        if not kept and not others_watched:
            await c.call("DELETE", f"/series/{s['id']}", params={"deleteFiles": "true", "addImportListExclusion": "false"})
            self._cache.clear()
            return size, True
        full = await c.call("GET", f"/series/{s['id']}")
        for x in (full or {}).get("seasons") or []:
            if int(x.get("seasonNumber") or -1) in want:
                x["monitored"] = False
        await c.call("PUT", f"/series/{s['id']}", body=full)
        if gone:
            await c.call("DELETE", "/episodefile/bulk", body={"episodeFileIds": [f["id"] for f in gone]})
        self._cache.clear()
        return size, False

    async def size_of(self, kind: str, tmdb_id: int | None, tvdb_id: int | None, seasons: list[int] | None, is4k: bool) -> int | None:
        """Bytes on disk for a tracked title (the requested seasons of a show), or None when unknown."""
        try:
            if kind == "movie" and tmdb_id:
                m = await self.movie(tmdb_id, is4k)
                return int(m.get("sizeOnDisk") or 0) if m else None
            if kind == "tv" and tvdb_id:
                s = await self.series(tvdb_id, is4k)
                if not s:
                    return None
                want = {int(x) for x in seasons or []}
                return sum(int((x.get("statistics") or {}).get("sizeOnDisk") or 0) for x in s.get("seasons") or []
                           if not want or int(x.get("seasonNumber") or -1) in want)
        except ArrError:
            return None
        return None
