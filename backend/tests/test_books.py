"""Books & comics: the media site in a frame, signed in with a one-time pass made from the shared secret."""
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from tests.test_security import app, signed_in  # noqa: F401  (fixture)

SECRET = "s3cr3t-made-by-the-media-site-0123456789abcdef"
H = {"X-Requested-With": "greystar"}


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(token: str, secret: str) -> dict:
    """What the media site does with the pass (server/telemetry_sso.ts there)."""
    v, payload, sig = token.split(".")
    assert v == "v1"
    want = hmac.new(secret.encode(), f"v1.{payload}".encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(want, _b64d(sig))
    return json.loads(_b64d(payload))


def admin(M):
    return signed_in(M, "9001", "cluster@mail.com")


def turn_on(M):
    a = admin(M)
    r = a.put("/api/settings/books", json={"url": "https://media.greystarnetworks.com/", "secret": SECRET, "enabled": True}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_settings_keep_the_secret_write_only_and_encrypted(app):
    got = turn_on(app)
    assert got == {"url": "https://media.greystarnetworks.com", "has_secret": True, "enabled": True}
    assert SECRET not in json.dumps(admin(app).get("/api/settings/books").json())
    raw = app.store.db.execute("SELECT value FROM settings WHERE key = 'media_secret'").fetchone()[0]
    assert raw != SECRET and SECRET not in raw
    a = admin(app)
    assert a.put("/api/settings/books", json={"url": "http://media.example"}, headers=H).status_code == 422
    assert a.put("/api/settings/books", json={"url": "https://media.example/path"}, headers=H).status_code == 422
    assert a.put("/api/settings/books", json={"secret": "short"}, headers=H).status_code == 422
    assert a.delete("/api/settings/books", headers=H).json() == {"url": "https://media.greystarnetworks.com", "has_secret": False, "enabled": False}


def test_cannot_turn_on_without_a_secret(app):
    r = admin(app).put("/api/settings/books", json={"enabled": True}, headers=H)
    assert r.status_code == 422


def test_viewers_cannot_reach_the_settings(app):
    v = signed_in(app, "2001", "v@x.com", ["books"])
    assert v.get("/api/settings/books").status_code == 403
    assert v.put("/api/settings/books", json={"enabled": False}, headers=H).status_code == 403


def test_frame_is_allowed_only_while_on(app):
    c = TestClient(app.app)
    assert "frame-src 'none'" in c.get("/healthz").headers["content-security-policy"]
    turn_on(app)
    csp = c.get("/healthz").headers["content-security-policy"]
    assert "frame-src https://media.greystarnetworks.com;" in csp and "frame-ancestors 'none'" in csp


def test_a_viewer_gets_a_signed_one_time_pass_for_themselves(app):
    turn_on(app)
    v = signed_in(app, "2001", "Reader@X.com", ["books"])
    cfg = v.get("/api/books/config").json()
    assert cfg == {"enabled": True, "origin": "https://media.greystarnetworks.com", "plex": True}
    r = v.get("/api/books/link", params={"next": "/audiobooks/12"})
    assert r.status_code == 200
    url = urlparse(r.json()["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://media.greystarnetworks.com/api/sso/telemetry"
    claims = verify(parse_qs(url.query)["token"][0], SECRET)
    assert claims["sub"] == "2001" and claims["email"] == "Reader@X.com" and claims["next"] == "/audiobooks/12"
    assert 0 < claims["exp"] - claims["iat"] <= 60 and abs(claims["iat"] - time.time()) < 5
    second = verify(parse_qs(urlparse(v.get("/api/books/link").json()["url"]).query)["token"][0], SECRET)
    assert second["nonce"] != claims["nonce"] and second["next"] == "/"
    odd = verify(parse_qs(urlparse(v.get("/api/books/link", params={"next": "//evil.example"}).json()["url"]).query)["token"][0], SECRET)
    assert odd["next"] == "/"


def test_books_is_a_section_on_by_default_and_can_be_turned_off(app):
    turn_on(app)
    from app.auth import DEFAULT_SECTIONS
    assert "books" in DEFAULT_SECTIONS
    off = signed_in(app, "2002", "o@x.com", ["history"])
    assert off.get("/api/books/link").status_code == 403
    assert off.get("/api/books/config").status_code == 403


def test_local_admin_logins_get_no_pass(app):
    turn_on(app)
    from app.auth import new_session, hash_password
    aid = app.directory.add_admin("ops", hash_password("x" * 20))
    c = TestClient(app.app)
    c.cookies.set("gst_session", new_session(app.directory, "local", str(aid)))
    r = c.get("/api/books/link")
    assert r.status_code == 409 and "Plex" in r.json()["detail"]


def test_not_set_up_says_so(app):
    v = signed_in(app, "2001", "v@x.com", ["books"])
    assert v.get("/api/books/config").json()["enabled"] is False
    assert v.get("/api/books/link").status_code == 409
