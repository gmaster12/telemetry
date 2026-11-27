"""Shows TMDB has no TheTVDB id for (new streaming shows): Seerr cannot send them to Sonarr, so a request adds them to
Sonarr directly by their IMDb id, with Seerr's default profile and folder. Anything uncertain goes to the admins."""
import pytest

from tests import mock_arr as A
from tests import mock_seerr as M
from tests.test_cleanup import cx  # noqa: F401  (fixture)
from tests.test_downloads import client
from tests.test_requests import env  # noqa: F401  (fixture)

TMDB, IMDB, TVDB = 340615, "tt33004920", 484688
AUTO_TV = 512


@pytest.fixture
def show():
    M.SHOWS[TMDB] = {"name": "The Trials of Karen Read", "firstAirDate": "2026-11-23", "posterPath": "/k.jpg", "backdropPath": "/kb.jpg",
                     "seasons": 1, "have": {}, "tvdb": None, "imdb": IMDB}
    M.state["arr_settings"] = {"sonarr": [{"id": 0, "is4k": False, "isDefault": True, "activeProfileId": 1, "activeProfileName": "Any",
                                           "activeDirectory": "Z:\\Storage2\\TV Shows", "enableSeasonFolders": False}]}
    A.state["lookup"] = [{"title": "The Trials of Karen Read", "titleSlug": "the-trials-of-karen-read", "tvdbId": TVDB, "imdbId": IMDB,
                          "year": 2026, "seriesType": "standard", "images": [],
                          "seasons": [{"seasonNumber": 0, "monitored": False}, {"seasonNumber": 1, "monitored": True}]}]
    sable = next(u for u in M.state["users"] if u["id"] == 5)
    sable["permissions"] |= AUTO_TV
    yield
    M.SHOWS.pop(TMDB, None)


def ask(c, **extra):
    return c.post("/api/requests/request", json={"mediaType": "tv", "mediaId": TMDB, "seasons": [1], "agreeRemoval": True, **extra})


def test_added_to_sonarr_by_imdb_id_then_shown_as_requested(cx, show):
    store, d, svc, arr, cl, _ = cx
    c = client(cx)
    assert c.get(f"/api/requests/tv/{TMDB}").json()["autoAdd"] == "possible"
    before = len(M.state.get("bodies", []))
    r = ask(c)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["direct"] and r.json()["status"] == "approved"
    assert len(M.state.get("bodies", [])) == before  # Seerr was never asked to make a request
    [added] = A.state["added"]
    assert added["tvdbId"] == TVDB and added["qualityProfileId"] == 1 and added["rootFolderPath"] == "Z:\\Storage2\\TV Shows"
    assert added["seasonFolder"] is False and added["addOptions"]["searchForMissingEpisodes"] is True
    assert "monitor" not in added["addOptions"]  # Sonarr follows the seasons' flags, as with Seerr
    assert {s["seasonNumber"]: s["monitored"] for s in added["seasons"]} == {0: False, 1: True}
    assert c.get(f"/api/requests/tv/{TMDB}").json()["autoAdd"] == "added"
    row = store.db.execute("SELECT source, status, seasons, tmdb_id FROM request_log").fetchone()
    assert dict(row) == {"source": "sonarr", "status": "approved", "seasons": "1", "tmdb_id": TMDB}
    assert svc.help.needs_you() == []


def test_already_in_sonarr_gets_the_season_monitored_and_searched(cx, show):
    A.state["series"].append({"id": 7, "tvdbId": TVDB, "monitored": False, "seasons": [{"seasonNumber": 1, "monitored": False}]})
    r = ask(client(cx))
    assert r.json()["direct"] and A.state["added"] == []
    assert A.state["series"][0]["seasons"][0]["monitored"] is True
    assert A.state["commands"] == [{"name": "SeasonSearch", "seriesId": 7, "seasonNumber": 1}]


@pytest.mark.parametrize("why", ["no-imdb", "two-matches", "wrong-year", "needs-approval", "4k", "no-seerr-sonarr"])
def test_anything_uncertain_goes_to_the_admins_and_adds_nothing(cx, show, why):
    store, d, svc, arr, cl, _ = cx
    extra = {}
    if why == "no-imdb":
        M.SHOWS[TMDB]["imdb"] = None
    elif why == "two-matches":
        A.state["lookup"].append({**A.state["lookup"][0], "tvdbId": 999})
    elif why == "wrong-year":
        A.state["lookup"][0]["year"] = 2019
    elif why == "needs-approval":
        next(u for u in M.state["users"] if u["id"] == 5)["permissions"] &= ~AUTO_TV
    elif why == "4k":
        extra = {"is4k": True}
    elif why == "no-seerr-sonarr":
        M.state["arr_settings"] = {}
    r = ask(client(cx), **extra)
    assert r.status_code == 409 and "let the admin know" in r.json()["detail"]
    assert A.state["added"] == [] and A.state["commands"] == []
    assert [f["request_id"] for f in svc.help.needs_you()] == [-TMDB]
