"""Playback history tracking, passwords and section access."""
import json

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "Cluster@Mail.com")
    from app.secrets import SecretBox
    from app.store import Directory, Store
    store = Store(str(tmp_path / "t.db"), SecretBox())
    return store, Directory(store)


def row(**kw):
    base = {"server": "Atlas", "session_key": "42", "rating_key": "123", "user_id": "2001", "user": "sable", "user_thumb": None,
            "title": "Snowfall", "subtitle": "S6 · E4", "kind": "Episode", "device": "LG TV", "player": "Plex for LG", "platform": "webOS",
            "location": "LAN", "method": "Direct play", "resolution": "4K", "state": "Playing", "progress_pct": 10, "duration_min": 46,
            "_art_path": "/library/metadata/100/thumb/1"}
    base.update(kw)
    return base


def test_track_plays_opens_extends_and_closes(env, monkeypatch):
    store, d = env
    from app import poller as P
    p = P.Poller(store)
    clock = [1_000_000]
    monkeypatch.setattr(P.time, "time", lambda: clock[0])
    p.track_plays([row()], {"Atlas"})
    clock[0] += 10
    p.track_plays([row(progress_pct=12)], {"Atlas"})
    clock[0] += 10
    p.track_plays([row(state="Paused")], {"Atlas"})  # paused time does not count
    h = d.history("2001")
    assert len(h) == 1 and h[0]["stopped_at"] is None and h[0]["watched_sec"] == 10
    clock[0] += 10
    p.track_plays([], set())  # server did not answer: keep the play open
    assert d.history("2001")[0]["stopped_at"] is None
    p.track_plays([], {"Atlas"})  # server answered without it: closed
    assert d.history("2001")[0]["stopped_at"] is not None
    assert d.get_user("2001")["title"] == "sable"


def test_new_item_in_same_session_starts_new_play(env, monkeypatch):
    store, d = env
    from app import poller as P
    p = P.Poller(store)
    p.track_plays([row()], {"Atlas"})
    p.track_plays([row(rating_key="124", title="Snowfall", subtitle="S6 · E5")], {"Atlas"})
    h = d.history("2001")
    assert [x["rating_key"] for x in h if x["stopped_at"] is None] == ["124"]
    assert len(h) == 2


def test_owner_local_account_maps_to_owner(env):
    store, d = env
    from app.poller import Poller
    d.upsert_user({"id": "9001", "username": "glenn", "title": "Glenn", "email": "cluster@mail.com"}, shared=True, is_owner=True)
    Poller(store).track_plays([row(user_id="1", user="glenn")], {"Atlas"})
    assert len(d.history("9001")) == 1
    assert d.get_user("1") is None
    assert d.get_user("9001")["title"] == "Glenn"  # a stream does not overwrite the Plex display name


def test_import_is_idempotent(env):
    _, d = env
    rec = {"server": "Atlas", "rating_key": "900", "account": "2001", "user_id": "2001", "username": "sable", "title": "Heat",
           "subtitle": "1995", "kind": "Movie", "art": None, "viewed_at": 1_000}
    assert d.import_play(rec) is True
    assert d.import_play(rec) is False


def test_search_by_username_and_email(env):
    _, d = env
    d.upsert_user({"id": "2001", "username": "sable", "title": "Sable", "email": "sable@example.com"}, shared=True)
    d.upsert_user({"id": "2002", "username": "quill_9", "title": "Quill", "email": "q@other.org"}, shared=True)
    assert [u["plex_id"] for u in d.search_users("QUILL")] == ["2002"]
    assert [u["plex_id"] for u in d.search_users("example.com")] == ["2001"]
    assert len(d.search_users("")) == 2


def test_passwords():
    from app.auth import check_password, hash_password, password_problem
    h = hash_password("correct horse battery")
    assert check_password("correct horse battery", h)
    assert not check_password("correct horse batterY", h)
    assert not check_password("x", "garbage")
    assert password_problem("short") and not password_problem("twelve chars")


def test_roles_and_sections(env):
    store, d = env
    from app.auth import default_sections, plex_principal
    d.upsert_user({"id": "9001", "username": "glenn", "email": "cluster@mail.com"})
    d.upsert_user({"id": "2001", "username": "sable", "email": "sable@example.com"})
    admin = plex_principal(store, d.get_user("9001"))
    viewer = plex_principal(store, d.get_user("2001"))
    assert admin.is_admin and admin.can("servers")
    assert not viewer.is_admin and viewer.can("history") and not viewer.can("streams")
    store.set_setting("default_sections", json.dumps(["streams", "history", "bogus"]))
    assert default_sections(store) == ["streams", "history"]
    assert plex_principal(store, d.get_user("2001")).can("streams")
    d.set_user_access("2001", sections=["bandwidth"])
    v = plex_principal(store, d.get_user("2001"))
    assert v.can("bandwidth") and not v.can("streams")
    d.set_user_access("2001", disabled=True)
    assert plex_principal(store, d.get_user("2001")) is None


def test_sessions_and_admin_password_change(env):
    _, d = env
    from app.auth import _hash_token, hash_password, new_session
    aid = d.add_admin("ops", hash_password("correct horse battery"))
    tok = new_session(d, "local", str(aid))
    assert d.get_session(_hash_token(tok))
    d.set_admin_password(aid, hash_password("another long passphrase"))
    assert d.get_session(_hash_token(tok)) is None
    assert d.get_admin("OPS")["id"] == aid  # usernames are case-insensitive
