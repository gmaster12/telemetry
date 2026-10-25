"""Requests through Seerr: each person acts as their own Seerr user, found, imported or made once."""
import asyncio
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests import mock_seerr as M


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    from app.secrets import SecretBox
    from app.seerr import Requests
    from app.store import Directory, Store
    M.reset()
    store = Store(str(tmp_path / "t.db"), SecretBox())
    d = Directory(store)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=M.app))
    svc = Requests(store, http)
    store.set_setting("seerr_url", "http://seerr.test")
    store.set_setting("seerr_key", M.KEY)
    return store, d, svc


def principal(pid, email, name, role="viewer"):
    from app.auth import Principal
    return Principal(kind="plex", subject=pid, name=name, role=role, sections=["requests"], email=email)


def app_for(svc, d, who):
    from app.requests_api import make_router
    a = FastAPI()

    def need(request, section):
        if section not in who.sections and who.role != "admin":
            raise HTTPException(403, "no")
        return who
    a.include_router(make_router(svc, d, need))
    return TestClient(a)


def test_key_is_saved_encrypted(env):
    store, _, _ = env
    raw = store.db.execute("SELECT value FROM settings WHERE key = 'seerr_key'").fetchone()["value"]
    assert M.KEY not in raw and store.get_setting("seerr_key") == M.KEY


def test_people_are_matched_imported_or_made_once(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "S@x.com"}, shared=True)
    d.upsert_user({"id": "2002", "username": "kit", "email": "k@x.com"}, shared=True)
    d.upsert_user({"id": "2003", "username": "wren", "email": "w@x.com"}, shared=True)
    d.upsert_user({"id": "2004", "username": "noemail"}, shared=True)

    async def go():
        a = await svc.seerr_user(principal("2001", "s@x.com", "sable"), d)       # already in Seerr: matched by email
        b = await svc.seerr_user(principal("2002", "k@x.com", "kit"), d)         # Seerr's Plex account shares with them: imported
        c = await svc.seerr_user(principal("2003", "w@x.com", "wren"), d)        # not visible to Seerr's Plex check: made by email
        again = await svc.seerr_user(principal("2003", "w@x.com", "wren"), d)    # remembered, no new account
        from app.auth import Principal
        local = await svc.seerr_user(Principal(kind="local", subject="1", name="ops", role="admin"), d)
        with pytest.raises(Exception) as e:
            await svc.seerr_user(principal("2004", None, "noemail"), d)
        return a, b, c, again, local, e.value
    a, b, c, again, local, err = asyncio.run(go())
    assert a == 5 and b and c and again == c and local is None
    assert {l["how"] for l in svc.links()} == {"matched", "imported", "created"}
    assert len([u for u in M.state["users"] if u["email"] == "w@x.com"]) == 1
    assert "no email" in err.message


def test_requests_are_made_as_the_person_and_cannot_be_widened(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    me = c.get("/api/requests/me").json()
    assert me["connected"] and me["user"]["id"] == 5 and me["can"]["movie4k"] and not me["can"]["advanced"] and not me["can"]["manage"]
    r = c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 27205, "is4k": False, "serverId": 3, "rootFolder": "/x"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    body = M.state["bodies"][-1]
    assert "serverId" not in body and "rootFolder" not in body and "userId" not in body
    assert any(x["path"] == "/api/v1/request" and x["method"] == "POST" and x["user"] == 5 for x in M.calls)
    # TV needs seasons; "all" passes through
    assert c.post("/api/requests/request", json={"mediaType": "tv", "mediaId": 1396}).status_code == 422
    assert c.post("/api/requests/request", json={"mediaType": "tv", "mediaId": 1396, "seasons": [1, 2]}).json()["ok"]
    mine = c.get("/api/requests/mine").json()["items"]
    assert [m["title"] for m in mine] == ["Breaking Bad", "Inception"] and mine[0]["seasons"] == [1, 2] and mine[0]["canCancel"]
    dr = c.delete(f"/api/requests/request/{mine[0]['id']}"); assert dr.status_code == 200, dr.text
    # nobody else's email or token reaches the browser
    for path in ["/api/requests/movie/27205", "/api/requests/tv/1399", "/api/requests/row?type=2", "/api/requests/mine"]:
        txt = c.get(path).text
        assert "@x.com" not in txt and "cluster@mail.com" not in txt and "secret-admin-token" not in txt, path


def test_tv_page_shows_each_season(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    t = c.get("/api/requests/tv/1399").json()
    by = {s["number"]: s for s in t["seasons"]}
    assert by[1]["status"] == "available" and by[3]["status"] == "partial" and by[4]["status"] == "unknown"
    assert t["media"]["status"] == "partial" and t["tvdbId"] == 121361
    c.post("/api/requests/request", json={"mediaType": "tv", "mediaId": 1399, "seasons": [4, 5]})
    by = {s["number"]: s for s in c.get("/api/requests/tv/1399").json()["seasons"]}
    assert by[4]["requested"] == "pending" and by[6]["requested"] is None


def test_discover_rows_and_search(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    home = c.get("/api/requests/home").json()
    assert [r["type"] for r in home["rows"]][:4] == [1, 2, 3, 4] and 12 not in [r["type"] for r in home["rows"]]
    assert home["rows"][0]["title"] == "Recently Added" and len(home["networks"]) > 10
    recent = c.get("/api/requests/row", params={"type": 1}).json()["items"]
    assert recent[0]["title"] == "The Matrix" and recent[0]["status"] == "available"
    trending = c.get("/api/requests/row", params={"type": 4}).json()
    assert trending["more"] and {i["kind"] for i in trending["items"]} == {"movie", "tv"}
    s = c.get("/api/requests/search", params={"q": "keanu"}).json()["items"]
    assert any(i["kind"] == "person" for i in s)
    assert c.get("/api/requests/row", params={"type": 6}).json()["genres"][0]["name"] == "Action"


def test_section_can_be_turned_off(env):
    store, d, svc = env
    from app.auth import Principal
    who = Principal(kind="plex", subject="2001", name="sable", role="viewer", sections=["history"], email="s@x.com")
    assert app_for(svc, d, who).get("/api/requests/home").status_code == 403


def test_everyone_gets_requests_once(env):
    store, d, svc = env
    from app.auth import add_requests_section, default_sections
    store.set_setting("default_sections", json.dumps(["history"]))
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    d.set_user_access("2001", sections=["streams"], use_defaults=False, disabled=False)
    add_requests_section(store)
    assert default_sections(store) == ["history", "requests"]
    assert json.loads(d.get_user("2001")["sections"]) == ["streams", "requests"]
    store.set_setting("default_sections", json.dumps(["history"]))  # Glenn turns it off later: it stays off
    add_requests_section(store)
    assert default_sections(store) == ["history"]


def test_search_sends_spaces_and_apostrophes_the_way_seerr_wants(env):
    from app.seerr import query_string
    assert query_string({"query": "The 'Burbs (1989)!*", "page": 1}) == "query=The%20%27Burbs%20%281989%29%21%2A&page=1"
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    for q in ["the burbs", "The 'Burbs", "the+burbs (1989)"]:
        r = c.get("/api/requests/search", params={"q": q})
        assert r.status_code == 200, (q, r.text)
    assert any(i["title"] == "The 'Burbs" for i in c.get("/api/requests/search", params={"q": "the 'burbs"}).json()["items"])
