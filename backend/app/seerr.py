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
