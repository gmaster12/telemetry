"""Request log: admins see every request made through the site, who made it and when, and where it stands now."""
import asyncio
import datetime as dt
import time

from tests import mock_seerr as M
from tests.test_requests import app_for, env, principal  # noqa: F401  (env is a fixture)


def test_requests_are_logged_with_who_and_when_and_their_state_now(env):
    from app import request_log
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "title": "Sable Reyes", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    before = int(time.time())
    assert c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 27205}).json()["ok"]
    assert c.post("/api/requests/request", json={"mediaType": "tv", "mediaId": 1396, "seasons": [1, 2]}).json()["ok"]
    assert c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 603}).status_code in (200, 404)

    rows, err = asyncio.run(request_log.build(svc, d))
    assert err is None
    by_title = {r["title"]: r for r in rows}
    inc, bb = by_title["Inception"], by_title["Breaking Bad"]
    assert inc["who"]["name"] == "Sable Reyes" and inc["who"]["username"] == "sable" and inc["who"]["email"] == "s@x.com"
    assert inc["at"] >= before and inc["kind"] == "movie" and inc["state"] == "waiting" and inc["label"] == "Waiting for approval"
    assert bb["seasons"] == [1, 2] and bb["kind"] == "tv"
    assert rows[0]["at"] >= rows[-1]["at"]  # newest first

    # Seerr moves on: approved and downloading, then available; one declined; a canceled one stays in the log as canceled
    rq = {r["media"]["tmdbId"]: r for r in M.state["requests"]}
    rq[27205]["status"], rq[27205]["media_status"] = 2, 5
    rq[1396]["status"] = 3
    svc.clear_cache()
    rows, _ = asyncio.run(request_log.build(svc, d))
    by_title = {r["title"]: r for r in rows}
    assert by_title["Inception"]["state"] == "available" and by_title["Inception"]["group"] == "available"
    assert by_title["Breaking Bad"]["state"] == "declined" and by_title["Breaking Bad"]["tone"] == "danger"

    rq[1396]["status"] = 1
    svc.clear_cache()
    assert c.delete(f"/api/requests/request/{rq[1396]['id']}").status_code == 200
    rows, _ = asyncio.run(request_log.build(svc, d))
    assert {r["title"]: r for r in rows}["Breaking Bad"]["state"] == "canceled"

    # filters and counts
    assert [r["title"] for r in rows if request_log.matches(r, "breaking", "", "all")] == ["Breaking Bad"]
    assert [r["title"] for r in rows if request_log.matches(r, "reyes", "movie", "available")] == ["Inception"]
    assert request_log.counts(rows)["ended"] == 1

    # CSV in the viewer's time zone
    csv = request_log.to_csv(rows, dt.timezone(dt.timedelta(hours=-6)))
    assert csv.splitlines()[0].startswith("Date,Time,Day,Name,Username,Email") and "Inception" in csv and "s@x.com" in csv


def test_seerr_down_still_shows_the_log(env):
    from app import request_log
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 27205})
    store.set_setting("seerr_key", "wrong-key")  # Seerr refuses: the log still shows, state unknown
    svc.clear_cache()
    rows, err = asyncio.run(request_log.build(svc, d))
    assert err and rows[0]["state"] == "unknown" and rows[0]["title"] == "Inception"


def test_earlier_requests_import_once_and_only_since_linking(env):
    from app import request_log
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    asyncio.run(svc.seerr_user(principal("2001", "s@x.com", "sable"), d))  # linked now
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    M.state["requests"] += [
        {"id": 90, "type": "movie", "status": 2, "is4k": False, "by": 5, "created": stamp(time.time() - 30 * 86400), "media": {"tmdbId": 603}, "seasons": []},
        {"id": 91, "type": "movie", "status": 1, "is4k": False, "by": 5, "created": stamp(time.time() + 5), "media": {"tmdbId": 27205}, "seasons": []},
        {"id": 92, "type": "movie", "status": 1, "is4k": False, "by": 1, "created": stamp(time.time() + 5), "media": {"tmdbId": 27205}, "seasons": []},
    ]
    out = asyncio.run(request_log.import_earlier(svc, d, svc.title_of))
    assert out["added"] == 1  # 90 is from before they were linked, 92 is someone else's
    again = asyncio.run(request_log.import_earlier(svc, d, svc.title_of))
    assert again["added"] == 0
    rows, _ = asyncio.run(request_log.build(svc, d))
    assert [(r["request"], r["source"], r["who"]["name"]) for r in rows] == [(91, "imported", "sable")]
