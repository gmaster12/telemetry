"""Ratings and box office for poster chips and the About list: Rotten Tomatoes, IMDb and TMDB through Seerr,
budget and gross from TMDB, saved so a page of posters asks Seerr once."""
from tests import mock_seerr as M
from tests.test_requests import app_for, env, principal  # noqa: F401  (env is a fixture)


def test_scores_for_movies_and_series(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    r = c.get("/api/requests/scores?ids=movie:27205,movie:157336,movie:1001,tv:1399,tv:1396,bogus:1,movie:x")
    assert r.status_code == 200
    items = {(i["kind"], i["id"]): i for i in r.json()["items"]}
    assert set(items) == {("movie", 27205), ("movie", 157336), ("movie", 1001), ("tv", 1399), ("tv", 1396)}

    inc = items[("movie", 27205)]
    assert inc["rt"] == {"critics": 87, "fresh": True, "certified": True, "audience": 91, "upright": True}
    assert inc["imdb"] == {"score": 8.8, "votes": 2600000}
    assert inc["money"]["multiple"] == 5.2 and inc["money"]["verdict"] == "hit" and inc["money"]["profit"] == 839030630 - 160000000

    inter = items[("movie", 157336)]
    assert inter["rt"]["fresh"] is False and inter["imdb"] is None and inter["money"]["verdict"] == "loss"

    assert items[("movie", 1001)]["rt"] is None and items[("movie", 1001)]["money"] is None  # not out yet
    assert items[("tv", 1399)]["rt"]["critics"] == 89 and items[("tv", 1399)]["money"] is None
    assert items[("tv", 1396)]["rt"] is None


def test_scores_are_saved_and_not_asked_for_again(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    c = app_for(svc, d, principal("2001", "s@x.com", "sable"))
    c.get("/api/requests/scores?ids=movie:27205,tv:1399")
    calls = M.state.get("rating_calls", 0)
    svc.clear_cache()  # a request elsewhere clears Seerr's short cache; the saved scores stay
    again = c.get("/api/requests/scores?ids=movie:27205,tv:1399").json()["items"]
    assert M.state.get("rating_calls", 0) == calls and [i["id"] for i in again] == [27205, 1399]


def test_scores_need_the_requests_section(env):
    store, d, svc = env
    p = principal("2001", "s@x.com", "sable")
    p.sections = [s for s in p.sections if s != "requests"]
    c = app_for(svc, d, p)
    assert c.get("/api/requests/scores?ids=movie:27205").status_code == 403
