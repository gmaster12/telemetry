"""Live streams globe and Settings, Dashboard Cards.

The globe payload must never carry a name, a Plex id, an IP or the viewer's own coordinates: only a random id, the
time, a city label and the city's centre. Card choices are saved per admin account and default to every card on.
"""
import asyncio
import sys
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.globe import GeoDB, Globe, place_from, public_ip

H = {"X-Requested-With": "greystar"}

DALLAS = {"city": {"names": {"en": "Dallas"}}, "subdivisions": [{"names": {"en": "Texas"}}],
          "country": {"iso_code": "US", "names": {"en": "United States"}},
          "location": {"latitude": 32.7831, "longitude": -96.8067}}
TORONTO = {"city": {"names": {"en": "Toronto"}}, "subdivisions": [{"names": {"en": "Ontario"}}],
           "country": {"iso_code": "CA", "names": {"en": "Canada"}},
           "location": {"latitude": 43.7001, "longitude": -79.4163}}


class FakeReader:
    def __init__(self, table):
        self.table = table

    def get(self, ip):
        return self.table.get(ip)

    def close(self):
        pass


def fake_geo(tmp_path):
    g = GeoDB(tmp_path / "geo")
    g.reader = FakeReader({"8.8.8.8": DALLAS, "24.114.1.1": TORONTO})
    return g


def test_labels_are_city_level_and_rounded():
    assert place_from(DALLAS) == {"label": "Dallas, TX", "lat": 32.8, "lon": -96.8}
    assert place_from(TORONTO) == {"label": "Toronto, CA", "lat": 43.7, "lon": -79.4}
    no_city = {**DALLAS, "city": {}}
    assert place_from(no_city)["label"] == "Texas, TX"
    assert place_from({"country": {"iso_code": "US"}}) is None  # no location, no ping


def test_only_public_addresses_are_looked_up():
    for ip in ("192.168.1.20", "10.0.0.5", "127.0.0.1", "100.64.3.2", "fe80::1", "", None, "not-an-ip"):
        assert public_ip(ip) is None, ip
    assert public_ip("8.8.8.8") == "8.8.8.8"


def test_events_carry_no_identity(tmp_path):
    g = Globe(fake_geo(tmp_path), lambda ts: 7)
    row = {"_ip": "8.8.8.8", "user": "alice", "user_id": "123", "title": "Dune", "server": "Atlas"}
    assert g.started(row) is not None
    assert g.started({"_ip": "192.168.1.9", "user": "bob"}) is None  # LAN: counted elsewhere, no ping
    assert g.since(None) == {"events": [], "cursor": 1}  # page load: no pings for what is already playing
    ev = g.since(0)["events"]
    assert len(ev) == 1 and set(ev[0]) == {"id", "at", "label", "lat", "lon"}
    flat = repr(ev)
    for secret in ("alice", "123", "8.8.8.8", "Dune", "Atlas", "32.78"):
        assert secret not in flat
    assert g.last_hour() == 7


def test_playing_now_dots(tmp_path):
    g = Globe(fake_geo(tmp_path), lambda ts: 0)
    a = {"server": "Atlas", "session_key": "a", "_ip": "8.8.8.8", "method": "Transcode", "user": "alice", "user_id": "123", "title": "Dune"}
    b = {"server": "Beta", "session_key": "b", "_ip": "24.114.1.1", "method": "Direct play", "user": "bob"}
    lan = {"server": "Atlas", "session_key": "c", "_ip": "192.168.1.4", "method": "Direct play"}
    g.playing([b])                                   # already playing when we started: a dot, no ping
    ev = g.started(a)                                # a new stream: its dot shares the ping's id
    g.playing([a, b, lan])
    dots = {d["label"]: d for d in g.active()}
    assert set(dots) == {"Dallas, TX", "Toronto, CA"}  # LAN gets no dot
    assert dots["Dallas, TX"]["id"] == ev["id"] and dots["Dallas, TX"]["type"] == "transcode"
    assert dots["Toronto, CA"]["type"] == "direct" and dots["Toronto, CA"]["server"] == "Beta"
    assert set(dots["Dallas, TX"]) == {"id", "label", "lat", "lon", "type", "server"}
    for secret in ("alice", "bob", "123", "8.8.8.8", "24.114", "Dune", "32.78"):
        assert secret not in repr(g.active())
    g.playing([a], online={"Beta"})                  # b stopped on Beta (answered); Atlas kept
    assert [d["label"] for d in g.active()] == ["Dallas, TX"]
    g.playing([], online={"Beta"})                   # Atlas did not answer: its dot stays
    assert len(g.active()) == 1
    g.playing([], online={"Atlas"})
    assert g.active() == []


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    monkeypatch.setenv("SAMPLE_MODE", "never")
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as M
    from app import auth
    auth._failures.clear()
    M.geo.reader = FakeReader({"8.8.8.8": DALLAS, "24.114.1.1": TORONTO})
    yield M
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


def plex_client(M, plex_id, email, sections=None):
    from app.auth import new_session
    M.directory.upsert_user({"id": plex_id, "username": plex_id, "email": email}, shared=True)
    if sections is not None:
        M.directory.set_user_access(plex_id, sections=sections, use_defaults=False, disabled=False)
    c = TestClient(M.app)
    c.cookies.set("gst_session", new_session(M.directory, "plex", plex_id))
    return c


def local_admin(M, name="ops"):
    from app.auth import hash_password, new_session
    aid = M.directory.add_admin(name, hash_password("x" * 20))
    c = TestClient(M.app)
    c.cookies.set("gst_session", new_session(M.directory, "local", str(aid)))
    return c


def row(session, ip, rating="10", user="alice", uid="123"):
    return {"server": "Atlas", "server_id": 1, "session_key": session, "rating_key": rating, "user": user, "user_id": uid,
            "title": "Dune", "kind": "Movie", "state": "Playing", "location": "WAN", "_ip": ip, "_art_path": None}


def test_new_sessions_ping_once_and_the_ip_never_leaves(app):
    M = app
    M.poller.cfg = SimpleNamespace(servers=[SimpleNamespace(enabled=True, name="Atlas", id=1)], poll_seconds=10, use_sample=False)
    rounds = [
        [row("a", "8.8.8.8")],
        [row("a", "8.8.8.8"), row("b", "24.114.1.1", user="bob", uid="456")],        # a keeps playing, b starts
        [row("a", "8.8.8.8", rating="11"), row("b", "24.114.1.1", user="bob", uid="456")],  # next episode in a: not new
        [row("a", "8.8.8.8", rating="11"), row("b", "24.114.1.1"), row("c", "192.168.1.4")],  # LAN start: no ping
    ]

    async def fake_poll(s):
        M.poller.server_state["Atlas"] = {"online": True}
        return [dict(r) for r in rounds[0]]

    M.poller._poll_server = fake_poll
    a = plex_client(M, "9001", "cluster@mail.com")
    first = a.get("/api/globe").json()
    assert first["events"] == []
    cursor = first["cursor"]
    labels = []
    while rounds:
        asyncio.run(M.poller.poll_plex())
        assert all("_ip" not in r for r in M.poller.streams)
        got = a.get(f"/api/globe?after={cursor}").json()
        cursor = got["cursor"]
        labels += [e["label"] for e in got["events"]]
        body = repr(got)
        for secret in ("8.8.8.8", "24.114", "192.168", "alice", "bob", "123", "456", "Dune"):
            assert secret not in body
        rounds.pop(0)
    assert labels == ["Dallas, TX", "Toronto, CA"]
    now = a.get("/api/globe").json()
    assert sorted(d["label"] for d in now["active"]) == ["Dallas, TX", "Toronto, CA"]
    assert now["servers"] == ["Atlas"]
    assert a.get("/api/globe").json()["last_hour"] >= 3
    assert "_ip" not in repr(a.get("/api/streams").json())


def test_viewers_cannot_see_the_globe_or_card_settings(app):
    v = plex_client(app, "2001", "viewer@x.com", ["streams", "viewing"])
    assert v.get("/api/globe").status_code == 403
    assert v.get("/api/settings/cards").status_code == 403
    assert v.put("/api/settings/cards", json={"hidden": ["globe"]}, headers=H).status_code == 403
    assert v.get("/api/me").json()["cards_hidden"] == []


def test_card_choices_are_per_admin_and_default_to_all(app):
    a = plex_client(app, "9001", "cluster@mail.com")
    b = local_admin(app)
    got = a.get("/api/settings/cards").json()
    assert got["hidden"] == [] and {c["key"] for c in got["cards"]} >= {"viewing", "globe", "capacity", "streams"}
    r = a.put("/api/settings/cards", json={"hidden": ["globe", "alerts", "nonsense"]}, headers=H)
    assert r.status_code == 200 and r.json()["hidden"] == ["globe", "alerts"]
    assert a.get("/api/me").json()["cards_hidden"] == ["globe", "alerts"]
    assert b.get("/api/me").json()["cards_hidden"] == []      # another admin is untouched
    assert b.get("/api/settings/cards").json()["hidden"] == []
    a.put("/api/settings/cards", json={"hidden": []}, headers=H)
    assert a.get("/api/me").json()["cards_hidden"] == []
