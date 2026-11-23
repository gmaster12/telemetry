"""Emby members sign in with Emby Connect (or a local Emby password) and get the same member pages as Plex viewers."""
import asyncio
import json
import sys

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.emby import EmbyAuth, EmbyError, is_member_id, member_id

SERVER = "http://emby.test"
STEVE = {"Id": "523c18f4b39c4023beb16003a3a8c827", "Name": "steve@x.ca", "HasPassword": False,
         "ConnectUserName": "steve@x.ca", "ConnectLinkType": "LinkedUser", "Policy": {"IsDisabled": False}}
BOB = {"Id": "aa" * 16, "Name": "Bob", "HasPassword": False, "ConnectUserName": "bobconnect",
       "ConnectLinkType": "LinkedUser", "Policy": {"IsDisabled": False}}
LOCAL = {"Id": "bb" * 16, "Name": "Kim", "HasPassword": True, "Policy": {"IsDisabled": False}}
OFF = {"Id": "cc" * 16, "Name": "off@x.ca", "HasPassword": False, "ConnectUserName": "off@x.ca",
       "ConnectLinkType": "LinkedUser", "Policy": {"IsDisabled": True}}
CONNECT = {  # (name or email, password) -> Connect user
    ("steve@x.ca", "pw"): {"Id": "c1", "Name": "stevec", "Email": "Steve@X.ca"},
    ("bobconnect", "pw"): {"Id": "c2", "Name": "bobconnect", "Email": "bob@else.com"},
    ("stranger@x.ca", "pw"): {"Id": "c3", "Name": "stranger", "Email": "stranger@x.ca"},
    ("off@x.ca", "pw"): {"Id": "c4", "Name": "offc", "Email": "off@x.ca"},
}


def make(calls):
    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path == "/Users":
            assert req.headers["X-Emby-Token"] == "key"
            return httpx.Response(200, json=[STEVE, BOB, LOCAL, OFF])
        if req.url.path == "/Users/AuthenticateByName":
            b = json.loads(req.content)
            return httpx.Response(200, json={"AccessToken": "t"}) if (b["Username"], b["Pw"]) == ("Kim", "kpw") else httpx.Response(401)
        if req.url.path == "/Sessions/Logout":
            return httpx.Response(204)
        if req.url.path == "/service/user/authenticate":
            if not req.headers.get("X-Application"):
                return httpx.Response(403)
            f = dict(httpx.QueryParams(req.content.decode()))
            u = CONNECT.get((f["nameOrEmail"], f["rawpw"]))
            if u:
                return httpx.Response(200, json={"AccessToken": "ct", "User": u})
            return httpx.Response(401 if any(k[0] == f["nameOrEmail"] for k in CONNECT) else 404)
        return httpx.Response(404)
    return EmbyAuth(SERVER, "key", httpx.AsyncClient(transport=httpx.MockTransport(handler)), connect_url="https://connect.test")


def run(coro):
    return asyncio.run(coro)


def test_member_ids_never_meet_plex_ids_and_fit_the_media_site():
    m = member_id(STEVE["Id"])
    assert is_member_id(m) and m == member_id(STEVE["Id"]) and len(m) == 15 and int(m) < 2 ** 53
    assert not is_member_id("123456789") and member_id(BOB["Id"]) != m


def test_emby_connect_member_linked_to_our_server_gets_in():
    calls = []
    e = make(calls)
    assert run(e.authenticate("steve@x.ca", "pw")) == {"emby_id": STEVE["Id"], "name": "steve@x.ca", "email": "steve@x.ca"}
    # A Connect account linked by its Connect name (not an email) matches on the name.
    assert run(e.authenticate("bobconnect", "pw"))["emby_id"] == BOB["Id"]
    assert ("POST", "/Users/AuthenticateByName") not in calls   # no local password: Connect only


def test_wrong_password_unlinked_and_disabled_stay_out():
    e = make([])
    for name, pw, status in (("steve@x.ca", "bad", 401), ("nobody@x.ca", "pw", 401), ("stranger@x.ca", "pw", 403),
                             ("off@x.ca", "pw", 403), ("", "pw", 401)):
        with pytest.raises(EmbyError) as x:
            run(e.authenticate(name, pw))
        assert x.value.status == status, name


def test_local_emby_password_is_checked_by_the_server_and_its_session_closed():
    calls = []
    e = make(calls)
    assert run(e.authenticate("kim", "kpw"))["emby_id"] == LOCAL["Id"]
    assert ("POST", "/Sessions/Logout") in calls
    with pytest.raises(EmbyError):
        run(e.authenticate("Kim", "nope"))


def test_not_set_up_is_off():
    assert not EmbyAuth(None, None).enabled()
    with pytest.raises(EmbyError) as x:
        run(EmbyAuth("", "").authenticate("a", "b"))
    assert x.value.status == 409


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "steve@x.ca")   # an Emby sign-in must never become admin, even by email
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    monkeypatch.setenv("EMBY_URL", SERVER)
    monkeypatch.setenv("EMBY_API_KEY", "key")
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as M
    from app import auth
    auth._failures.clear()
    yield M
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


H = {"X-Requested-With": "greystar"}


def test_emby_sign_in_makes_a_viewer_member(app, monkeypatch):
    M = app
    e = make([])
    monkeypatch.setattr(M.emby, "http", e.http)
    monkeypatch.setattr(M.emby, "connect_url", e.connect_url)
    c = TestClient(M.app)
    assert c.get("/api/auth/options").json() == {"emby": True}
    r = c.post("/api/auth/emby", json={"username": "steve@x.ca", "password": "pw"}, headers=H)
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    me = c.get("/api/me").json()
    assert me["role"] == "viewer" and me["kind"] == "emby" and me["id"] == member_id(STEVE["Id"])
    u = M.directory.get_user(member_id(STEVE["Id"]))
    assert u["source"] == "emby" and u["emby_id"] == STEVE["Id"] and u["email"] == "steve@x.ca"
    assert c.get("/api/users").status_code == 403                     # not an admin
    assert c.get("/api/me/subscription").json()["status"] == "none"   # billing answers for members
    assert c.get("/api/books/config").json()["plex"] is True           # books treats them as a member
    # Signing in again keeps one row.
    TestClient(M.app).post("/api/auth/emby", json={"username": "steve@x.ca", "password": "pw"}, headers=H)
    assert M.store.db.execute("SELECT COUNT(*) FROM users WHERE source = 'emby'").fetchone()[0] == 1


def test_emby_sign_in_refusals(app, monkeypatch):
    M = app
    e = make([])
    monkeypatch.setattr(M.emby, "http", e.http)
    monkeypatch.setattr(M.emby, "connect_url", e.connect_url)
    c = TestClient(M.app)
    assert c.post("/api/auth/emby", json={"username": "steve@x.ca", "password": "bad"}, headers=H).status_code == 401
    assert c.post("/api/auth/emby", json={"username": "stranger@x.ca", "password": "pw"}, headers=H).status_code == 403
    # Access turned off on the dashboard sticks for an Emby member too.
    c.post("/api/auth/emby", json={"username": "bobconnect", "password": "pw"}, headers=H)
    M.directory.set_user_access(member_id(BOB["Id"]), disabled=True)
    assert TestClient(M.app).post("/api/auth/emby", json={"username": "bobconnect", "password": "pw"}, headers=H).status_code == 403


def test_without_settings_the_button_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    monkeypatch.delenv("EMBY_URL", raising=False)
    monkeypatch.delenv("EMBY_API_KEY", raising=False)
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as M
    c = TestClient(M.app)
    assert c.get("/api/auth/options").json() == {"emby": False}
    assert c.post("/api/auth/emby", json={"username": "a", "password": "b"}, headers=H).status_code == 404
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
