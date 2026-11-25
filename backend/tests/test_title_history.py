"""The title on a stream card opens that show or movie's play history: every server, admins see everyone, a member
sees only their own plays."""
from tests.test_security import app, signed_in  # noqa: F401  (fixture)
from tests.test_server_ids import add


def play(M, sid, server, user, name, title, sub, kind, at, key):
    M.directory.start_play({"server": server, "server_id": sid, "session_key": key, "user_id": user, "user": name, "rating_key": key,
                            "title": title, "subtitle": sub, "kind": kind}, None, at)


def seed(M):
    a, b = add(M, "GALAXY", "mach-g"), add(M, "ORION-3", "mach-o")
    play(M, a, "GALAXY", "2001", "Sam", "Friends", "S1 · E1 · Pilot", "Episode", 1000, "k1")
    play(M, b, "ORION-3", "2002", "Ann", "friends", "S1 · E1 · Pilot", "Episode", 2000, "k2")      # another server, other capitals
    play(M, a, "GALAXY", "2002", "Ann", "Friends", "S1 · E2 · The Sonogram", "Episode", 3000, "k3")
    play(M, a, "GALAXY", "2001", "Sam", "Dune", "1984", "Movie", 4000, "k4")
    play(M, a, "GALAXY", "2002", "Ann", "Dune", "2021", "Movie", 5000, "k5")
    play(M, a, "GALAXY", "2002", "Ann", "Dune", "", "Movie", 6000, "k6")                           # imported with no year
    play(M, a, "GALAXY", "2001", "Sam", "Friends: The Reunion", "2021", "Movie", 7000, "k7")
    M.poller.reload()


def test_an_admin_sees_everyones_plays_of_a_show_on_every_server(app):
    seed(app)
    a = signed_in(app, "9001", "cluster@mail.com")
    r = a.get("/api/titles/history", params={"title": "Friends", "kind": "Episode"}).json()
    assert r["scope"] == "everyone" and [(x["user"], x["server"], x["subtitle"]) for x in r["rows"]] == [
        ("Ann", "GALAXY", "S1 · E2 · The Sonogram"), ("Ann", "ORION-3", "S1 · E1 · Pilot"), ("Sam", "GALAXY", "S1 · E1 · Pilot")]
    assert r["stats"]["plays"] == 3 and r["stats"]["people"] == 2 and r["stats"]["episodes"] == 2 and r["stats"]["first_play"] == 1000
    one = a.get("/api/titles/history", params={"title": "Friends", "kind": "Episode", "episode": "S1 · E1 · Pilot"}).json()
    assert [x["user"] for x in one["rows"]] == ["Ann", "Sam"] and one["stats"]["plays"] == 2 and one["stats"]["episodes"] is None
    older = a.get("/api/titles/history", params={"title": "Friends", "kind": "Episode", "before": 3000}).json()
    assert [x["started_at"] for x in older["rows"]] == [2000, 1000] and older["stats"] is None


def test_a_movie_is_kept_apart_from_its_remake_and_from_longer_names(app):
    seed(app)
    a = signed_in(app, "9001", "cluster@mail.com")
    r = a.get("/api/titles/history", params={"title": "Dune", "kind": "Movie", "year": "2021"}).json()
    assert [x["started_at"] for x in r["rows"]] == [6000, 5000]          # the 2021 film and the play with no year; not 1984
    assert a.get("/api/titles/history", params={"title": "Friends", "kind": "Movie"}).json()["rows"] == []


def test_a_member_sees_only_their_own_plays(app):
    seed(app)
    v = signed_in(app, "2001", "s@x.com", ["history"])
    r = v.get("/api/titles/history", params={"title": "Friends", "kind": "Episode"}).json()
    assert r["scope"] == "you" and [x["started_at"] for x in r["rows"]] == [1000] and "user_id" not in r["rows"][0]
    assert r["stats"]["plays"] == 1 and r["stats"]["people"] == 1
    assert signed_in(app, "2003", "n@x.com", []).get("/api/titles/history", params={"title": "Friends"}).status_code == 403
    assert v.get("/api/titles/history").status_code == 422
