"""Access rules and headers: viewers cannot reach admin data; sign-in limits cannot be dodged."""
import importlib
import sys

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as M
    from app import auth
    auth._failures.clear()
    yield M
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


def signed_in(M, plex_id, email, sections=None):
    from app.auth import new_session
    M.directory.upsert_user({"id": plex_id, "username": plex_id, "email": email}, shared=True)
    if sections is not None:
        M.directory.set_user_access(plex_id, sections=sections, use_defaults=False, disabled=False)
    c = TestClient(M.app)
    c.cookies.set("gst_session", new_session(M.directory, "plex", plex_id))
    return c


ADMIN_ONLY_GET = ["/api/users", "/api/users/2002", "/api/users/2002/history", "/api/settings", "/api/settings/billing",
                  "/api/settings/billing/unmatched", "/api/settings/access", "/api/settings/summary", "/api/settings/requests",
                  "/api/status"]


def test_viewers_get_403_from_every_admin_endpoint(app):
    v = signed_in(app, "2001", "viewer@x.com", ["streams", "history", "requests"])
    for path in ADMIN_ONLY_GET:
        assert v.get(path).status_code == 403, path
    h = {"X-Requested-With": "greystar"}
    assert v.post("/api/settings/admins", json={"username": "evil", "password": "x" * 20}, headers=h).status_code == 403
    assert v.put("/api/settings/users/2001", json={"sections": ["streams"], "disabled": False}, headers=h).status_code == 403
    assert v.put("/api/settings/access/defaults", json={"sections": []}, headers=h).status_code == 403
    assert v.get("/api/me").json()["role"] == "viewer"


def test_admin_still_gets_in(app):
    a = signed_in(app, "9001", "Cluster@Mail.com")
    assert a.get("/api/users").status_code == 200 and a.get("/api/settings/access").status_code == 200


def test_poster_needs_streams_or_history(app):
    v = signed_in(app, "2003", "r@x.com", ["requests"])
    assert v.get("/api/poster/1/123").status_code == 403


def test_security_headers_and_no_cors(app):
    c = TestClient(app.app)
    r = c.get("/healthz", headers={"Origin": "https://evil.example"})
    csp = r.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "unsafe-eval" not in csp
    assert "camera=()" in r.headers["permissions-policy"]
    assert "access-control-allow-origin" not in r.headers and "access-control-allow-credentials" not in r.headers
    pre = c.options("/api/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in pre.headers


def test_sign_in_limit_cannot_be_dodged_with_forwarded_for(app):
    from app.auth import hash_password
    app.directory.add_admin("ops", hash_password("correct horse battery"))
    c = TestClient(app.app)
    h = {"X-Requested-With": "greystar"}
    codes = []
    for i in range(12):
        # the proxy appends the real address last; the attacker controls only what comes before it
        codes.append(c.post("/api/auth/local", json={"username": "ops", "password": "wrong"},
                            headers={**h, "X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"}).status_code)
    assert codes[:8] == [401] * 8 and set(codes[8:]) == {429}


def test_guesses_from_many_addresses_still_hit_the_username_limit(app):
    from app.auth import hash_password
    app.directory.add_admin("ops", hash_password("correct horse battery"))
    c = TestClient(app.app)
    codes = [c.post("/api/auth/local", json={"username": "ops", "password": "wrong"},
                    headers={"X-Requested-With": "greystar", "X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(22)]
    assert codes[:20] == [401] * 20 and codes[20:] == [429, 429]
