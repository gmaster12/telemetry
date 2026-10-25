"""Stopping a stream: admins only, Plex gets the session id and the viewer's message, off with ENABLE_ACTIONS=false."""
import sys

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture()
def M(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as main
    main.store.add_server({"name": "Atlas", "plex_url": "http://a:32400", "max_streams": 8}, "t")
    main.poller.reload()
    main.poller.cfg.sample_mode = "never"
    main.poller.streams = [{"id": "Atlas-42", "server": "Atlas", "session_id": "abc123", "user": "sable", "title": "Snowfall"}]
    yield main
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


def client(M, plex_id, email):
    from app.auth import new_session
    M.directory.upsert_user({"id": plex_id, "username": plex_id, "email": email}, shared=True)
    c = TestClient(M.app)
    c.cookies.set("gst_session", new_session(M.directory, "plex", plex_id))
    return c


H = {"X-Requested-With": "greystar"}


def test_admin_stops_a_stream_with_a_message(M, monkeypatch):
    sent = []

    async def terminate(self, session_id, reason):
        sent.append((self.server.name, session_id, reason))
    from app import plex
    monkeypatch.setattr(plex.PlexClient, "terminate", terminate)
    monkeypatch.setattr(type(M.poller.cfg), "use_sample", property(lambda self: False))
    a = client(M, "9001", "cluster@mail.com")
    assert a.get("/api/me").json()["actions"] is True
    r = a.post("/api/streams/Atlas-42/stop", json={"reason": "Server maintenance in 5 minutes"}, headers=H)
    assert r.status_code == 200, r.text
    assert sent == [("Atlas", "abc123", "Server maintenance in 5 minutes")]
    assert a.post("/api/streams/Atlas-99/stop", json={}, headers=H).status_code == 404


def test_viewers_cannot_stop_streams(M):
    v = client(M, "2001", "v@x.com")
    assert v.get("/api/me").json()["actions"] is False
    assert v.post("/api/streams/Atlas-42/stop", json={}, headers=H).status_code == 403


def test_can_be_turned_off(M, monkeypatch):
    monkeypatch.setenv("ENABLE_ACTIONS", "false")
    a = client(M, "9001", "cluster@mail.com")
    assert a.get("/api/me").json()["actions"] is False
    assert a.post("/api/streams/Atlas-42/stop", json={}, headers=H).status_code == 403
