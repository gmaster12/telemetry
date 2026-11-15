"""One place decides where a request stands (Glenn's pick O1A, Nov 15 2026), and the notifier and Unwatched cleanup ask
Plex itself the way the title pages do (pick 4A): "is ready" and the cleanup clock no longer wait for Seerr's scan."""
import asyncio
import time

import pytest

from app import request_state as rs
from tests.test_cleanup import cx, iso, seerr_req, track  # noqa: F401  (fixtures)
from tests.test_notify import notifier, principal, req, tick
from tests.test_requests import env  # noqa: F401  (fixture)


def q(status=2, media=3, kind="movie", is4k=False, seasons=(), media_seasons=(), downloading=False, **m):
    return {"status": status, "type": kind, "is4k": is4k, "seasons": [{"seasonNumber": n} for n in seasons],
            "media": {"status": media, "status4k": 1, "downloadStatus": [{}] if downloading else [],
                      "seasons": [{"seasonNumber": n, "status": st} for n, st in media_seasons], **m}}


@pytest.mark.parametrize("req_, kw, stage", [
    (q(status=1), {}, "waiting"),
    (q(status=3), {}, "declined"),
    (q(status=4), {}, "failed"),
    (q(media=5), {}, "ready"),
    (q(status=5, media=3), {}, "ready"),
    (q(), {}, "approved"),
    (q(), {"wait": {"reason": "unreleased"}}, "release"),
    (q(downloading=True), {}, "downloading"),
    (q(), {"arr_stage": "queued"}, "downloading"),
    (q(), {"arr_stage": "imported"}, "importing"),
    (q(), {"arr_stage": "imported", "on_plex": True}, "ready"),                     # Plex has it before Seerr does
    (q(kind="tv", media=4, seasons=[2], media_seasons=[(1, 5)]), {}, "partial"),  # season 1 is there, season 2 asked
    (q(kind="tv", media=4, seasons=[1], media_seasons=[(1, 5)]), {}, "ready"),    # every asked-for season is there
    (q(status=1, kind="tv", media=4, seasons=[2], media_seasons=[(1, 5)]), {}, "waiting"),
    (None, {}, "unknown"),
])
def test_one_stage_for_every_request(req_, kw, stage):
    assert rs.stage_of(req_, **kw) == stage


def test_every_view_takes_its_words_from_the_one_table():
    from app import request_log
    from app.requests_api import public_status, unify_seerr
    assert request_log.STATES["available"] == rs.WORDS["ready"]["log"]
    assert all(request_log.STATES[request_log.LOG_ID.get(st, st)] == rs.WORDS[st]["log"] for st in rs.STAGES)
    row = {"id": 1, "kind": "movie", "tmdbId": 9, "stage": "downloading"}
    assert unify_seerr(row, None)["label"] == rs.WORDS["downloading"]["member"][1]
    assert unify_seerr({**row, "stage": "approved"}, {"reason": "airing"})["label"] == "Airing"
    assert public_status is rs.public_words
    words = rs.vocabulary()
    assert {"filters", "tones", "logGroups", "request", "media", "seerrFilters", "hub", "episodes"} <= set(words)
    hub_stages = [s for g in words["hub"] for s in g["stages"]]
    assert len(hub_stages) == len(set(hub_stages)) and set(hub_stages) <= set(rs.STAGES)


def test_shared_words_from_the_media_site_map_onto_the_same_stages():
    assert rs.from_shared("work", "Waiting for approval") == "waiting"
    assert rs.from_shared("work", "Downloading 40%") == "downloading"
    assert rs.from_shared("work", "Adding to library") == "importing"
    assert rs.from_shared("ready", "Available") == "ready"
    assert rs.from_shared("problem", "Failed") == "failed"
    assert rs.from_shared("follow", "Following") == "following"


class FakeArr:
    def __init__(self, stage):
        self.stage = stage

    def configured(self, kind=None):
        return True

    async def movie_progress(self, tmdb, is4k=False):
        return {"stage": self.stage, "word": "x"}

    async def imported_file(self, tmdb, is4k=False):
        return 1234, "Inception.2010.mkv", "Inception"


class FakePlex:
    def __init__(self, at):
        self.at = at
        self.asked = 0

    async def movie(self, tmdb, **kw):
        self.asked += 1
        return self.at


def test_ready_is_told_when_plex_has_it_even_though_seerr_has_not_caught_up(env):
    store, d, svc = env
    svc.save_link("2001", 5, "email")
    req(7)                                           # approved; Seerr still says processing
    arr, plex = FakeArr(None), FakePlex(None)          # Radarr has not found a copy yet
    n = notifier(env, arr=arr, status=rs.RequestStatus(svc, arr, plex))
    tick(n)                                          # first run: silent
    arr.stage = "imported"
    svc.clear_cache()
    tick(n)
    assert [x["title"] for x in n.items_for(principal("2001"))["items"]] == ["Inception started downloading"]
    plex.at = time.time() - 30                       # Plex has the file; Seerr's scan has not run
    tick(n)
    titles = [x["title"] for x in n.items_for(principal("2001"))["items"]]
    assert "Inception is ready" in titles and plex.asked >= 2


def test_the_cleanup_clock_starts_when_plex_has_it(cx):
    store, d, svc, arr, cl, _ = cx
    asked = time.time() - 5 * 86400
    seerr_req(1, "movie", 27205, created=iso(asked), media_status=3)
    item = track(cl, 1, "movie", 27205, "Inception", requested_at=asked)
    at = time.time() - 3600
    cl.status = rs.RequestStatus(svc, FakeArr("imported"), FakePlex(at))
    asyncio.run(cl.refresh())
    row = cl._get(item)
    assert row["state"] == "running" and abs(row["available_at"] - at) < 2


def test_the_page_gets_the_words_with_me(env):
    from tests.test_requests import app_for
    from tests.test_requests import principal as person
    store, d, svc = env
    c = app_for(svc, d, person("2001", "s@x.com", "sable"))
    got = c.get("/api/requests/me").json()
    assert got["words"]["filters"][0] == ["all", "Any status"] and got["words"]["hub"][0]["stages"] == ["ready"]
