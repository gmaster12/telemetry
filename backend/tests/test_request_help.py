"""Glenn's request rules for movies and shows: one Try again then Notify admin, Notify me too without names,
book actions sent on to the media site, and searches that found nothing (app/request_help.py)."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests import mock_seerr as M
from tests.test_requests import env  # noqa: F401  (fixture)

REQUEST = 32


def person(env, pid, email, name, sections=("requests", "books"), media=None):
    from app.auth import Principal
    from app.requests_api import make_router
    store, d, svc = env
    d.upsert_user({"id": pid, "username": name, "email": email}, shared=True)
    who = Principal(kind="plex", subject=pid, name=name, role="viewer", sections=list(sections), email=email)
    a = FastAPI()

    def need(request, section):
        if section not in who.sections:
            raise HTTPException(403, "no")
        return who
    a.include_router(make_router(svc, d, need, media))
    return TestClient(a)


def failed(rid, by=5, tmdb=27205):
    M.state["requests"].append({"id": rid, "type": "movie", "status": 4, "is4k": False, "by": by, "created": "2026-11-04T10:00:00.000Z",
                                "media": {"tmdbId": tmdb}, "seasons": []})


def test_one_try_again_then_notify_admin_until_handled(env):
    store, d, svc = env
    failed(7)
    c = person(env, "2001", "s@x.com", "sable")
    assert c.post("/api/requests/request/7/notify").status_code == 409  # try again first
    row = lambda: next(x for x in c.get("/api/requests/everything").json()["items"] if x["key"] == "seerr:7")
    assert row()["canRetry"] and not row()["canNotify"]
    assert c.post("/api/requests/request/7/retry").status_code == 200
    M.state["requests"][0]["status"] = 4  # failed again
    svc.clear_cache()
    again = c.post("/api/requests/request/7/retry")
    assert again.status_code == 409 and "Notify admin" in again.json()["detail"]
    assert not row()["canRetry"] and row()["canNotify"]
    assert c.get("/api/requests/attention").json()["failed"][0]["canNotify"]
    assert c.post("/api/requests/request/7/notify").status_code == 200
    assert row()["notified"] and not row()["canNotify"]
    assert [f["request_id"] for f in svc.help.needs_you()] == [7] and svc.help.needs_count() == 1
    assert svc.help.needs_you()[0]["title"] == "Inception"
    svc.help.mark_handled(7)
    assert svc.help.needs_count() == 0 and row()["canNotify"]  # handled; if it fails again they can tell the admin again


def test_someone_elses_request_is_never_named_and_can_be_followed(env):
    store, d, svc = env
    M.state["users"].append({"id": 7, "email": "k@x.com", "plexUsername": "kit", "username": None, "displayName": "Kit Secret",
                             "permissions": REQUEST, "userType": 1, "plexToken": "", "avatar": "/a7.png"})
    M.state["requests"].append({"id": 40, "type": "movie", "status": 2, "is4k": False, "by": 7, "created": "2026-11-05T10:00:00.000Z",
                                "media": {"tmdbId": 27205}, "seasons": []})
    c = person(env, "2001", "s@x.com", "sable")
    detail = c.get("/api/requests/movie/27205")
    assert "Kit Secret" not in detail.text and "kit" not in detail.text.lower().replace("kit secret", "")
    q = detail.json()["requests"][0]
    assert q["by"] is None and not q["mine"] and not q["watching"]
    assert c.post("/api/requests/watch/40").status_code == 200
    assert c.get("/api/requests/movie/27205").json()["requests"][0]["watching"]
    got = c.get("/api/requests/everything").json()
    item = next(x for x in got["items"] if x["key"] == "seerr-watch:40")
    assert item["watching"] and item["note"].startswith("Requested by another member") and item["title"] == "Inception"
    assert "Kit" not in str(got) and "k@x.com" not in str(got)
    assert got["cursor"].count(".") == 2
    assert c.delete("/api/requests/watch/40").json() == {"ok": True, "watching": False}
    assert not [x for x in c.get("/api/requests/everything").json()["items"] if x.get("watching")]
    # your own request cannot be "followed"
    M.state["requests"].append({"id": 41, "type": "movie", "status": 2, "is4k": False, "by": 5, "created": "2026-11-06T10:00:00.000Z",
                                "media": {"tmdbId": 157336}, "seasons": []})
    assert c.post("/api/requests/watch/41").status_code == 409


def test_book_actions_go_to_the_media_site_signed_and_refresh_the_list(env):
    calls = []

    class FakeMedia:
        def enabled(self):
            return True

        async def call(self, method, path):
            calls.append((method, path))
            return {"ok": True}

        def forget_member(self, pid):
            calls.append(("forget", pid))

        async def member_requests(self, pid):
            return [{"key": "ebook:7", "kind": "ebook", "id": 7, "title": "Foundation", "sub": None, "created": "2026-11-05T10:00:00.000Z",
                     "sortAt": "2026-11-05T10:00:00.000Z", "state": "problem", "label": "Failed", "note": None, "progress": None,
                     "open": None, "canRetry": True, "canNotify": False, "notified": False, "canCancel": True, "canStop": False}]
    c = person(env, "2001", "s@x.com", "sable", media=FakeMedia())
    assert c.post("/api/requests/books/ebook/7/retry").json() == {"ok": True}
    assert calls == [("POST", "/api/telemetry/requests/ebook/7/retry?sub=2001"), ("forget", "2001")]
    assert c.post("/api/requests/books/ebook/7/delete").status_code == 404
    assert c.post("/api/requests/books/movie/7/retry").status_code == 404
    item = next(x for x in c.get("/api/requests/everything").json()["items"] if x["key"] == "ebook:7")
    assert item["canRetry"] and item["canCancel"]
    no_books = person(env, "2001", "s@x.com", "sable", sections=("requests",), media=FakeMedia())
    assert no_books.post("/api/requests/books/ebook/7/retry").status_code == 409


def test_searches_that_found_nothing_keep_only_the_words(env):
    store, d, svc = env
    c = person(env, "2001", "s@x.com", "sable")
    for q in ("Zzyzx Road", "zzyzx  road", "matrix"):
        c.get("/api/requests/search", params={"q": q})
    rows = svc.help.misses(7)
    assert [(r["term"], r["count"]) for r in rows] == [("zzyzx road", 2)]
    assert "sable" not in str(rows) and "2001" not in str(rows)
