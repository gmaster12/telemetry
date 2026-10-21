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


def test_user_servers(env):
    _, d = env
    d.upsert_user({"id": "2001", "username": "sable", "servers": ["Orion", "Atlas"]}, shared=True)
    assert json.loads(d.get_user("2001")["servers"]) == ["Atlas", "Orion"]
    d.add_user_server("2001", "Beta")
    d.add_user_server("2001", "Atlas")
    assert json.loads(d.get_user("2001")["servers"]) == ["Atlas", "Beta", "Orion"]
    d.upsert_user({"id": "2001", "title": "Sable"})  # no servers given: keep them
    assert json.loads(d.get_user("2001")["servers"]) == ["Atlas", "Beta", "Orion"]


def test_track_plays_records_server(env):
    store, d = env
    from app.poller import Poller
    Poller(store).track_plays([row(server="Basestar")], {"Basestar"})
    assert json.loads(d.get_user("2001")["servers"]) == ["Basestar"]


def test_old_users_table_gets_servers_column(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (plex_id TEXT PRIMARY KEY, username TEXT, title TEXT, email TEXT, thumb TEXT, shared INTEGER NOT NULL DEFAULT 0,"
                " is_owner INTEGER NOT NULL DEFAULT 0, sections TEXT, disabled INTEGER NOT NULL DEFAULT 0, first_seen INTEGER, last_seen INTEGER, last_login INTEGER)")
    con.commit(); con.close()
    from app.secrets import SecretBox
    from app.store import Directory, Store
    d = Directory(Store(str(db), SecretBox()))
    d.upsert_user({"id": "1", "username": "x", "servers": ["Atlas"]})
    assert json.loads(d.get_user("1")["servers"]) == ["Atlas"]


def test_sync_users_across_owner_accounts(env, monkeypatch):
    """Each server belongs to its own Plex account: owners and shares are merged per person."""
    import asyncio
    store, d = env
    from app import plex as plexmod
    from app.poller import Poller
    store.add_server({"name": "Atlas", "plex_url": "http://a:32400", "machine_id": "m-atlas", "enabled": 1}, "t1")
    store.add_server({"name": "Orion", "plex_url": "http://o:32400", "machine_id": "m-orion", "enabled": 1}, "t2")
    store.save_account({"id": "9001", "username": "glenn", "email": "cluster@mail.com"}, "tok-a")
    store.save_account({"id": "9002", "username": "orion_owner", "email": "o@example.com"}, "tok-b")
    infos = {"tok-a": {"id": "9001", "username": "glenn", "title": "Glenn", "email": "cluster@mail.com"},
             "tok-b": {"id": "9002", "username": "orion_owner", "title": "Orion Owner", "email": "o@example.com"}}
    owned = {"tok-a": "m-atlas", "tok-b": "m-orion"}
    shares = {"tok-a": [{"id": "2001", "username": "sable", "email": "s@example.com", "server_ids": ["m-atlas"], "server_names": []}],
              "tok-b": [{"id": "2001", "username": "sable", "email": "s@example.com", "server_ids": ["m-orion"], "server_names": []},
                        {"id": "2002", "username": "quill", "email": "q@example.com", "server_ids": ["m-orion"], "server_names": []}]}

    async def user_info(self, t): return dict(infos[t])
    async def servers(self, t): return [{"machine_id": owned[t], "owned": True, "token": "x"}]
    async def shared_users(self, t): return [dict(u) for u in shares[t]]
    monkeypatch.setattr(plexmod.PlexAccount, "user_info", user_info)
    monkeypatch.setattr(plexmod.PlexAccount, "servers", servers)
    monkeypatch.setattr(plexmod.PlexAccount, "shared_users", shared_users)
    p = Poller(store)
    assert asyncio.run(p.sync_users()) == 4  # two owners, two shared people
    assert json.loads(d.get_user("2001")["servers"]) == ["Atlas", "Orion"]
    assert json.loads(d.get_user("2002")["servers"]) == ["Orion"]
    assert d.get_user("9002")["is_owner"] == 1
    assert {s["name"]: s["owner_plex_id"] for s in store.list_servers()} == {"Atlas": "9001", "Orion": "9002"}
    # each server's local account 1 is that server's owner
    assert p.owner_for("Atlas") == "9001" and p.owner_for("Orion") == "9002"
