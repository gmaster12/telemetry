"""Requests: Seerr (Overseerr / Jellyseerr / Seerr) inside the dashboard.

The dashboard talks to Glenn's Seerr with its API key, server side only. Every call made for a
person carries `X-API-User: <their Seerr user id>`, so Seerr applies that person's own permissions,
quotas, auto-approve rules and notifications, exactly as if they had used Seerr themselves.

People sign in here with Plex. Their Seerr account is found by email or Plex username; if they have
none yet it is imported from Plex (the same as Seerr's "Import Plex users"), and if Seerr's own Plex
check cannot see them (they are on a server that belongs to another Plex account) a Seerr account
is created for them with their Plex email, so a later Seerr sign-in with Plex lands on the same
account. Links are remembered in `seerr_links`.

Nothing from Seerr goes to the browser unfiltered: other people's emails, tokens and settings are
removed, and only the fields the pages use are kept for people (`scrub`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("telemetry.seerr")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seerr_links (
  plex_id TEXT PRIMARY KEY,
  seerr_id INTEGER NOT NULL,
  how TEXT,
  created INTEGER
);
CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at INTEGER NOT NULL,
  who_kind TEXT NOT NULL,
  who_id TEXT NOT NULL,
  who_name TEXT,
  who_email TEXT,
  seerr_user INTEGER,
  seerr_request INTEGER,
  media_type TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL,
  title TEXT,
  year TEXT,
  poster TEXT,
  seasons TEXT,
  is4k INTEGER NOT NULL DEFAULT 0,
  status TEXT,
  source TEXT NOT NULL DEFAULT 'site',
  ended TEXT,
  ended_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS request_log_req ON request_log (seerr_request) WHERE seerr_request IS NOT NULL;
CREATE INDEX IF NOT EXISTS request_log_at ON request_log (at);
CREATE TABLE IF NOT EXISTS title_scores (
  kind TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL,
  data TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (kind, tmdb_id)
);
"""

# Seerr's own numbers (server/constants/media.ts, server/lib/permissions.ts)
MEDIA_STATUS = {1: "unknown", 2: "pending", 3: "processing", 4: "partial", 5: "available", 6: "blocklisted", 7: "deleted"}
REQUEST_STATUS = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}
P = {"ADMIN": 2, "MANAGE_USERS": 8, "MANAGE_REQUESTS": 16, "REQUEST": 32, "AUTO_APPROVE": 128, "AUTO_APPROVE_MOVIE": 256,
     "AUTO_APPROVE_TV": 512, "REQUEST_4K": 1024, "REQUEST_4K_MOVIE": 2048, "REQUEST_4K_TV": 4096, "REQUEST_ADVANCED": 8192,
     "REQUEST_VIEW": 16384, "AUTO_APPROVE_4K": 32768, "AUTO_APPROVE_4K_MOVIE": 65536, "AUTO_APPROVE_4K_TV": 131072,
     "REQUEST_MOVIE": 262144, "REQUEST_TV": 524288, "MANAGE_ISSUES": 1048576, "VIEW_ISSUES": 2097152, "CREATE_ISSUES": 4194304,
     "RECENT_VIEW": 67108864, "WATCHLIST_VIEW": 134217728}

# Keys never passed to the browser, wherever they appear.
SECRET_KEYS = {"plexToken", "jellyfinAuthToken", "password", "resetPasswordGuid", "recoveryLinkExpirationDate", "settings",
               "pushSubscriptions", "notificationTypes", "apiKey", "email", "plexId", "jellyfinUserId", "jellyfinDeviceId",
               "warnings", "serviceUrl", "serviceUrl4k", "externalServiceSlug", "externalServiceSlug4k", "tautulliUrl", "tautulliUrl4k"}
USER_KEYS = {"id", "displayName", "avatar", "username", "plexUsername", "userType", "requestCount"}


class SeerrError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def has(perms: int, *names: str) -> bool:
    """Seerr's rule: ADMIN has everything; otherwise any of the named bits."""
    perms = int(perms or 0)
    return bool(perms & P["ADMIN"]) or any(perms & P[n] for n in names)


def scrub(obj: Any, me: int | None = None) -> Any:
    """Copy Seerr JSON without secrets or other people's contact details."""
    if isinstance(obj, list):
        return [scrub(x, me) for x in obj]
    if not isinstance(obj, dict):
        return obj
    looks_like_user = "displayName" in obj and ("userType" in obj or "plexUsername" in obj or "permissions" in obj)
    out = {}
    for k, v in obj.items():
        if k in SECRET_KEYS:
            continue
        if looks_like_user and k not in USER_KEYS and not (k == "permissions" and obj.get("id") == me):
            continue
        out[k] = scrub(v, me)
    return out


def query_string(params: dict | None) -> str:
    """Seerr rejects '+' for spaces and raw reserved characters (' ( ) ! *): encode everything but
    letters, digits and - _ . ~ as %XX, the way its own web page does."""
    pairs = [(k, v) for k, v in (params or {}).items() if v is not None]
    return "&".join(f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in pairs)


class SeerrClient:
    def __init__(self, url: str, key: str, http: httpx.AsyncClient):
        self.base = url.rstrip("/") + "/api/v1"
        self.key = key
        self.http = http

    async def call(self, method: str, path: str, *, user: int | None = None, params: dict | None = None,
                   body: Any = None, timeout: float = 20) -> Any:
        headers = {"X-Api-Key": self.key, "Accept": "application/json"}
        if user:
            headers["X-API-User"] = str(int(user))
        try:
            q = query_string(params)
            r = await self.http.request(method, self.base + path + ("?" + q if q else ""), headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise SeerrError(502, f"Could not reach Seerr: {e.__class__.__name__}") from None
        if r.status_code >= 400:
            msg = ""
            try:
                j = r.json()
                msg = j.get("message") or j.get("error") or (j.get("detail") if isinstance(j.get("detail"), str) else "") or ""
            except ValueError:
                pass
            if r.status_code in (401, 403) and not msg:
                msg = "Seerr refused the request."
            raise SeerrError(r.status_code, msg or f"Seerr answered HTTP {r.status_code}.")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            raise SeerrError(502, "Seerr sent something that is not JSON. Check the address.") from None


class Requests:
    """Seerr connection, people links and a small cache."""

    def __init__(self, store, http: httpx.AsyncClient):
        self.store = store
        self.http = http
        with store.lock:
            store.db.executescript(SCHEMA)
            store.db.commit()
        from .request_help import RequestHelp
        self.help = RequestHelp(store)  # one retry then Notify admin, Notify me too, searches that found nothing
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_error: str | None = None

    # ---------- connection ----------
    def configured(self) -> bool:
        return bool(self.store.get_setting("seerr_url") and self.store.get_setting("seerr_key"))

    def client(self) -> SeerrClient:
        url, key = self.store.get_setting("seerr_url"), self.store.get_setting("seerr_key")
        if not (url and key):
            raise SeerrError(409, "Requests are not connected yet. An administrator can connect Seerr in Settings, Requests.")
        return SeerrClient(url, key, self.http)

    def clear_cache(self) -> None:
        self._cache.clear()

    async def cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        now = time.time()
        if hit and hit[0] > now:
            return hit[1]
        val = await fn()
        self._cache[key] = (now + ttl, val)
        if len(self._cache) > 2000:  # keep it small: drop the expired, then the oldest
            for k in [k for k, (t, _) in self._cache.items() if t <= now] or sorted(self._cache, key=lambda k: self._cache[k][0])[:500]:
                self._cache.pop(k, None)
        return val

    async def get(self, path: str, *, user: int | None = None, params: dict | None = None, ttl: float = 0) -> Any:
        c = self.client()
        if not ttl:
            return await c.call("GET", path, user=user, params=params)
        key = f"{user or 0}|{path}|{sorted((params or {}).items())}"
        return await self.cached(key, ttl, lambda: c.call("GET", path, user=user, params=params))

    async def send(self, method: str, path: str, *, user: int | None = None, body: Any = None) -> Any:
        out = await self.client().call(method, path, user=user, body=body)
        self.clear_cache()  # statuses changed: show them right away
        return out

    async def test(self) -> dict:
        c = self.client()
        status = await c.call("GET", "/status")
        me = await c.call("GET", "/auth/me")
        pub = await c.call("GET", "/settings/public")
        return {"version": status.get("version"), "admin": (me or {}).get("displayName"), "title": pub.get("applicationTitle"),
                "movie4k": bool(pub.get("movie4kEnabled")), "series4k": bool(pub.get("series4kEnabled"))}

    # ---------- people ----------
    def link_for(self, plex_id: str) -> int | None:
        r = self.store.db.execute("SELECT seerr_id FROM seerr_links WHERE plex_id = ?", (str(plex_id),)).fetchone()
        return int(r["seerr_id"]) if r else None

    def save_link(self, plex_id: str, seerr_id: int, how: str) -> None:
        with self.store.lock:
            self.store.db.execute("INSERT INTO seerr_links (plex_id, seerr_id, how, created) VALUES (?, ?, ?, ?) "
                                  "ON CONFLICT(plex_id) DO UPDATE SET seerr_id = excluded.seerr_id, how = excluded.how, created = excluded.created",
                                  (str(plex_id), int(seerr_id), how, int(time.time())))
            self.store.db.commit()

    def drop_link(self, plex_id: str) -> None:
        with self.store.lock:
            self.store.db.execute("DELETE FROM seerr_links WHERE plex_id = ?", (str(plex_id),))
            self.store.db.commit()

    def links(self) -> list[dict]:
        return [dict(r) for r in self.store.db.execute("SELECT plex_id, seerr_id, how, created FROM seerr_links ORDER BY created DESC")]

    # ---------- request log: every request made through this site, for admins ----------
    def log_request(self, principal, seerr_user: int | None, seerr_request: int | None, media_type: str, tmdb_id: int,
                    info: dict, seasons: list | None, is4k: bool, status: str | None, *, at: int | None = None, source: str = "site") -> None:
        with self.store.lock:
            self.store.db.execute(
                "INSERT OR IGNORE INTO request_log (at, who_kind, who_id, who_name, who_email, seerr_user, seerr_request, media_type, tmdb_id, "
                "title, year, poster, seasons, is4k, status, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(at or time.time()), principal.kind, str(principal.subject), principal.name, principal.email, seerr_user, seerr_request,
                 media_type, int(tmdb_id), info.get("title"), info.get("year"), info.get("poster"),
                 ",".join(str(n) for n in seasons) if isinstance(seasons, list) else None, 1 if is4k else 0, status, source))
            self.store.db.commit()

    def log_end(self, seerr_request: int, what: str) -> None:
        """Mark a logged request canceled (pending, by its owner) or removed (failed, cleared from the list)."""
        with self.store.lock:
            self.store.db.execute("UPDATE request_log SET ended = ?, ended_at = ? WHERE seerr_request = ? AND ended IS NULL",
                                  (what, int(time.time()), int(seerr_request)))
            self.store.db.commit()

    def log_rows(self) -> list[dict]:
        return [dict(r) for r in self.store.db.execute("SELECT * FROM request_log ORDER BY at DESC, id DESC")]

    def log_counts(self, now: float | None = None) -> dict:
        now = now or time.time()
        db = self.store.db
        one = lambda q, *a: db.execute(q, a).fetchone()[0]
        return {"total": one("SELECT COUNT(*) FROM request_log"),
                "day": one("SELECT COUNT(*) FROM request_log WHERE at >= ?", int(now - 86400)),
                "week": one("SELECT COUNT(*) FROM request_log WHERE at >= ?", int(now - 7 * 86400)),
                "people": one("SELECT COUNT(DISTINCT who_kind || ':' || who_id) FROM request_log")}

    async def title_of(self, kind: str, tmdb: int) -> dict:
        try:
            d = await self.get(f"/{'movie' if kind == 'movie' else 'tv'}/{int(tmdb)}", ttl=6 * 3600)
        except SeerrError:
            return {"title": None, "year": "", "poster": None}
        when = d.get("releaseDate") or d.get("firstAirDate") or ""
        return {"title": d.get("title") or d.get("name"), "year": when[:4], "poster": d.get("posterPath")}

    # ---------- scores: ratings and box office for poster chips and the About list ----------
    SCORE_TTL = 24 * 3600      # a released title's numbers barely move
    SCORE_TTL_SHORT = 6 * 3600  # not out yet, or Seerr could not answer: look again sooner

    def _saved_scores(self, keys: list[tuple[str, int]]) -> dict[tuple[str, int], tuple[dict, int]]:
        if not keys:
            return {}
        out = {}
        with self.store.lock:
            for kind, tid in keys:
                row = self.store.db.execute("SELECT data, fetched_at FROM title_scores WHERE kind = ? AND tmdb_id = ?", (kind, tid)).fetchone()
                if row:
                    out[(kind, tid)] = (json.loads(row[0]), int(row[1]))
        return out

    async def _fetch_scores(self, kind: str, tid: int) -> tuple[dict, bool]:
        """One title's numbers from Seerr (as its administrator). Returns (scores, complete)."""
        out: dict = {"kind": kind, "id": tid, "rt": None, "imdb": None, "tmdb": None, "money": None}
        complete = True
        c = self.client()
        try:
            d = await c.call("GET", f"/{'movie' if kind == 'movie' else 'tv'}/{tid}")
        except SeerrError:
            d, complete = {}, False
        if d.get("voteAverage"):
            out["tmdb"] = {"score": round(float(d["voteAverage"]) * 10), "votes": int(d.get("voteCount") or 0)}
        if kind == "movie":
            budget, revenue = int(d.get("budget") or 0), int(d.get("revenue") or 0)
            if budget >= 100000 and revenue > 0:
                m = revenue / budget
                out["money"] = {"budget": budget, "revenue": revenue, "profit": revenue - budget, "multiple": round(m, 1),
                                 "verdict": "hit" if m >= 1.5 else "even" if m >= 0.8 else "loss"}
            elif d and d.get("status") != "Released":
                complete = False
        rt = imdb = None
        try:
            if kind == "movie":
                try:
                    r = await c.call("GET", f"/movie/{tid}/ratingscombined")
                    rt, imdb = (r or {}).get("rt"), (r or {}).get("imdb")
                except SeerrError as e:
                    if e.status != 404:
                        raise
                    # an older Seerr has no combined route (and a title with no ratings is a 404 too): try the Rotten Tomatoes one
                    rt = await c.call("GET", f"/movie/{tid}/ratings")
            else:
                rt = await c.call("GET", f"/tv/{tid}/ratings")
        except SeerrError as e:
            if e.status != 404:
                complete = False
        if isinstance(rt, dict) and rt.get("criticsScore") is not None:
            rating = str(rt.get("criticsRating") or "")
            out["rt"] = {"critics": int(rt["criticsScore"]), "fresh": "rotten" not in rating.lower() if rating else int(rt["criticsScore"]) >= 60,
                         "certified": "certified" in rating.lower(), "audience": rt.get("audienceScore"),
                         "upright": str(rt.get("audienceRating") or "").lower() != "spilled" if rt.get("audienceScore") is not None else None}
        if isinstance(imdb, dict) and imdb.get("criticsScore") is not None:
            out["imdb"] = {"score": float(imdb["criticsScore"]), "votes": int(imdb.get("criticsScoreCount") or 0)}
        return out, complete

    async def scores(self, keys: list[tuple[str, int]]) -> list[dict]:
        """Ratings and box office for many titles. Saved in SQLite so a page of posters costs Seerr nothing after
        the first look; new titles are fetched a few at a time."""
        keys = list(dict.fromkeys((k, int(i)) for k, i in keys if k in ("movie", "tv")))[:60]
        now = int(time.time())
        saved = self._saved_scores(keys)
        out: dict[tuple[str, int], dict] = {}
        todo = []
        for k in keys:
            data, at = saved.get(k, (None, 0))
            ttl = self.SCORE_TTL if data and data.get("_complete") else self.SCORE_TTL_SHORT
            if data and now - at < ttl:
                out[k] = data
            else:
                todo.append(k)
        if todo and self.configured():
            sem = asyncio.Semaphore(4)

            async def one(k):
                async with sem:
                    return k, await self._fetch_scores(*k)
            for k, (data, complete) in await asyncio.gather(*(one(k) for k in todo)):
                data["_complete"] = complete
                out[k] = data
                with self.store.lock:
                    self.store.db.execute("INSERT OR REPLACE INTO title_scores (kind, tmdb_id, data, fetched_at) VALUES (?, ?, ?, ?)",
                                          (k[0], k[1], json.dumps(data), now))
                    self.store.db.commit()
        return [{x: v for x, v in out[k].items() if x != "_complete"} for k in keys if k in out]

    async def all_requests(self, max_pages: int = 30) -> dict[int, dict]:
        """Every request Seerr has, by id, as its administrator (cached a minute): the current state of logged requests."""
        async def fetch():
            out: dict[int, dict] = {}
            for page in range(max_pages):
                res = await self.client().call("GET", "/request", params={"take": 100, "skip": page * 100, "filter": "all", "sort": "added"})
                rows = (res or {}).get("results") or []
                for q in rows:
                    if q.get("id") is not None:
                        out[int(q["id"])] = q
                pages = ((res or {}).get("pageInfo") or {}).get("pages") or 1
                if not rows or page + 1 >= pages:
                    break
            return out
        return await self.cached("all-requests", 60, fetch)

    async def _find(self, email: str | None, username: str | None) -> int | None:
        c = self.client()
        for q in [x for x in (email, username) if x]:
            res = await c.call("GET", "/user", params={"q": q, "take": 50})
            for u in (res or {}).get("results", []):
                if email and (u.get("email") or "").lower() == email.lower():
                    return int(u["id"])
                if username and username.lower() in {(u.get("plexUsername") or "").lower(), (u.get("username") or "").lower()}:
                    return int(u["id"])
        return None

    async def seerr_user(self, principal, directory) -> int | None:
        """The Seerr user id to act as. None means Seerr's own administrator (user 1): local admin logins."""
        if principal.kind != "plex":
            return None
        pid = str(principal.subject)
        known = self.link_for(pid)
        if known:
            return known
        lock = self._locks.setdefault(pid, asyncio.Lock())
        async with lock:
            known = self.link_for(pid)
            if known:
                return known
            u = directory.get_user(pid) or {}
            email = (u.get("email") or principal.email or "").strip().lower() or None
            username = u.get("username") or None
            sid = await self._find(email, username)
            how = "matched"
            if not sid:
                # Same as Seerr's "Import Plex users", for this one person.
                try:
                    await self.client().call("POST", "/user/import-from-plex", body={"plexIds": [pid]})
                except SeerrError as e:
                    log.info("Seerr Plex import for one user failed: %s", e.message)
                sid = await self._find(email, username)
                how = "imported"
            if not sid:
                if not email:
                    raise SeerrError(409, "Your Plex account has no email address, so a Seerr account cannot be made for you. Ask the admin to add you in Seerr.")
                # Seerr's Plex check only sees servers its own Plex account shares. Make the account by email:
                # signing in to Seerr with Plex later finds it by the same email.
                made = await self.client().call("POST", "/user", body={"email": email, "username": username or email.split("@")[0],
                                                                        "password": secrets.token_urlsafe(24)})
                sid = int((made or {}).get("id") or 0) or await self._find(email, username)
                how = "created"
            if not sid:
                raise SeerrError(502, "Could not find or make a Seerr account for you.")
            self.save_link(pid, sid, how)
            log.info("Linked Plex user to Seerr user %s (%s)", sid, how)
            return sid
