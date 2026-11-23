"""Sign-in for people who watch on the Greystar Emby server instead of Plex.

Set EMBY_URL (e.g. http://74.207.197.131:8096) and EMBY_API_KEY (an API key made on that Emby server:
Dashboard, Advanced, API Keys) in .env. Without both, the "Sign in with Emby" button stays hidden.

Two ways in, both checked against the Emby server's own user list (GET /Users with the API key):
1. An Emby user with a local password: the name and password are checked by the Emby server itself
   (POST /Users/AuthenticateByName); the session it opens is closed again straight away.
2. Everyone else signs in to Emby with Emby Connect (most Greystar Emby users have no local password).
   The name or email and password are checked by Emby Connect, and the Connect account must be linked to a
   user on our Emby server (that user's ConnectUserName). A Connect account that is not linked stays out.
A disabled Emby user stays out either way. The password is never stored.

An Emby member is kept in the users table like a Plex member, under a member id made from the Emby user id
(`member_id`): fifteen digits starting with 9, so it never meets a real plex.tv id (those are under ten
digits) and the media site, which only takes a numeric member id, accepts it. `source` is "emby" and
`emby_id` keeps the Emby user id. Emby members are never admins.
"""
from __future__ import annotations

import time

import httpx

CONNECT_URL = "https://connect.emby.media"
APP = "GreystarTelemetry/1.0"
AUTH_HEADER = 'MediaBrowser Client="Greystar Telemetry", Device="Telemetry", DeviceId="greystar-telemetry", Version="1.0"'
MEMBER_BASE = 900_000_000_000_000  # member ids 900000000000000..999999999999999 (below 2**53, so JS keeps them exact)
USERS_SECONDS = 60


def member_id(emby_user_id: str) -> str:
    """The users-table id for an Emby user (an Emby user id is 32 hex digits)."""
    return str(MEMBER_BASE + int(emby_user_id.replace("-", "")[:16], 16) % 100_000_000_000_000)


def is_member_id(key: str | None) -> bool:
    return bool(key) and key.isdigit() and len(key) == 15 and key.startswith("9")


class EmbyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class EmbyAuth:
    def __init__(self, url: str | None, api_key: str | None, http: httpx.AsyncClient | None = None,
                 connect_url: str = CONNECT_URL):
        self.url = (url or "").rstrip("/")
        self.api_key = api_key or ""
        self.http = http
        self.connect_url = connect_url.rstrip("/")
        self._users: tuple[float, list[dict]] | None = None

    def enabled(self) -> bool:
        return bool(self.url and self.api_key)

    def _client(self) -> httpx.AsyncClient:
        return self.http or httpx.AsyncClient(timeout=15)

    async def server_users(self) -> list[dict]:
        if self._users and time.time() - self._users[0] < USERS_SECONDS:
            return self._users[1]
        try:
            r = await self._client().get(self.url + "/Users", headers={"X-Emby-Token": self.api_key}, timeout=15)
        except httpx.HTTPError:
            raise EmbyError(502, "The Greystar Emby server is not answering. Try again in a few minutes.")
        if r.status_code != 200:
            raise EmbyError(502, "The Greystar Emby server is not answering. Try again in a few minutes.")
        users = r.json() or []
        self._users = (time.time(), users)
        return users

    async def image(self, item_id: str) -> tuple[bytes, str] | None:
        """An item's cover (watch history, emby_plays.py), or None."""
        try:
            r = await self._client().get(f"{self.url}/Items/{item_id}/Images/Primary", params={"maxHeight": 450, "quality": 90},
                                         headers={"X-Emby-Token": self.api_key}, timeout=10)
        except httpx.HTTPError:
            return None
        return (r.content, r.headers.get("content-type", "image/jpeg")) if r.status_code == 200 else None

    async def _local(self, user: dict, password: str) -> bool:
        c = self._client()
        try:
            r = await c.post(self.url + "/Users/AuthenticateByName", json={"Username": user["Name"], "Pw": password},
                             headers={"X-Emby-Authorization": AUTH_HEADER}, timeout=15)
        except httpx.HTTPError:
            raise EmbyError(502, "The Greystar Emby server is not answering. Try again in a few minutes.")
        if r.status_code != 200:
            return False
        tok = (r.json() or {}).get("AccessToken")
        if tok:  # we only needed the yes: close the Emby session it opened
            try:
                await c.post(self.url + "/Sessions/Logout", headers={"X-Emby-Token": tok}, timeout=10)
            except httpx.HTTPError:
                pass
        return True

    async def _connect(self, name: str, password: str) -> dict | None:
        """The Emby Connect user for these details, or None when they are wrong."""
        try:
            r = await self._client().post(self.connect_url + "/service/user/authenticate",
                                          data={"nameOrEmail": name, "rawpw": password},
                                          headers={"X-Application": APP, "Accept": "application/json"}, timeout=15)
        except httpx.HTTPError:
            raise EmbyError(502, "Emby Connect is not answering. Try again in a few minutes.")
        if r.status_code in (401, 404):
            return None
        if r.status_code != 200:
            raise EmbyError(502, "Emby Connect is not answering. Try again in a few minutes.")
        u = (r.json() or {}).get("User") or {}
        return u if u.get("Id") else None

    async def authenticate(self, name: str, password: str) -> dict:
        """{emby_id, name, email} for a Greystar Emby member, else EmbyError (401 wrong details, 403 no access)."""
        name = name.strip()
        if not self.enabled():
            raise EmbyError(409, "Emby sign-in is not set up.")
        if not name or not password:
            raise EmbyError(401, "Wrong Emby name or password.")
        users = await self.server_users()
        low = name.lower()
        local = next((u for u in users if (u.get("Name") or "").lower() == low and u.get("HasPassword")), None)
        email = None
        if local and await self._local(local, password):
            user = local
            if "@" in (local.get("Name") or ""):
                email = local["Name"]
        else:
            cu = await self._connect(name, password)
            if not cu:
                raise EmbyError(401, "Wrong Emby name or password.")
            c_name, c_email = (cu.get("Name") or "").lower(), (cu.get("Email") or "").lower()

            def linked(u: dict) -> bool:
                cn = (u.get("ConnectUserName") or "").lower()
                if not cn or not u.get("ConnectLinkType"):
                    return False
                return cn == c_email if "@" in cn else cn == c_name

            user = next((u for u in users if linked(u)), None)
            if not user:
                raise EmbyError(403, "This Emby Connect account is not linked to the Greystar Emby server. Ask the administrator to add you.")
            email = cu.get("Email") or (user["ConnectUserName"] if "@" in user["ConnectUserName"] else None)
        if (user.get("Policy") or {}).get("IsDisabled"):
            raise EmbyError(403, "Your Emby account is turned off. Ask the administrator.")
        return {"emby_id": user["Id"], "name": user.get("Name") or name, "email": (email or "").strip().lower() or None}
