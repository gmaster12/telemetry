"""Emby watch history: live sessions become plays like Plex ones, and Emby's activity log backfills older plays."""
import asyncio
import sys

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.emby import EmbyAuth, member_id
from app.emby_plays import EmbyPlays, describe, from_text, res_label

STEVE = "523c18f4b39c4023beb16003a3a8c827"
EP = {"Id": "17845480", "Type": "Episode", "Name": "Bros Before Nodes", "SeriesName": "Rules of Engagement", "SeriesId": "17845384",
      "ParentIndexNumber": 6, "IndexNumber": 2, "RunTimeTicks": 12_000_000_000, "Height": 720, "MediaType": "Video"}
MOVIE = {"Id": "3569", "Type": "Movie", "Name": "Detective Pikachu", "ProductionYear": 2019, "RunTimeTicks": 60_000_000_000,
         "Height": 804, "ProviderIds": {"Tmdb": "447404"}, "MediaType": "Video"}


def session(item, paused=False, sid="s1", pos=6_000_000_000):
    return {"Id": sid, "UserId": STEVE, "UserName": "steve@x.ca", "Client": "Emby for Android", "DeviceName": "Den TV",
            "RemoteEndPoint": "99.233.240.167", "NowPlayingItem": item,
            "PlayState": {"PositionTicks": pos, "IsPaused": paused, "PlayMethod": "DirectPlay"}}


LOG = [  # newest first, as Emby sends it
    {"Id": 7, "Type": "playback.start", "ItemId": "999", "Date": "2026-11-23T05:00:00.0000000Z", "Name": "steve@x.ca is playing Gone Show - S1, Ep1 - Pilot on TV"},
    {"Id": 6, "Type": "playback.stop", "ItemId": "999", "Date": "2026-11-20T02:00:00.0000000Z", "Name": "steve@x.ca has finished playing Gone Show - S1, Ep1 - Pilot on TV"},
    {"Id": 5, "Type": "playback.start", "ItemId": "999", "Date": "2026-11-20T01:40:00.0000000Z", "Name": "steve@x.ca is playing Gone Show - S1, Ep1 - Pilot on TV"},
    {"Id": 4, "Type": "playback.stop", "ItemId": "3569", "Date": "2026-11-19T22:00:00.1234567Z", "Name": "steve@x.ca has finished playing Detective Pikachu on Den TV"},
    {"Id": 3, "Type": "user.authenticated", "Date": "2026-11-19T20:01:00.0000000Z", "Name": "steve@x.ca signed in"},
    {"Id": 2, "Type": "playback.start", "ItemId": "3569", "Date": "2026-11-19T20:30:00.0000000Z", "Name": "steve@x.ca is playing Detective Pikachu on Den TV"},
    {"Id": 1, "Type": "playback.stop", "ItemId": "3569", "Date": "2026-11-18T10:00:00.0000000Z", "Name": "nobody@gone.ca has finished playing Detective Pikachu on X"},
]


class FakeEmby:
    def __init__(self):
        self.sessions = []
        self.fail = False

    def handler(self, req: httpx.Request):
        p = req.url.path
        if self.fail:
            return httpx.Response(500)
        if p == "/Sessions":
            return httpx.Response(200, json=self.sessions)
        if p == "/Users":
            return httpx.Response(200, json=[{"Id": STEVE, "Name": "steve@x.ca", "ConnectUserName": "steve@x.ca", "ConnectLinkType": "LinkedUser", "Policy": {}}])
        if p == "/System/ActivityLog/Entries":
            start = int(req.url.params.get("StartIndex", 0))
            return httpx.Response(200, json={"Items": LOG[start:start + int(req.url.params["Limit"])]})
        if p == "/Items":
            ids = req.url.params["Ids"].split(",")
            return httpx.Response(200, json={"Items": [x for x in (EP, MOVIE) if x["Id"] in ids]})
        if p.startswith("/Items/") and p.endswith("/Images/Primary"):
            return httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    def auth(self):
        return EmbyAuth("http://emby.test", "key", httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


def test_describe_matches_plex_row_shape():
    d = describe(EP)
    assert (d["kind"], d["title"], d["subtitle"], d["art"], d["season"]) == ("Episode", "Rules of Engagement", "S6 · E2 · Bros Before Nodes", "17845384", 6)
    m = describe(MOVIE)
    assert (m["kind"], m["subtitle"], m["tmdb_id"], m["art"]) == ("Movie", "2019", 447404, "3569")
    assert from_text("Gone Show - S1, Ep1 - Pilot")["subtitle"] == "S1 · E1 · Pilot"
    assert res_label(720) == "720p" and res_label(2160) == "4K" and res_label(None) is None


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    monkeypatch.setenv("EMBY_URL", "http://emby.test")
    monkeypatch.setenv("EMBY_API_KEY", "key")
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as M
    yield M
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


def test_live_sessions_open_extend_and_close_plays(app):
    M = app
    fake = FakeEmby()
    ep = EmbyPlays(fake.auth(), M.directory, M.store)
    me = member_id(STEVE)
    fake.sessions = [session(EP)]
    assert asyncio.run(ep.poll(now=1000)) == 1
    asyncio.run(ep.poll(now=1010))
    fake.sessions = [session(EP, paused=True)]
    asyncio.run(ep.poll(now=1020))                         # paused: no time added
    rows = M.directory.history(me)
    assert len(rows) == 1 and rows[0]["watched_sec"] == 10 and rows[0]["stopped_at"] is None
    assert rows[0]["server"] == "Emby" and rows[0]["title"] == "Rules of Engagement" and rows[0]["method"] == "Direct play"
    assert rows[0]["progress_pct"] == 50 and rows[0]["location"] == "WAN"
    u = M.directory.get_user(me)
    assert u["source"] == "emby" and u["email"] == "steve@x.ca" and u["last_seen"]
    fake.fail = True
    asyncio.run(ep.poll(now=1030))                         # Emby not answering: the play stays open
    assert M.directory.history(me)[0]["stopped_at"] is None
    fake.fail = False
    fake.sessions = [session(MOVIE)]                       # next title in the same session: a new play
    asyncio.run(ep.poll(now=1040))
    fake.sessions = []
    asyncio.run(ep.poll(now=1050))
    rows = M.directory.history(me)
    assert [r["title"] for r in rows] == ["Detective Pikachu", "Rules of Engagement"] and all(r["stopped_at"] for r in rows)


def test_activity_log_backfill_pairs_starts_and_stops_once(app):
    M = app
    fake = FakeEmby()
    ep = EmbyPlays(fake.auth(), M.directory, M.store)
    M.store.set_setting("emby_live_since", str(1795400000))  # 2026-11-23 (after every stop above)
    r = asyncio.run(ep.import_history(page=3))
    assert r == {"added": 2, "skipped": 0, "errors": []}   # the unknown person's play and the open start are skipped
    rows = {x["title"]: x for x in M.directory.history(member_id(STEVE))}
    pika = rows["Detective Pikachu"]
    assert pika["watched_sec"] == 5400 and pika["tmdb_id"] == 447404 and pika["source"] == "emby" and pika["device"] == "Den TV"
    gone = rows["Gone Show"]                                # deleted from Emby since: the log's words
    assert gone["subtitle"] == "S1 · E1 · Pilot" and gone["watched_sec"] == 1200
    assert asyncio.run(ep.import_history())["added"] == 0  # again: nothing twice


def test_member_history_shows_emby_plays_with_emby_covers(app):
    M = app
    from app.auth import new_session
    fake = FakeEmby()
    M.emby.http = fake.auth().http
    ep = EmbyPlays(M.emby, M.directory, M.store)
    fake.sessions = [session(EP)]
    asyncio.run(ep.poll(now=1000))
    me = member_id(STEVE)
    M.directory.set_user_access(me, sections=["history"])
    c = TestClient(M.app)
    c.cookies.set("gst_session", new_session(M.directory, "emby", me))
    h = c.get("/api/me/history").json()
    assert h["rows"][0]["server"] == "Emby" and h["rows"][0]["poster"] == "/api/emby/poster/17845384"
    assert c.get("/api/emby/poster/17845384").content == b"JPEG"
    assert c.get("/api/emby/poster/..%2Fx").status_code == 404
