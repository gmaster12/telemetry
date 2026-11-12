"""Unwatched cleanup and download progress: the tick box, the clock from arrival, plays that keep a title, nightly removal
through Radarr/Sonarr and Seerr, admin actions, and the plain words members see (never who asked, never release names)."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests import mock_arr as A
from tests import mock_seerr as M
from tests.test_requests import env  # noqa: F401  (fixture)

DAY = 86400


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


@pytest.fixture()
def cx(env):
    from app.arr import Arr
    from app.cleanup import Cleanup
    store, d, svc = env
    A.reset()
    arr = Arr(store, httpx.AsyncClient(transport=httpx.ASGITransport(app=A.app)))
    store.set_setting("radarr_url", "http://arr.test/radarr")
    store.set_setting("radarr_key", A.KEY)
    store.set_setting("sonarr_url", "http://arr.test/sonarr")
    store.set_setting("sonarr_key", A.KEY)
    poller = type("P", (), {"streams": []})()
    cl = Cleanup(store, svc, arr, poller)
    return store, d, svc, arr, cl, poller


def client(cx, sections=("requests",)):
    from app.auth import Principal
    from app.requests_api import make_router
    store, d, svc, arr, cl, _ = cx
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    who = Principal(kind="plex", subject="2001", name="sable", role="viewer", sections=list(sections), email="s@x.com")
    a = FastAPI()

    def need(request, section):
        if section not in who.sections:
            raise HTTPException(403, "no")
        return who
    a.include_router(make_router(svc, d, need, None, cl, arr))
    return TestClient(a)


def run(coro):
    return asyncio.run(coro)


def seerr_req(rid, kind, tmdb, req_status=2, created=None, seasons=(), media_status=2, media_seasons=None, **media):
    if media_seasons is not None:
        media["seasons"] = media_seasons
    M.state["requests"].append({"id": rid, "type": kind, "status": req_status, "is4k": False, "by": 1,
                                "created": created or iso(time.time() - 20 * DAY), "media": {"tmdbId": tmdb}, "seasons": list(seasons),
                                "media_status": media_status, "media_extra": {"id": tmdb + 90000, **media}})


def track(cl, rid, kind, tmdb, title, seasons=None, requested_at=None, year="2025"):
    cl.track(request_id=rid, kind=kind, tmdb_id=tmdb, is4k=False, seasons=seasons, info={"title": title, "year": year, "poster": None},
             plex_id="2001", name="sable")
    if requested_at:
        cl._set(cl._rows("request_id = ?", (rid,))[0]["id"], requested_at=int(requested_at))
    return cl._rows("request_id = ?", (rid,))[0]["id"]


def play(store, **row):
    base = {"server": "ATLAS", "session_key": f"s{time.time_ns()}", "user_id": "9", "username": "x", "rating_key": "1", "title": "?",
            "subtitle": "", "kind": "Movie", "started_at": int(time.time()), "last_seen": int(time.time()), "watched_sec": 600, "source": "live"}
    base.update(row)
    cols = ", ".join(base)
    with store.lock:
        store.db.execute(f"INSERT INTO plays ({cols}) VALUES ({', '.join('?' * len(base))})", tuple(base.values()))
        store.db.commit()


# ---------- the tick box ----------
def test_a_request_needs_the_tick_box_and_is_then_tracked(cx):
    c = client(cx)
    r = c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 27205})
    assert r.status_code == 422 and "Tick the box" in r.json()["detail"]
    assert not M.state.get("bodies")  # nothing reached Seerr
    r = c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 27205, "agreeRemoval": True})
    assert r.status_code == 200, r.text
    assert "agreeRemoval" not in M.state["bodies"][-1]
    rows = cx[4]._rows()
    assert [(x["kind"], x["tmdb_id"], x["state"], x["title"]) for x in rows] == [("movie", 27205, "waiting", "Inception")]
    assert c.get("/api/requests/me").json()["cleanup"] == {"on": True, "days": 14}


def test_off_means_no_tick_box_and_nothing_tracked(cx):
    cx[4].set_mode("off")
    c = client(cx)
    assert c.post("/api/requests/request", json={"mediaType": "movie", "mediaId": 27205}).status_code == 200
    assert cx[4]._rows() == []


# ---------- the clock ----------
def test_the_clock_starts_on_arrival_not_on_request(cx):
    store, d, svc, arr, cl, _ = cx
    asked = time.time() - 10 * DAY
    added = time.time() - 4 * DAY
    seerr_req(1, "movie", 27205, created=iso(asked), media_status=5, mediaAddedAt=iso(added))
    item = track(cl, 1, "movie", 27205, "Inception", requested_at=asked)
    run(cl.refresh())
    row = cl._get(item)
    assert row["state"] == "running" and abs(row["available_at"] - added) < 2 and abs(row["deadline"] - (added + 14 * DAY)) < 2
    clock = cl.clock_for("movie", 27205)
    assert clock["state"] in ("running", "due") and clock["daysLeft"] in (10, 11) and "days left to watch" in clock["word"]


def test_not_on_plex_yet_has_no_countdown(cx):
    cl = cx[4]
    seerr_req(1, "movie", 27205, media_status=3)
    track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    assert cl.clock_for("movie", 27205)["word"] == "Clock starts on arrival"


def test_one_real_play_by_anyone_keeps_it_short_ones_do_not(cx):
    store, d, svc, arr, cl, _ = cx
    added = time.time() - 3 * DAY
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(added), created=iso(added - 60))
    item = track(cl, 1, "movie", 27205, "Inception", requested_at=added - 60, year="2010")
    play(store, title="Inception", subtitle="2010", watched_sec=30, started_at=int(time.time() - DAY))      # 30 s: not enough
    play(store, title="Inception", subtitle="2010", watched_sec=900, started_at=int(added - DAY))           # before it arrived
    run(cl.refresh())
    assert cl._get(item)["state"] == "running"
    play(store, title="Inception (Director's Cut)", tmdb_id=27205, watched_sec=300, started_at=int(time.time() - 3600))  # by TMDB id
    run(cl.refresh())
    row = cl._get(item)
    assert row["state"] == "watched" and row["plays"] == 1
    assert cl.clock_for("movie", 27205)["word"] == "Watched, it stays"


def test_a_show_counts_plays_of_the_requested_seasons_only(cx):
    store, d, svc, arr, cl, _ = cx
    added = time.time() - 2 * DAY
    seerr_req(1, "tv", 1396, seasons=[2], created=iso(added - 60), media_status=4, mediaAddedAt=iso(added), tvdbId=81189,
              media_seasons=[{"seasonNumber": 1, "status": 5}, {"seasonNumber": 2, "status": 5}])
    item = track(cl, 1, "tv", 1396, "Breaking Bad", seasons=[2], requested_at=added - 60)
    play(store, kind="Episode", title="Breaking Bad", subtitle="S1 · E3 · ...", season=1, watched_sec=1800)
    run(cl.refresh())
    assert cl._get(item)["state"] == "running"
    play(store, kind="Episode", title="Breaking Bad", subtitle="S2 · E1 · Seven Thirty-Seven", watched_sec=1800)
    run(cl.refresh())
    assert cl._get(item)["state"] == "watched"


# ---------- removal ----------
def overdue(cl, item):
    now = int(time.time())
    cl._set(item, state="running", available_at=now - 20 * DAY, deadline=now - 6 * DAY)
    cl.store.set_setting("cleanup_hour", "0")


def test_review_mode_only_notes_what_would_go(cx):
    store, d, svc, arr, cl, _ = cx
    A.state["movies"].append({"id": 7, "tmdbId": 27205, "hasFile": True, "sizeOnDisk": 14_000_000_000})
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(time.time() - 20 * DAY))
    item = track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    overdue(cl, item)
    run(cl.nightly())
    row = cl._get(item)
    assert row["state"] == "running" and row["would_delete_at"] and A.state["movies"]
    assert cl.board()["totals"]["wouldHave"] == 1


def test_auto_mode_removes_through_radarr_and_clears_seerr(cx):
    store, d, svc, arr, cl, _ = cx
    cl.set_mode("auto")
    A.state["movies"].append({"id": 7, "tmdbId": 27205, "hasFile": True, "sizeOnDisk": 14_000_000_000})
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(time.time() - 20 * DAY))
    item = track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    overdue(cl, item)
    run(cl.nightly())
    row = cl._get(item)
    assert row["state"] == "deleted" and row["freed_bytes"] == 14_000_000_000 and row["deleted_by"] == "Nightly cleanup"
    assert A.state["movies"] == []
    deletes = [c for c in A.state["calls"] if c[1] == "DELETE"]
    assert deletes[0][3] == {"deleteFiles": "true", "addImportExclusion": "false"}
    assert 27205 + 90000 in M.state["deleted_media"]
    assert cl.clock_for("movie", 27205)["word"] == "Removed, not watched"
    run(cl.nightly())  # once a day
    assert len([c for c in A.state["calls"] if c[1] == "DELETE"]) == 1


def test_not_removed_while_someone_is_watching(cx):
    store, d, svc, arr, cl, poller = cx
    cl.set_mode("auto")
    A.state["movies"].append({"id": 7, "tmdbId": 27205, "hasFile": True, "sizeOnDisk": 1})
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(time.time() - 20 * DAY))
    item = track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    overdue(cl, item)
    poller.streams = [{"kind": "Movie", "title": "Inception"}]
    run(cl.nightly())
    assert cl._get(item)["state"] == "running" and A.state["movies"]


def test_a_show_loses_only_the_requested_seasons(cx):
    store, d, svc, arr, cl, _ = cx
    A.state["series"].append({"id": 3, "tvdbId": 81189, "seasons": [{"seasonNumber": 1, "monitored": True, "statistics": {"sizeOnDisk": 10}},
                                                                    {"seasonNumber": 2, "monitored": True, "statistics": {"sizeOnDisk": 20}}]})
    A.state["files"] += [{"id": 11, "seriesId": 3, "seasonNumber": 1, "size": 10}, {"id": 21, "seriesId": 3, "seasonNumber": 2, "size": 20}]
    seerr_req(1, "tv", 1396, seasons=[2], media_status=4, tvdbId=81189, mediaAddedAt=iso(time.time() - 20 * DAY),
              media_seasons=[{"seasonNumber": 2, "status": 5}])
    item = track(cl, 1, "tv", 1396, "Breaking Bad", seasons=[2])
    run(cl.refresh())
    res = run(cl.delete(item, "Glenn"))
    assert res["ok"] and res["freed"] == 20
    assert [f["id"] for f in A.state["files"]] == [11]
    s = A.state["series"][0]
    assert [x["monitored"] for x in s["seasons"]] == [True, False]
    assert 1396 + 90000 not in M.state.get("deleted_media", [])  # the show stays in Seerr (Season 1 is still there)


def test_without_radarr_it_says_so_and_keeps_the_title(cx):
    store, d, svc, arr, cl, _ = cx
    store.set_setting("radarr_url", None)
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(time.time() - 20 * DAY))
    item = track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    res = run(cl.delete(item, "Glenn"))
    assert not res["ok"] and "Radarr is not connected" in res["error"]
    assert cl._get(item)["state"] == "running"


# ---------- admin actions ----------
def test_keep_extend_and_track_again(cx):
    store, d, svc, arr, cl, _ = cx
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(time.time() - 12 * DAY))
    item = track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    before = cl._get(item)["deadline"]
    cl.extend(item, 7)
    assert cl._get(item)["deadline"] == before + 7 * DAY
    cl.keep(item, "Glenn", "Family favourite")
    assert cl.board()["columns"]["kept"][0]["keptNote"] == "Family favourite"
    assert cl.clock_for("movie", 27205)["word"] == "Kept"
    cl.track_again(item)
    row = cl._get(item)
    assert row["state"] == "running" and row["deadline"] - time.time() > 13 * DAY


# ---------- what members see ----------
def test_clocks_never_say_who_asked(cx):
    store, d, svc, arr, cl, _ = cx
    seerr_req(1, "movie", 27205, media_status=5, mediaAddedAt=iso(time.time() - 3 * DAY))
    track(cl, 1, "movie", 27205, "Inception")
    run(cl.refresh())
    body = client(cx).get("/api/requests/clocks?ids=movie:27205,tv:1").text
    assert '"movie:27205"' in body and '"tv:1":null' in body
    for secret in ("sable", "2001", "requested", "s@x.com"):
        assert secret not in body


def test_movie_progress_in_plain_words(cx):
    store, d, svc, arr, cl, _ = cx
    seerr_req(1, "movie", 27205, media_status=3)
    A.state["movies"].append({"id": 7, "tmdbId": 27205, "hasFile": False, "isAvailable": True})
    A.state["queue"]["radarr"].append({"id": 1, "movieId": 7, "downloadId": "abc", "size": 1000, "sizeleft": 380, "status": "downloading",
                                       "trackedDownloadState": "downloading", "timeleft": "00:10:00",
                                       "title": "Inception.2010.1080p.WEB-DL.x264-GROUP", "downloadClient": "qbit",
                                       "indexer": "SecretIndexer", "quality": {"quality": {"name": "WEBDL-1080p"}}})
    got = client(cx).get("/api/requests/progress?ids=movie:27205")
    body = got.text
    item = got.json()["items"]["movie:27205"]
    assert item["word"] == "Downloading 62%" and item["quality"] == "1080p · WEB" and item["minutes"] == 10
    assert [s["state"] for s in item["steps"]] == ["done", "done", "done", "now", "next", "next"]
    for secret in ("GROUP", "qbit", "SecretIndexer", "x264"):
        assert secret not in body


def test_an_import_problem_reads_plainly(cx):
    store, d, svc, arr, cl, _ = cx
    A.state["movies"].append({"id": 7, "tmdbId": 27205, "hasFile": False, "isAvailable": True})
    A.state["queue"]["radarr"].append({"id": 1, "movieId": 7, "size": 1000, "sizeleft": 0, "status": "completed",
                                       "trackedDownloadState": "importPending", "trackedDownloadStatus": "warning",
                                       "statusMessages": [{"title": "Inception.2010.GROUP.mkv", "messages": ["No audio tracks detected"]}]})
    item = client(cx).get("/api/requests/progress?ids=movie:27205").json()["items"]["movie:27205"]
    assert item["stage"] == "problem" and item["word"] == "Import needs a look"
    assert item["problem"] == "The copy has the wrong language or no usable audio."


def test_show_progress_episode_by_episode(cx):
    store, d, svc, arr, cl, _ = cx
    seerr_req(1, "tv", 1396, seasons=[1], tvdbId=81189)
    A.state["series"].append({"id": 3, "tvdbId": 81189, "seasons": [{"seasonNumber": 1, "monitored": True}]})
    past, future = "2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"
    A.state["episodes"] += [
        {"id": 1, "seriesId": 3, "seasonNumber": 1, "episodeNumber": 1, "title": "Pilot", "hasFile": True, "airDateUtc": past},
        {"id": 2, "seriesId": 3, "seasonNumber": 1, "episodeNumber": 2, "title": "Two", "hasFile": False, "airDateUtc": past},
        {"id": 3, "seriesId": 3, "seasonNumber": 1, "episodeNumber": 3, "title": "Three", "hasFile": False, "airDateUtc": past},
        {"id": 4, "seriesId": 3, "seasonNumber": 1, "episodeNumber": 4, "title": "Four", "hasFile": False, "airDateUtc": future}]
    A.state["queue"]["sonarr"].append({"id": 9, "seriesId": 3, "episodeId": 2, "size": 100, "sizeleft": 55, "status": "downloading"})
    item = client(cx).get("/api/requests/progress?ids=tv:1396").json()["items"]["tv:1396"]
    eps = item["show"]["seasons"][0]["episodes"]
    assert [e["word"] for e in eps] == ["On Plex", "Downloading 45%", "Looking for a copy", "Not out yet"]
    assert item["show"]["counts"]["onplex"] == 1 and item["show"]["counts"]["downloading"] == 1


def test_copy_from_seerr_fills_radarr_and_sonarr(cx):
    store, d, svc, arr, cl, _ = cx
    for k in ("radarr", "sonarr"):
        store.set_setting(k + "_url", None)
    M.state["arr_settings"] = {"radarr": [{"hostname": "arr.test", "port": 80, "useSsl": False, "baseUrl": "/radarr", "apiKey": A.KEY,
                                           "is4k": False, "isDefault": True}],
                               "sonarr": [{"hostname": "arr.test", "port": 80, "useSsl": False, "baseUrl": "/sonarr", "apiKey": A.KEY,
                                           "is4k": False, "isDefault": True}]}
    done = run(arr.from_seerr(svc))
    assert [(x["slot"], x["ok"]) for x in done] == [("radarr", True), ("sonarr", True)]
    assert store.get_setting("radarr_url") == "http://arr.test:80/radarr"
    assert "key" not in json.dumps(arr.settings()).replace("has_key", "")
