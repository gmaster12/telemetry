"""My requests as one list: movies and shows from Seerr merged with comics, ebooks and audiobooks from the media site."""
import hashlib
import hmac

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests import mock_seerr as M
from tests.test_requests import env  # noqa: F401  (fixture)

SECRET = "s3cr3t-made-by-the-media-site-0123456789abcdef"


def req(rid, kind, tmdb, status, created, by=5, **extra):
    M.state["requests"].append({"id": rid, "type": kind, "status": status, "is4k": False, "by": by, "created": created,
                                "media": {"tmdbId": tmdb}, "seasons": [], **extra})


def book(key, title, state, at, kind="ebook", open_path=None):
    return {"key": key, "kind": kind, "id": int(key.split(":")[1]), "title": title, "sub": "Someone", "created": at, "sortAt": at,
            "state": state, "label": state.title(), "note": None, "progress": None, "open": open_path}


class FakeMedia:
    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = items or [], fail, 0

    def enabled(self):
        return True

    async def member_requests(self, plex_id):
        self.calls += 1
        if self.fail:
            raise RuntimeError("media site down")
        assert plex_id == "2001"
        return self.items


def client(env, media, sections=("requests", "books"), kind="plex"):
    from app.auth import Principal
    from app.requests_api import make_router
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    who = Principal(kind=kind, subject="2001", name="sable", role="viewer", sections=list(sections), email="s@x.com")
    a = FastAPI()

    def need(request, section):
        if section not in who.sections:
            raise HTTPException(403, "no")
        return who
    a.include_router(make_router(svc, d, need, media))
    return TestClient(a)


def test_one_list_newest_first_with_paging(env):
    req(1, "movie", 603, 5, "2026-11-01T10:00:00.000Z", media_status=5)       # Available
    req(2, "movie", 27205, 4, "2026-11-04T10:00:00.000Z")                    # Failed
    req(3, "movie", 157336, 3, "2026-11-06T10:00:00.000Z")                   # Declined: left out
    req(4, "tv", 1396, 2, "2026-11-08T10:00:00.000Z", media_status=3, downloading=[{"title": "x"}])
    media = FakeMedia([book("comic:9", "Monstress", "follow", "2026-11-09T10:00:00.000Z", kind="comic", open_path="/series/4"),
                       book("ebook:7", "Foundation", "work", "2026-11-05T10:00:00.000Z"),
                       book("audiobook:3", "Dune Messiah", "ready", "2026-11-02T10:00:00.000Z", kind="audiobook")])
    c = client(env, media)
    got = c.get("/api/requests/everything").json()
    assert got["sources"] == {"seerr": "ok", "books": "ok"}
    assert [(x["key"], x["state"]) for x in got["items"]] == [
        ("comic:9", "follow"), ("seerr:4", "work"), ("ebook:7", "work"), ("seerr:2", "problem"), ("audiobook:3", "ready"), ("seerr:1", "ready")]
    assert got["items"][1]["label"] == "Downloading"
    assert got["items"][0]["open"] == "#/books/series/4"
    assert got["items"][-1]["open"] == "#/requests/movie/603"
    assert got["items"][3]["canRetry"] and not got["items"][2]["canRetry"]
    assert not got["more"]
    old = c.get("/api/requests/everything?order=asc").json()
    assert old["items"][0]["key"] == "seerr:1" and old["items"][-1]["key"] == "comic:9"


def test_paging_uses_both_sides_without_gaps(env, monkeypatch):
    from app import requests_api
    monkeypatch.setattr(requests_api, "EVERYTHING_PAGE", 3)
    for i in range(5):
        req(100 + i, "movie", 603, 5, f"2026-11-0{i + 1}T10:00:00.000Z", media_status=5)
    media = FakeMedia([book(f"ebook:{i}", f"Book {i}", "ready", f"2026-11-0{i + 1}T12:00:00.000Z") for i in reversed(range(5))])
    c = client(env, media)
    seen, cursor = [], "0.0"
    for _ in range(6):
        got = c.get(f"/api/requests/everything?cursor={cursor}").json()
        seen += [x["key"] for x in got["items"]]
        cursor = got["cursor"]
        if not got["more"]:
            break
    assert len(seen) == 10 and len(set(seen)) == 10
    assert seen[:3] == ["ebook:4", "seerr:104", "ebook:3"]


def test_one_side_down_still_shows_the_other(env):
    req(1, "movie", 603, 5, "2026-11-01T10:00:00.000Z", media_status=5)
    got = client(env, FakeMedia(fail=True)).get("/api/requests/everything").json()
    assert got["sources"] == {"seerr": "ok", "books": "down"}
    assert [x["key"] for x in got["items"]] == ["seerr:1"]
    store, _, svc = env
    store.set_setting("seerr_url", "http://127.0.0.1:9")  # nothing listens there
    svc._cache.clear()
    svc.http = httpx.AsyncClient(timeout=1)
    got = client(env, FakeMedia([book("ebook:7", "Foundation", "work", "2026-11-05T10:00:00.000Z")])).get("/api/requests/everything").json()
    assert got["sources"]["seerr"] == "down" and [x["key"] for x in got["items"]] == ["ebook:7"]


def test_books_only_for_plex_sign_ins_with_the_section(env):
    req(1, "movie", 603, 5, "2026-11-01T10:00:00.000Z", media_status=5)
    media = FakeMedia([book("ebook:7", "Foundation", "work", "2026-11-05T10:00:00.000Z")])
    got = client(env, media, sections=("requests",)).get("/api/requests/everything").json()
    assert got["sources"]["books"] == "off" and media.calls == 0


def test_no_other_peoples_names_or_emails(env):
    req(1, "movie", 603, 5, "2026-11-01T10:00:00.000Z", media_status=5)
    body = client(env, FakeMedia()).get("/api/requests/everything").text
    for secret in ("cluster@mail.com", "secret-admin-token", "s@x.com"):
        assert secret not in body


def test_signed_call_matches_what_the_media_site_checks(env):
    """The media site rebuilds HMAC-SHA256(secret, "v1.<ts>.<nonce>.<METHOD> <path?query>") (server/telemetry_feed.ts)."""
    import base64

    from app.media import sign_call
    sig = sign_call(SECRET, "get", "/api/telemetry/requests?sub=2001", now=1_800_000_000, nonce="abcdefgh12")
    v, ts, nonce, mac = sig.split(".")
    want = hmac.new(SECRET.encode(), b"v1.1800000000.abcdefgh12.GET /api/telemetry/requests?sub=2001", hashlib.sha256).digest()
    assert (v, ts, nonce) == ("v1", "1800000000", "abcdefgh12")
    assert mac == base64.urlsafe_b64encode(want).rstrip(b"=").decode()
