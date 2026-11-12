"""The green bar's downloads (Glenn's pick A, Nov 12 2026): only this person's own requests, downloading now or ready in
the last half hour, in plain words."""
import time

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests import mock_arr as A
from tests import mock_seerr as M
from tests.test_cleanup import cx, iso  # noqa: F401  (fixture)
from tests.test_requests import env  # noqa: F401  (fixture)


class FakeMedia:
    def __init__(self, items):
        self.items = items

    def enabled(self):
        return True

    async def member_requests(self, plex_id):
        assert plex_id == "2001"
        return self.items


def client(cx, media=None):
    from app.auth import Principal
    from app.requests_api import make_router
    store, d, svc, arr, cl, _ = cx
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    who = Principal(kind="plex", subject="2001", name="sable", role="viewer", sections=["requests", "books"], email="s@x.com")
    a = FastAPI()

    def need(request, section):
        if section not in who.sections:
            raise HTTPException(403, "no")
        return who
    a.include_router(make_router(svc, d, need, media, cl, arr))
    return TestClient(a)


def mine(rid, tmdb, media_status, by=5, **media):
    M.state["requests"].append({"id": rid, "type": "movie", "status": 2, "is4k": False, "by": by, "created": iso(time.time() - 3600),
                                "media": {"tmdbId": tmdb}, "seasons": [], "media_status": media_status,
                                "media_extra": {"id": tmdb + 90000, **media}})


def test_own_downloads_and_just_finished_books_only(cx):
    mine(1, 27205, 3)                                   # downloading, mine
    mine(2, 603, 3, by=1)                               # downloading, someone else's: never shown
    A.state["movies"] += [{"id": 7, "tmdbId": 27205, "hasFile": False, "isAvailable": True},
                          {"id": 8, "tmdbId": 603, "hasFile": False, "isAvailable": True}]
    A.state["queue"]["radarr"] += [
        {"id": 1, "movieId": 7, "downloadId": "a", "size": 1000, "sizeleft": 540, "status": "downloading", "trackedDownloadState": "downloading",
         "timeleft": "00:04:00", "title": "Inception.2010.1080p.WEB-DL-GROUP", "downloadClient": "qbit"},
        {"id": 2, "movieId": 8, "downloadId": "b", "size": 1000, "sizeleft": 100, "status": "downloading", "trackedDownloadState": "downloading"}]
    now = time.time()
    books = [
        {"key": "audiobook:9", "kind": "audiobook", "title": "Four", "state": "work", "label": "Downloading 46%", "progress": 46},
        {"key": "ebook:3", "kind": "ebook", "title": "Dune", "state": "ready", "label": "Available", "open": "/ebooks/3", "doneAt": iso(now - 300)},
        {"key": "ebook:4", "kind": "ebook", "title": "Old", "state": "ready", "label": "Available", "open": "/ebooks/4", "doneAt": iso(now - 7200)},
        {"key": "watch:ebook:5", "kind": "ebook", "title": "Theirs", "state": "work", "label": "Downloading 10%", "progress": 10, "watching": True},
        {"key": "comic:2", "kind": "comic", "title": "Saga", "state": "work", "label": "Searching", "progress": None},
    ]
    res = client(cx, FakeMedia(books)).get("/api/requests/downloads")
    body = res.json()
    got = {x["key"]: x for x in body["items"]}
    assert set(got) == {"movie:27205", "audiobook:9", "ebook:3"}
    assert got["movie:27205"]["pct"] == 46 and got["movie:27205"]["minutes"] == 4 and not got["movie:27205"]["ready"]
    assert got["audiobook:9"]["pct"] == 46
    assert got["ebook:3"]["ready"] and got["ebook:3"]["open"] == "#/books/ebooks/3"
    assert body["items"][-1]["key"] == "ebook:3"           # ready ones after what is still downloading
    for secret in ("GROUP", "qbit", "Inception.2010"):
        assert secret not in res.text


def test_nothing_without_radarr_or_books(cx):
    store, d, svc, arr, cl, _ = cx
    for k in ("radarr", "sonarr"):
        store.set_setting(k + "_url", None)
    body = client(cx).get("/api/requests/downloads").json()
    assert body["items"] == [] and body["sources"] == {"seerr": "off", "books": "off"}
