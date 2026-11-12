"""Is it on Plex yet? (Glenn's ask, Nov 12 2026)

Seerr only learns a title is available from its library scans: the five-minute one reads Plex's
Recently added shelf, the thorough one runs once a day. A download that lands in a folder Plex
already had an entry for never reaches that shelf, so the site used to read "Importing" for hours
(Divergent did, Nov 12 2026). Both sides are asked now and the first one to say it is on Plex wins.

These check that the Plex side is strict about identity (ids, the imported file, 4K or not, and a
file Plex can really read), that it never costs anything while a title is still downloading, and
that Seerr stays the only source when Plex cannot be asked.
"""
import asyncio
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests import mock_arr as A
from tests import mock_plex as P
from tests import mock_seerr as M
from tests.test_cleanup import cx, iso  # noqa: F401  (fixtures)
from tests.test_requests import env  # noqa: F401  (fixture)

TMDB = 27205          # Inception, in the Seerr stand-in
TVDB = 81189
FILE = r"Z:\Movies\A Movie (2014)\A Movie (2014) Bluray-1080p.mkv"


class FakePoller:
    """Just enough poller for OnPlex: the configured servers and a client for one."""

    def __init__(self, servers, http):
        self.cfg = type("Cfg", (), {"servers": servers})()
        self.http = http
        self._clients: dict[int, object] = {}

    def client(self, server):
        from app.plex import PlexClient
        c = self._clients.get(server.id)
        if c is None or c.server != server:
            c = PlexClient(server, self.http)
            self._clients[server.id] = c
        return c


@pytest.fixture()
def px(cx):
    """OnPlex pointed at the Plex stand-in, with one linked server that is the portal's own."""
    from app.config import ServerCfg
    from app.onplex import OnPlex
    store, d, svc, arr, cl, _ = cx
    P.reset()
    P.state["sections"] = [{"key": "1", "type": "movie", "title": "Movies"},
                           {"key": "2", "type": "show", "title": "TV Shows"},
                           {"key": "9", "type": "movie", "title": "-"}]        # off in Seerr: never read
    server = ServerCfg(id=4, name="Perseus", plex_url="http://plex.test", token=P.TOKEN)
    poller = FakePoller([server], httpx.AsyncClient(transport=httpx.ASGITransport(app=P.app)))
    return OnPlex(store, svc, poller), cx


def run(coro):
    return asyncio.run(coro)


def calls(what):
    return [c for c in P.state["calls"] if c[0] == what]


# ---------- what counts as the requested title ----------
def test_a_matching_id_and_a_file_with_bytes():
    from app.onplex import match_part
    item = P.movie(TMDB, size=800)
    assert match_part(item, tmdb_id=TMDB)["size"] == 800
    assert match_part(item, tmdb_id=603) is None                      # a different film
    assert match_part(P.movie(TMDB, size=0), tmdb_id=TMDB) is None    # an entry with no real file


def test_the_imported_file_identifies_an_entry_with_no_ids():
    """An item an old Plex agent left without ids is still proof when the file is the one imported."""
    from app.onplex import match_part
    item = P.movie(TMDB, guids=False, size=800, file=FILE)
    assert match_part(item, tmdb_id=TMDB) is None
    assert match_part(item, tmdb_id=TMDB, size=800)["file"] == FILE
    assert match_part(item, tmdb_id=TMDB, file_name="a movie (2014) bluray-1080p.mkv")["file"] == FILE
    assert match_part(item, tmdb_id=TMDB, size=12345, file_name="something else.mkv") is None


def test_4k_wants_a_4k_file_and_ordinary_does_not():
    """Seerr's own rule: 4K availability is about the file's resolution, not the folder's name."""
    from app.onplex import match_part
    uhd, hd = P.movie(TMDB, resolution="4k"), P.movie(TMDB, resolution="1080")
    assert match_part(uhd, tmdb_id=TMDB, want4k=True) and not match_part(uhd, tmdb_id=TMDB, want4k=False)
    assert match_part(hd, tmdb_id=TMDB, want4k=False) and not match_part(hd, tmdb_id=TMDB, want4k=True)


# ---------- asking Plex ----------
def test_a_movie_plex_has_is_confirmed_from_the_changed_end(px):
    onplex, _ = px
    P.state["items"]["1"] = [P.movie(TMDB, rating_key="2942", size=800, file=FILE, updated=99)]
    at = run(onplex.movie(TMDB, size=800, file_name="A Movie (2014) Bluray-1080p.mkv"))
    assert at and at <= time.time()
    assert calls("items")[0][1]["sort"] == "updatedAt:desc"      # what a new file for an old entry bumps
    assert any(c[0] == "metadata" and c[1].get("checkFiles") == "1" for c in P.state["calls"])


def test_libraries_the_portal_does_not_read_are_not_looked_at(px):
    onplex, _ = px
    P.state["items"]["9"] = [P.movie(TMDB, rating_key="700", size=800)]
    assert run(onplex.movie(TMDB)) is None
    assert calls("items") and all(c[1].get("section") != "9" for c in calls("items"))


def test_a_file_plex_cannot_read_is_not_on_plex(px):
    """The old entry whose copy went away: Plex lists it, but there is nothing to watch."""
    onplex, _ = px
    P.state["items"]["1"] = [P.movie(TMDB, size=800, exists=False, accessible=False)]
    assert run(onplex.movie(TMDB)) is None


def test_nothing_there_is_not_asked_again_at_once(px):
    onplex, _ = px
    P.state["items"]["1"] = [P.movie(603, size=800)]
    assert run(onplex.movie(TMDB)) is None
    first = len(P.state["calls"])
    assert run(onplex.movie(TMDB)) is None
    assert len(P.state["calls"]) == first           # the no is kept for a while


def test_a_confirmation_keeps_its_first_time(px):
    onplex, _ = px
    P.state["items"]["1"] = [P.movie(TMDB, size=800)]
    first = run(onplex.movie(TMDB))
    P.state["items"]["1"] = []                      # Plex is not asked again
    assert run(onplex.movie(TMDB)) == first


def test_an_older_change_is_found_by_name(px, monkeypatch):
    """Past the recently-changed window, the title is searched for instead."""
    from app import onplex as mod
    onplex, _ = px
    monkeypatch.setattr(mod, "WINDOW", 1)
    P.state["items"]["1"] = [P.movie(603, rating_key="5", title="Other", size=900, updated=200),
                             P.movie(TMDB, rating_key="6", title="A Movie", size=800, updated=1)]
    assert run(onplex.movie(TMDB, title="A Movie"))
    assert any(c[1].get("title") == "A Movie" for c in calls("items"))


def test_only_the_portals_own_server_is_asked(px):
    """Seerr names the machine it scans; with no match nothing is asked and Seerr stays the source."""
    onplex, _ = px
    P.state["machine"] = "someone-elses-server"
    M.state["plex_settings"] = {"name": "Not Perseus", "machineId": "someone-elses-server-2", "libraries": []}
    P.state["items"]["1"] = [P.movie(TMDB, size=800)]
    assert run(onplex.movie(TMDB)) is None
    assert not calls("items")


def test_turning_it_off_leaves_seerr_as_the_only_source(px):
    onplex, (store, *_rest) = px
    store.set_setting("onplex_direct", "off")
    P.state["items"]["1"] = [P.movie(TMDB, size=800)]
    assert run(onplex.movie(TMDB)) is None
    assert not P.state["calls"]


def test_a_quiet_plex_server_is_left_alone_for_a_while(px):
    onplex, _ = px
    P.state["fail"].add("items")
    assert run(onplex.movie(TMDB)) is None
    before = len(calls("items"))
    assert run(onplex.movie(603)) is None            # a different title, same sick server
    assert len(calls("items")) == before


def test_every_asked_for_episode_must_be_there(px):
    """A different show per case: a confirmed answer is deliberately remembered, so reuse would hide the no."""
    onplex, _ = px
    P.state["items"]["2"] = [P.show(rating_key="80", title="A Show", tvdb=TVDB),
                             P.show(rating_key="81", title="B Show", tvdb=TVDB + 1),
                             P.show(rating_key="82", title="C Show", tvdb=TVDB + 2)]
    for key in ("80", "81", "82"):
        P.state["leaves"][key] = [P.episode(2, 1, size=500), P.episode(2, 2, size=600)]
    want = {(2, 1), (2, 2)}
    assert run(onplex.episodes(want, tvdb_id=TVDB, sizes={(2, 1): 500, (2, 2): 600}))
    assert run(onplex.episodes(want | {(2, 3)}, tvdb_id=TVDB + 1)) is None                           # one still missing
    assert run(onplex.episodes(want, tvdb_id=TVDB + 2, sizes={(2, 1): 999, (2, 2): 600})) is None    # another copy


# ---------- what the pages show ----------
def client(px, sections=("requests",)):
    from app.auth import Principal
    from app.requests_api import make_router
    onplex, (store, d, svc, arr, cl, _) = px
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    who = Principal(kind="plex", subject="2001", name="sable", role="viewer", sections=list(sections), email="s@x.com")
    a = FastAPI()

    def need(request, section):
        if section not in who.sections:
            raise HTTPException(403, "no")
        return who
    a.include_router(make_router(svc, d, need, None, cl, arr, onplex))
    return TestClient(a)


def imported(tmdb=TMDB, size=800, name="A Movie (2014) Bluray-1080p.mkv", media_status=3):
    """Radarr has finished with it and Seerr is still scanning: the state that used to say Importing."""
    M.state["requests"].append({"id": 1, "type": "movie", "status": 2, "is4k": False, "by": 5, "created": iso(time.time() - 600),
                                "media": {"tmdbId": tmdb}, "seasons": [], "media_status": media_status,
                                "media_extra": {"id": tmdb + 90000}})
    A.state["movies"].append({"id": 7, "tmdbId": tmdb, "hasFile": True, "isAvailable": True, "sizeOnDisk": size,
                              "title": "A Movie", "movieFile": {"size": size, "relativePath": name}})


def test_a_finished_movie_reads_on_plex_without_waiting_for_seerr(px):
    imported()
    P.state["items"]["1"] = [P.movie(TMDB, rating_key="2942", size=800, file=FILE)]
    body = client(px).get(f"/api/requests/progress?ids=movie:{TMDB}").json()
    item = body["items"][f"movie:{TMDB}"]
    assert item["onPlex"] is True and item["stage"] == "onplex"
    steps = {s["key"]: s for s in item["steps"]}
    assert steps["onplex"]["state"] == "done" and steps["onplex"]["at"]
    assert steps["importing"]["state"] == "done"


def test_seerr_is_still_the_backup_when_plex_has_nothing(px):
    imported()
    body = client(px).get(f"/api/requests/progress?ids=movie:{TMDB}").json()
    item = body["items"][f"movie:{TMDB}"]
    assert item["onPlex"] is False and item["stage"] == "imported"
    assert {s["key"]: s["state"] for s in item["steps"]}["importing"] == "now"


def test_seerr_saying_available_is_enough_on_its_own(px):
    imported(media_status=5)
    body = client(px).get(f"/api/requests/progress?ids=movie:{TMDB}").json()
    assert body["items"][f"movie:{TMDB}"]["onPlex"] is True
    assert not calls("items")           # Plex was never even asked


def test_the_title_page_says_available_before_seerr_does(px, monkeypatch):
    monkeypatch.setitem(M.MOVIES[TMDB], "status", 3)
    P.state["items"]["1"] = [P.movie(TMDB, size=800)]
    body = client(px).get(f"/api/requests/movie/{TMDB}").json()
    assert body["media"]["status"] == "available" and body["media"]["plexDirect"] is True


def test_the_title_page_leaves_a_title_plex_does_not_have_alone(px, monkeypatch):
    monkeypatch.setitem(M.MOVIES[TMDB], "status", 3)
    body = client(px).get(f"/api/requests/movie/{TMDB}").json()
    assert body["media"]["status"] == "processing" and "plexDirect" not in body["media"]


def test_the_green_bar_says_ready_and_keeps_no_release_name(px):
    imported()
    P.state["items"]["1"] = [P.movie(TMDB, rating_key="2942", size=800, file=FILE)]
    res = client(px).get("/api/requests/downloads")
    got = {x["key"]: x for x in res.json()["items"]}
    assert got[f"movie:{TMDB}"]["ready"] and got[f"movie:{TMDB}"]["word"] == "Ready"
    for release in ("Bluray-1080p", "Z:\\Movies", ".mkv"):
        assert release not in res.text
