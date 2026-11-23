"""Web Push: notifications on people's phones and computers, even when the site is closed.

A member turns it on per device (web/push.js, the Phone & computer alerts panel). The browser gives a subscription
(an https address at its push service plus two keys); it is kept in `push_subs`. When the Notifier posts something new
for them (notify.py `Notifier.post`), it is queued here and sent at the end of that check (`flush`).

Sending follows the standards, with nothing but `cryptography` and httpx:
- RFC 8292 (VAPID): the server signs a short JWT (ES256) for the push service. The key pair is made once and kept in
  settings (`push_vapid_private` sealed like other secrets, `push_vapid_public` in the clear for the page).
- RFC 8291 / 8188 (aes128gcm): the message is encrypted for that one browser; the push service cannot read it.
A push service answering 404 or 410 means the browser dropped the subscription: it is deleted.

Only addresses at the known push services are accepted (Google/Chrome/Edge-Android, Mozilla, Apple, Microsoft), so a
subscription cannot make this server call anywhere else.

What each person gets is chosen per kind (`CHOICES`, saved per person in `push_prefs`). Admin notices (the shared
ADMINS list) go to devices of people who are admins when it is sent.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
from typing import Callable
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger("telemetry.push")

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  who_kind TEXT NOT NULL,
  who_id TEXT NOT NULL,
  device TEXT,
  created INTEGER NOT NULL,
  last_ok INTEGER,
  last_error TEXT,
  fails INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS push_subs_who ON push_subs (who_kind, who_id);
CREATE TABLE IF NOT EXISTS push_prefs (
  who TEXT PRIMARY KEY,
  kinds TEXT NOT NULL
);
"""

ADMINS = "admins"   # the Notifier's shared admin audience (notify.ADMINS)
# What a person can choose, and which notification kinds each choice covers. `default`: on until they change it.
CHOICES = [
    {"key": "ready", "label": "Ready to watch or read", "kinds": ["ready"], "default": True},
    {"key": "replies", "label": "Replies to problems you reported", "kinds": ["reply"], "default": True},
    {"key": "problems", "label": "A request could not be downloaded", "kinds": ["problem"], "default": True},
    {"key": "billing", "label": "Payment problems", "kinds": ["billing"], "default": True},
    {"key": "started", "label": "A request started downloading", "kinds": ["started"], "default": False},
    {"key": "admin", "label": "Admin alerts (servers, payments, reported problems)", "kinds": ["*admin"], "default": True, "admin": True},
]
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com",
              "notify.windows.com", "push.apple.com", "android.googleapis.com")
TTL = 24 * 3600
MAX_FAILS = 20


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def allowed_endpoint(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and len(url) < 1000 and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291 message encryption (aes128gcm, one record)."""
    ua_pub_bytes = unb64u(p256dh)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    as_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    secret = as_key.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(unb64u(auth), secret, b"WebPush: info\x00" + ua_pub_bytes + as_pub, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!I", 4096) + bytes([len(as_pub)]) + as_pub + body


class Push:
    def __init__(self, store, http: httpx.AsyncClient | None = None, is_admin: Callable[[str, str], bool] | None = None,
                 contact: str = "mailto:admin@greystarnetworks.com"):
        self.store = store
        self.db = store.db
        self.http = http
        self.is_admin = is_admin or (lambda kind, wid: False)
        self.contact = contact
        self.outbox: list[tuple[str, dict]] = []
        with store.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()
        self._key: ec.EllipticCurvePrivateKey | None = None

    # ---------- keys ----------
    def _private(self) -> ec.EllipticCurvePrivateKey:
        if self._key is None:
            pem = self.store.get_setting("push_vapid_private")
            if pem:
                self._key = serialization.load_pem_private_key(pem.encode(), password=None)  # type: ignore[assignment]
            else:
                self._key = ec.generate_private_key(ec.SECP256R1())
                self.store.set_setting("push_vapid_private", self._key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
                self.store.set_setting("push_vapid_public", None)
        return self._key  # type: ignore[return-value]

    def public_key(self) -> str:
        pub = self.store.get_setting("push_vapid_public")
        if not pub:
            pub = b64u(self._private().public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
            self.store.set_setting("push_vapid_public", pub)
        return pub

    def _vapid(self, endpoint: str, now: int) -> str:
        u = urlparse(endpoint)
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": now + 12 * 3600, "sub": self.contact}, separators=(",", ":")).encode())
        der = self._private().sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"vapid t={head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={self.public_key()}"

    # ---------- subscriptions and choices ----------
    def subscribe(self, kind: str, wid: str, endpoint: str, p256dh: str, auth: str, device: str | None) -> None:
        if not allowed_endpoint(endpoint):
            raise ValueError("That is not a push address this site can use.")
        if len(unb64u(p256dh)) != 65 or len(unb64u(auth)) != 16:
            raise ValueError("The browser sent keys this site cannot use.")
        now = int(time.time())
        with self.store.lock:
            self.db.execute(
                "INSERT INTO push_subs (endpoint, p256dh, auth, who_kind, who_id, device, created) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, who_kind = excluded.who_kind, "
                "who_id = excluded.who_id, device = excluded.device, fails = 0, last_error = NULL",
                (endpoint, p256dh, auth, kind, str(wid), (device or "")[:120], now))
            self.db.commit()

    def unsubscribe(self, kind: str, wid: str, endpoint: str) -> bool:
        with self.store.lock:
            n = self.db.execute("DELETE FROM push_subs WHERE endpoint = ? AND who_kind = ? AND who_id = ?", (endpoint, kind, str(wid))).rowcount
            self.db.commit()
        return n > 0

    def devices(self, kind: str, wid: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT id, endpoint, device, created, last_ok, last_error FROM push_subs WHERE who_kind = ? AND who_id = ? ORDER BY created",
            (kind, str(wid)))]

    def choices(self, who: str, admin: bool) -> dict[str, bool]:
        r = self.db.execute("SELECT kinds FROM push_prefs WHERE who = ?", (who,)).fetchone()
        saved = json.loads(r["kinds"]) if r else {}
        return {c["key"]: bool(saved.get(c["key"], c["default"])) for c in CHOICES if admin or not c.get("admin")}

    def set_choices(self, who: str, values: dict[str, bool]) -> None:
        keep = {c["key"]: bool(values[c["key"]]) for c in CHOICES if c["key"] in values}
        with self.store.lock:
            self.db.execute("INSERT INTO push_prefs (who, kinds) VALUES (?, ?) ON CONFLICT(who) DO UPDATE SET kinds = excluded.kinds",
                            (who, json.dumps(keep)))
            self.db.commit()

    # ---------- sending ----------
    def queue(self, who: str, kind: str, title: str, body: str | None, href: str | None, tag: str) -> None:
        """Called by the Notifier for something new. Sent at the end of the check (`flush`)."""
        url = "/" + href if href and href.startswith("#/") else "/"
        self.outbox.append((who, {"title": title, "body": body or "", "url": url, "tag": tag[:100], "kind": kind}))
        del self.outbox[:-200]

    def _targets(self, who: str, kind: str) -> list[dict]:
        if who == ADMINS:
            subs = [dict(r) for r in self.db.execute("SELECT * FROM push_subs")]
            subs = [s for s in subs if self.is_admin(s["who_kind"], s["who_id"])]
            want = "admin"
        else:
            wid = who.split(":", 1)[-1]
            subs = [dict(r) for r in self.db.execute("SELECT * FROM push_subs WHERE who_kind IN ('plex', 'emby') AND who_id = ?", (wid,))]
            want = next((c["key"] for c in CHOICES if kind in c["kinds"]), None)
            if want is None:
                return []
        out = []
        for s in subs:
            admin = self.is_admin(s["who_kind"], s["who_id"])
            if self.choices(f"{s['who_kind']}:{s['who_id']}", admin).get(want):
                out.append(s)
        return out

    async def send(self, sub: dict, message: dict, now: int | None = None) -> int:
        """One push to one device. Returns the push service's HTTP status (0: could not reach it)."""
        now = int(now or time.time())
        data = encrypt(json.dumps(message, separators=(",", ":")).encode(), sub["p256dh"], sub["auth"])
        headers = {"TTL": str(TTL), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                   "Authorization": self._vapid(sub["endpoint"], now), "Urgency": "normal"}
        if message.get("tag"):
            headers["Topic"] = b64u(message["tag"].encode())[:32]
        try:
            r = await (self.http or httpx.AsyncClient(timeout=15)).post(sub["endpoint"], content=data, headers=headers, timeout=15)
            status, err = r.status_code, (None if r.status_code < 300 else f"HTTP {r.status_code}: {r.text[:150]}")
        except httpx.HTTPError as e:
            status, err = 0, str(e)[:150]
        with self.store.lock:
            if status in (404, 410):   # the browser dropped this subscription
                self.db.execute("DELETE FROM push_subs WHERE id = ?", (sub["id"],))
            elif err:
                self.db.execute("UPDATE push_subs SET fails = fails + 1, last_error = ? WHERE id = ?", (err, sub["id"]))
                self.db.execute("DELETE FROM push_subs WHERE id = ? AND fails >= ?", (sub["id"], MAX_FAILS))
            else:
                self.db.execute("UPDATE push_subs SET last_ok = ?, fails = 0, last_error = NULL WHERE id = ?", (now, sub["id"]))
            self.db.commit()
        if err:
            log.info("Push to one device failed: %s", err)
        return status

    async def flush(self) -> int:
        """Send everything queued. Returns how many pushes went out."""
        box, self.outbox = self.outbox, []
        sent = 0
        for who, msg in box:
            for sub in self._targets(who, msg["kind"]):
                if 200 <= await self.send(sub, msg) < 300:
                    sent += 1
        return sent
