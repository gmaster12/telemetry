"""Books & comics: the Greystar media site (comics, ebooks, audiobooks) shown inside this dashboard.

The media site runs on another server (https://media.greystarnetworks.com). This dashboard shows it in a
frame on its Books & comics page and signs the person in there with a short-lived pass, so nobody signs in
twice. The pass is signed with a secret both sites share (made on the media site: Administration, Settings,
Telemetry control panel; pasted here in Settings, Books & comics, saved encrypted as `media_secret`).

Pass: `v1.<payload>.<signature>`, base64url, signature = HMAC-SHA256(secret, "v1." + payload). Payload JSON:
{v: 1, sub: "<numeric plex.tv id>", email, username, title, thumb, iat, exp, nonce, next}. It lives 60 seconds
and the media site accepts each nonce once. The media site matches the person by Plex id, then Plex email, and
makes an approved account when there is none. Listening to audiobooks there needs its Audiobooks add-on.

Only Plex sign-ins get a pass: a local admin login is not a Plex account, so the media site could not tell
who it is.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import quote, urlparse

from .store import Store

DEFAULT_MEDIA_URL = "https://media.greystarnetworks.com"
PASS_SECONDS = 60
_NEXT_RE = re.compile(r"^/[A-Za-z0-9/_\-.~?=&%:+,]*$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def media_origin(url: str | None) -> str | None:
    """https://host[:port] for an address, or None when it is not a plain https address."""
    u = urlparse((url or "").strip())
    if u.scheme != "https" or not u.netloc or u.username or u.password:
        return None
    if u.path not in ("", "/") or u.query or u.fragment:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?", u.netloc):
        return None
    return f"https://{u.netloc.lower()}"


def safe_next(path: str | None) -> str:
    p = (path or "/").strip()
    if not p.startswith("/") or p.startswith("//") or len(p) > 300 or not _NEXT_RE.match(p):
        return "/"
    return p


def sign_pass(secret: str, user: dict, next_path: str = "/", now: int | None = None) -> str:
    t = int(time.time()) if now is None else now
    claims = {
        "v": 1, "sub": str(user["plex_id"]),
        "email": user.get("email") or None, "username": user.get("username") or None,
        "title": user.get("title") or None, "thumb": user.get("thumb") or None,
        "iat": t, "exp": t + PASS_SECONDS, "nonce": secrets.token_urlsafe(24), "next": safe_next(next_path),
    }
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), f"v1.{payload}".encode(), hashlib.sha256).digest())
    return f"v1.{payload}.{sig}"


class Media:
    def __init__(self, store: Store):
        self.store = store

    def url(self) -> str:
        return media_origin(self.store.get_setting("media_url")) or DEFAULT_MEDIA_URL

    def has_secret(self) -> bool:
        return bool(self.store.get_setting("media_secret"))

    def enabled(self) -> bool:
        return self.store.get_setting("media_enabled") == "1" and self.has_secret()

    def frame_origin(self) -> str | None:
        """The origin the page may frame, only while Books & comics is on."""
        return self.url() if self.enabled() else None

    def settings(self) -> dict:
        return {"url": self.url(), "has_secret": self.has_secret(), "enabled": self.enabled()}

    def save(self, url: str | None, secret: str | None, enabled: bool | None) -> dict:
        if url is not None:
            origin = media_origin(url)
            if not origin:
                raise ValueError("Use the media site's https address with nothing after it, for example https://media.greystarnetworks.com")
            self.store.set_setting("media_url", origin)
        if secret is not None and secret.strip():
            s = secret.strip()
            if len(s) < 32 or not re.fullmatch(r"[A-Za-z0-9_\-]+", s):
                raise ValueError("That does not look like the secret the media site made. Copy it again from the media site.")
            self.store.set_setting("media_secret", s)
        if enabled is not None:
            if enabled and not self.has_secret():
                raise ValueError("Paste the shared secret from the media site first.")
            self.store.set_setting("media_enabled", "1" if enabled else "0")
        return self.settings()

    def forget_secret(self) -> None:
        self.store.set_setting("media_secret", None)
        self.store.set_setting("media_enabled", "0")

    def sign_in_url(self, user: dict, next_path: str = "/") -> str:
        secret = self.store.get_setting("media_secret") or ""
        token = sign_pass(secret, user, next_path)
        return f"{self.url()}/api/sso/telemetry?token={quote(token, safe='')}"
