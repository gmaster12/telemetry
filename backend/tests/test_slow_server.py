"""A slow answer is not an outage (Glenn's pick 2A, Nov 15 2026): a Plex server that misses a poll keeps its last
streams, marked stale, for up to 3 misses in a row; only then is it Offline and its streams end."""
import asyncio

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def poller(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    from app import poller as P
    from app.secrets import SecretBox
    from app.store import Store
    store = Store(str(tmp_path / "t.db"), SecretBox())
    store.add_server({"name": "Atlas", "plex_url": "http://atlas:32400", "enabled": 1}, "tok")
    p = P.Poller(store)
    p.clips.want = lambda *a: False
    answers = {"ok": True}

    class Fake:
        async def sessions(self):
            if not answers["ok"]:
                raise TimeoutError("Atlas took too long")
            return [{"sessionKey": "42", "ratingKey": "7"}]

    monkeypatch.setattr(p, "client", lambda s: Fake())
    monkeypatch.setattr(P, "normalize_session", lambda s, it, src: {
        "id": "Atlas-42", "server": s.name, "rating_key": "7", "user_id": "2001", "user": "sable", "title": "Andor",
        "kind": "Movie", "state": "Playing", "bitrate_mbps": 8.0, "method": "Direct play"})
    return p, answers


def poll(p):
    asyncio.run(p.poll_plex())


def test_streams_stay_through_two_misses_then_end_on_the_third(poller):
    p, answers = poller
    poll(p)
    assert [r["id"] for r in p.streams] == ["Atlas-42"] and not p.streams[0].get("stale")
    play = p.directory.history("2001")[0]
    answers["ok"] = False
    for _ in range(2):                                     # two slow answers: the stream stays, marked
        poll(p)
        assert [r["id"] for r in p.streams] == ["Atlas-42"] and p.streams[0]["stale"]
        view = p.servers_view()[0]
        assert view["status"] == "Healthy" and view["answering"] is False
        assert p.directory.history("2001")[0]["stopped_at"] is None   # the play stays open
    poll(p)                                                # the third: down
    assert p.streams == [] and p.servers_view()[0]["status"] == "Offline"
    assert p.since[str(1)]["online"] is False
    assert p.directory.history("2001")[0]["id"] == play["id"]


def test_an_answer_after_one_miss_carries_on_as_if_nothing_happened(poller):
    p, answers = poller
    poll(p)
    answers["ok"] = False
    poll(p)
    answers["ok"] = True
    poll(p)
    assert [r.get("stale") for r in p.streams] == [None]
    assert p.servers_view()[0]["answering"] is True and p.since[str(1)]["online"] is True
    assert len(p.directory.history("2001")) == 1          # still the same play, not a new one
