"""Logins and permissions.

- Plex accounts: anyone you share a server with (or who has streamed from one)
  can sign in with Plex. They see only the sections you grant.
- The Plex account whose email matches ADMIN_PLEX_EMAIL is the only Plex
  administrator.
- Local admin logins (username and password, not Plex accounts) are always
  administrators. Create the first one with `python -m app.tools.admin create`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field

from fastapi import Request

from .config import env
from .store import Directory, Store

SESSION_COOKIE = "gst_session"
SESSION_TTL = 30 * 86400

SECTIONS = [
    {"key": "streams", "label": "Live streams", "hint": "Every live stream, with viewer names and what they are watching"},
    {"key": "mystreams", "label": "My streams", "hint": "Stream cards for what is playing on their own account right now"},
    {"key": "servers", "label": "Servers", "hint": "Server status, stream slots, throughput and capacity"},
    {"key": "bandwidth", "label": "Bandwidth", "hint": "Total bandwidth over the last hour"},
    {"key": "viewing", "label": "When people watch", "hint": "Busiest hours of the week"},
    {"key": "history", "label": "My history", "hint": "Their own playback history"},
    {"key": "requests", "label": "Requests", "hint": "Find movies and shows and request them through Seerr"},
]
SECTION_KEYS = [s["key"] for s in SECTIONS]
DEFAULT_SECTIONS = ["mystreams", "history", "requests"]


def admin_plex_email() -> str | None:
    v = env("ADMIN_PLEX_EMAIL")
    return v.strip().lower() if v else None


# ---------- passwords ----------
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, salt_b64, h_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except Exception:
        return False


def password_problem(pw: str) -> str | None:
    if len(pw) < 12:
        return "Use at least 12 characters."
    return None


# ---------- principals ----------
@dataclass
class Principal:
    kind: str  # "plex" or "local"
    subject: str  # plex id or local admin id
    name: str
    role: str  # "admin" or "viewer"
    sections: list[str] = field(default_factory=list)
    email: str | None = None
    thumb: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can(self, section: str) -> bool:
        return self.is_admin or section in self.sections

    def public(self) -> dict:
        return {"kind": self.kind, "id": self.subject, "name": self.name, "role": self.role,
                "sections": SECTION_KEYS if self.is_admin else self.sections, "email": self.email, "thumb": self.thumb}


def add_section_once(store: Store, key: str, flag: str) -> None:
    """A section that arrived after people already had access saved. Everyone gets it once (Glenn's call):
    the stored defaults and every custom list. Turning it off afterwards sticks."""
    if store.get_setting(flag):
        return
    raw = store.get_setting("default_sections")
    if raw is not None:
        try:
            cur = json.loads(raw)
            if key not in cur:
                store.set_setting("default_sections", json.dumps(cur + [key]))
        except ValueError:
            pass
    with store.lock:
        rows = store.db.execute("SELECT plex_id, sections FROM users WHERE sections IS NOT NULL").fetchall()
        for r in rows:
            try:
                cur = json.loads(r["sections"])
            except ValueError:
                continue
            if key not in cur:
                store.db.execute("UPDATE users SET sections = ? WHERE plex_id = ?", (json.dumps(cur + [key]), r["plex_id"]))
        store.db.commit()
    store.set_setting(flag, "1")


def add_requests_section(store: Store) -> None:
    add_section_once(store, "requests", "requests_section_added")


def add_mystreams_section(store: Store) -> None:
    add_section_once(store, "mystreams", "mystreams_section_added")


def default_sections(store: Store) -> list[str]:
    raw = store.get_setting("default_sections")
    if raw is None:
        return list(DEFAULT_SECTIONS)
    try:
        return [s for s in json.loads(raw) if s in SECTION_KEYS]
    except ValueError:
        return list(DEFAULT_SECTIONS)


def user_sections(store: Store, user: dict) -> list[str]:
    if user.get("sections"):
        try:
            return [s for s in json.loads(user["sections"]) if s in SECTION_KEYS]
        except ValueError:
            pass
    return default_sections(store)


def plex_principal(store: Store, user: dict) -> Principal | None:
    if user.get("disabled"):
        return None
    email = (user.get("email") or "").lower() or None
    is_admin = bool(email and email == admin_plex_email())
    return Principal(kind="plex", subject=user["plex_id"], name=user.get("title") or user.get("username") or "Plex user",
                     role="admin" if is_admin else "viewer", sections=[] if is_admin else user_sections(store, user),
                     email=email, thumb=user.get("thumb"))


# ---------- sessions ----------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(directory: Directory, kind: str, subject: str) -> str:
    token = secrets.token_urlsafe(32)
    directory.add_session(_hash_token(token), kind, subject, SESSION_TTL)
    directory.mark_login(kind, subject)
    return token


def end_session(directory: Directory, token: str | None) -> None:
    if token:
        directory.delete_session(_hash_token(token))


def principal_for(request: Request, store: Store, directory: Directory) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = directory.get_session(_hash_token(token))
    if not sess:
        return None
    if sess["kind"] == "local":
        a = directory.get_admin_by_id(int(sess["subject"]))
        if not a:
            return None
        return Principal(kind="local", subject=str(a["id"]), name=a["username"], role="admin")
    user = directory.get_user(sess["subject"])
    return plex_principal(store, user) if user else None


def set_cookie(response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or env("COOKIE_SECURE", "auto") == "true"
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=secure, samesite="lax", path="/")


# ---------- login throttling ----------
_failures: dict[str, list[float]] = {}


def throttled(key: str, limit: int = 8, window: int = 300) -> bool:
    now = time.time()
    hits = [t for t in _failures.get(key, []) if now - t < window]
    _failures[key] = hits
    return len(hits) >= limit


def record_failure(key: str) -> None:
    _failures.setdefault(key, []).append(time.time())
