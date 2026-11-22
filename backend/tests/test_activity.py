"""Activity log (admins only): who signed in, when, and what they did. Viewers never see it."""
import importlib
import sys
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

H = {"X-Requested-With": "greystar"}


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


def signed_in(M, plex_id, email, sections=None, ua="Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"):
    from app.auth import new_session
    M.directory.upsert_user({"id": plex_id, "username": "user" + plex_id, "email": email}, shared=True)
    if sections is not None:
        M.directory.set_user_access(plex_id, sections=sections, use_defaults=False, disabled=False)
    c = TestClient(M.app, headers={"User-Agent": ua})
    c.cookies.set("gst_session", new_session(M.directory, "plex", plex_id))
    return c


def fake_request(token="tok", ua="Mozilla/5.0 (iPhone) Version/17 Safari/604.1", path="/", method="GET", q=None):
    return SimpleNamespace(cookies={"gst_session": token}, headers={"user-agent": ua}, method=method,
                           url=SimpleNamespace(path=path), query_params=q or {}, client=SimpleNamespace(host="203.0.113.9"))


def test_viewers_cannot_read_the_activity_log(app):
    v = signed_in(app, "2001", "viewer@x.com", ["streams", "history", "requests"])
    for path in ["/api/settings/activity", "/api/settings/activity/summary", "/api/settings/activity/people?q=a"]:
        assert v.get(path).status_code == 403, path
    a = signed_in(app, "9001", "cluster@mail.com")
    for path in ["/api/settings/activity", "/api/settings/activity/summary", "/api/settings/activity/people?q=a"]:
        assert a.get(path).status_code == 200, path


def test_pages_are_recorded_once_and_shown_to_admins(app):
    v = signed_in(app, "2002", "v2@x.com", ["requests"])
    assert v.post("/api/activity/page", json={"path": "#/requests", "title": "Requests · Greystar Telemetry"}, headers=H).json()["ok"]
    assert not v.post("/api/activity/page", json={"path": "#/requests", "title": "Requests"}, headers=H).json()["ok"]  # same page again
    assert not v.post("/api/activity/page", json={"path": "javascript:alert(1)"}, headers=H).json()["ok"]
    assert v.post("/api/activity/page", json={"path": "#/history"}).status_code == 403  # CSRF header still needed
    a = signed_in(app, "9001", "cluster@mail.com")
    data = a.get("/api/settings/activity?q=user2002").json()
    [visit] = data["visits"]
    assert visit["who"]["name"] == "user2002" and visit["who"]["admin"] is False
    assert [e["label"] for e in visit["events"]] == ["Opened Requests"]
    assert visit["device"] == "Chrome · Windows"
    # who= narrows to one person exactly; another person's activity is not in it
    a.post("/api/activity/page", json={"path": "#/cleanup", "title": "Unwatched cleanup"}, headers=H)
    only = a.get("/api/settings/activity?who=plex:2002").json()["visits"]
    assert {v["who"]["id"] for v in only} == {"2002"}


def test_admin_changes_are_named(app):
    a = signed_in(app, "9001", "cluster@mail.com")
    app.directory.upsert_user({"id": "2003", "username": "nancy", "email": "n@x.com"}, shared=True)
    assert a.put("/api/settings/users/2003", json={"sections": ["requests"], "disabled": False}, headers=H).status_code == 200
    a.get("/api/users/2003/history")
    events = a.get("/api/settings/activity?who=plex:9001").json()["visits"][0]["events"]
    labels = [(e["label"], e["detail"]) for e in events]
    assert ("Changed a person's access", "nancy") in labels
    assert ("Looked up a person", "nancy") in labels
    # reading the log itself is not an action
    assert not any("activity" in (e["detail"] or "") for e in events)


def test_search_as_you_type_is_one_row(app):
    act = app.activity
    p = SimpleNamespace(kind="plex", subject="2004", name="sam")
    for q in ["st", "stra", "stranger things"]:
        act.search(fake_request("t4"), p, q)
    rows = app.store.db.execute("SELECT detail FROM activity_log WHERE type = 'search'").fetchall()
    assert [r["detail"] for r in rows] == ["“stranger things”"]


def test_visits_split_on_sign_in_and_quiet(app):
    act = app.activity
    who = ("plex", "2005", "kim")
    act._insert(1000, who, "s1", "login", "Signed in with Plex", device="Safari · iPhone")
    act._insert(1100, who, "s1", "page", "Opened Home")
    act._insert(1200, who, "s1", "logout", "Signed out")
    act._insert(1200 + 3600, who, "s2", "page", "Opened Requests", device="Chrome · Windows")  # came back an hour later
    data = act.visits(who="plex:2005", now=10_000)
    first, second = data["visits"][1], data["visits"][0]
    assert first["how"] == "login" and first["ended"] == "logout" and len(first["events"]) == 3 and first["minutes"] == 3
    assert second["how"] == "return" and second["ended"] == "idle"
    assert second["newDevice"] is True and first["newDevice"] is False
    # paging: the next page starts before the oldest visit shown
    page = act.visits(who="plex:2005", limit=1, now=10_000)
    assert len(page["visits"]) == 1 and page["next"] == second["start"]
    assert act.visits(who="plex:2005", before=page["next"], now=10_000)["visits"][0]["start"] == 1000


def test_old_sign_ins_are_recovered_once(app):
    from app.auth import new_session
    app.directory.upsert_user({"id": "2006", "username": "lee", "email": "l@x.com"}, shared=True)
    new_session(app.directory, "plex", "2006")
    app.store.set_setting("activity_recovered", None)
    assert app.activity.recover() == 1
    assert app.activity.recover() == 0
    row = app.store.db.execute("SELECT * FROM activity_log WHERE source = 'recovered'").fetchone()
    assert row["who_name"] == "lee" and row["type"] == "login" and len(row["session"]) == 12


def test_unknown_local_username_is_not_kept(app):
    c = TestClient(app.app)
    assert c.post("/api/auth/local", json={"username": "MyS3cretPassw0rd", "password": "x"}, headers=H).status_code == 401
    row = app.store.db.execute("SELECT * FROM activity_log WHERE type = 'denied'").fetchone()
    assert row["who_name"] == "unknown" and "MyS3cret" not in str(dict(row))


def test_summary_counts_days_and_requests(app):
    act = app.activity
    act._insert(1_795_000_000, ("plex", "2007", "ana"), "s7", "login", "Signed in with Plex")
    app.seerr.log_request(SimpleNamespace(kind="plex", subject="2007", name="ana", email="a@x.com"), 5, 77, "movie", 603,
                          {"title": "The Matrix", "year": "1999"}, None, False, "approved", at=1_795_000_100)
    s = act.summary(who="plex:2007", tz_offset_min=-360, now=1_795_100_000)
    assert s["totals"]["signins"] == 1 and s["totals"]["actions"] == 1
    assert sum(d["events"] for d in s["days"]) == 2 and s["days"][-1]["day"] == "2026-11-19"
    req = [e for v in act.visits(who="plex:2007")["visits"] for e in v["events"] if e["type"] == "request"]
    assert req[0]["detail"] == "The Matrix (1999)"


def test_device_names():
    from app.activity import device_of
    assert device_of("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Version/17.0 Mobile Safari/604.1") == "Safari · iPhone"
    assert device_of("Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/120.0 Safari/537.36 Edg/120.0") == "Edge · Windows"
    assert device_of("Mozilla/5.0 (Linux; Android 9; AFTMM) Chrome/120 Silk/120 Safari/537.36") == "Silk · Fire TV"
    assert device_of(None) is None
