"""Failed requests can be tried again or removed by the person who made them; approved ones still waiting say why."""
import datetime as dt

from tests import mock_seerr as M
from tests.test_requests import app_for, env, principal  # noqa: F401  (fixture)

H = {"X-Requested-With": "greystar"}


def req(rid, kind, tmdb, status, by=5, seasons=None, **extra):
    M.state["requests"].append({"id": rid, "type": kind, "status": status, "is4k": False, "by": by, "created": "2026-11-01T00:00:00Z",
                                "media": {"tmdbId": tmdb}, "seasons": seasons or [], **extra})


def viewer(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    return app_for(svc, d, principal("2001", "s@x.com", "sable"))


def test_failed_request_can_be_retried_and_removed_by_its_owner(env):
    c = viewer(env)
    req(201, "movie", 603, 4)
    req(202, "movie", 27205, 4)
    req(203, "movie", 157336, 4, by=1)  # someone else's
    got = c.get("/api/requests/attention").json()
    assert [x["id"] for x in got["failed"]] == [202, 201]
    assert all(x["canRetry"] and x["canRemove"] for x in got["failed"])
    assert c.post("/api/requests/request/201/retry", headers=H).json()["ok"]
    assert M.state["retried"] == [201]
    assert c.delete("/api/requests/request/202?failed=true", headers=H).status_code == 200
    assert 202 not in [r["id"] for r in M.state["requests"]]
    # not theirs, or not failed: refused
    assert c.post("/api/requests/request/203/retry", headers=H).status_code in (403, 404)
    req(204, "movie", 603, 2)
    assert c.post("/api/requests/request/204/retry", headers=H).status_code == 409
    assert c.delete("/api/requests/request/204?failed=true", headers=H).status_code == 409


def test_waiting_requests_say_why(env):
    c = viewer(env)
    soon = (dt.date.today() + dt.timedelta(days=17)).isoformat()
    M.MOVIES[1001]["home"] = [{"type": 4, "release_date": soon + "T00:00:00.000Z"}]
    M.SHOWS[1396]["air"] = {5: soon}
    req(301, "movie", 1001, 2, media_status=3)                 # out on digital in 17 days
    req(302, "movie", 603, 2, media_status=3)                  # released long ago, no copy found
    req(303, "tv", 1396, 2, seasons=[5], media_status=3)       # season 5 not aired yet
    req(304, "movie", 27205, 2, media_status=3, downloading=[{"title": "x"}])  # downloading: not waiting
    req(305, "movie", 157336, 1, media_status=2)               # pending approval: not waiting
    try:
        w = {x["id"]: x["wait"] for x in c.get("/api/requests/attention").json()["waiting"]}
    finally:
        M.MOVIES[1001].pop("home", None)
        M.SHOWS[1396].pop("air", None)
    assert w[301] == {"reason": "unreleased", "date": soon, "what": "home"}
    assert w[302] == {"reason": "searching"}
    assert w[303]["reason"] == "unreleased" and w[303]["date"] == soon
    assert 304 not in w and 305 not in w
