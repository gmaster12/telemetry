"""Plays belong to a server by id, so renaming, re-adding or moving a server keeps its history and covers."""
import sqlite3

import pytest
from cryptography.fernet import Fernet

from tests.test_security import app, signed_in  # noqa: F401  (fixture)

H = {"X-Requested-With": "greystar"}


def old_db(path):
    """A database from before plays had server_id: Glenn's servers re-added under new names and ids."""
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, plex_url TEXT NOT NULL, token_enc TEXT,
      max_streams INTEGER NOT NULL DEFAULT 8, capacity_mbps REAL NOT NULL DEFAULT 1000, netdata_node TEXT, netdata_agent_url TEXT,
      machine_id TEXT, enabled INTEGER NOT NULL DEFAULT 1, created INTEGER NOT NULL);
    CREATE TABLE plays (id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, session_key TEXT, user_id TEXT, username TEXT,
      rating_key TEXT, art TEXT, title TEXT, subtitle TEXT, kind TEXT, device TEXT, player TEXT, platform TEXT, location TEXT,
      method TEXT, resolution TEXT, started_at INTEGER NOT NULL, last_seen INTEGER NOT NULL, stopped_at INTEGER,
      watched_sec INTEGER NOT NULL DEFAULT 0, progress_pct INTEGER, duration_min INTEGER, source TEXT NOT NULL DEFAULT 'live',
      UNIQUE (server, session_key, rating_key, started_at));
    """)
    for sid, name in [(9, "ATLAS Network"), (11, "GALAXY"), (12, ".RED."), (13, "Beta-Server 2"), (14, "ORION-3")]:
        db.execute("INSERT INTO servers (id, name, plex_url, created) VALUES (?, ?, 'http://10.0.0.1:32400', 0)", (sid, name))
    for i, old in enumerate(["Orion", "Beta", "Atlas", "Red", "Galaxy", "ORION-3", "Gone"]):
        db.execute("INSERT INTO plays (server, session_key, user_id, rating_key, art, title, started_at, last_seen) VALUES (?, ?, '2001', ?, ?, 'T', ?, ?)",
                   (old, f"s{i}", str(100 + i), f"/library/metadata/{100 + i}/thumb/1", 1000 + i, 1000 + i))
    db.commit()
    db.close()


def test_old_plays_get_their_server_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    from app.secrets import SecretBox
    from app.store import Directory, Store
    path = str(tmp_path / "old.db")
    old_db(path)
    store = Store(path, SecretBox())
    got = {r["server"]: r["server_id"] for r in store.db.execute("SELECT server, server_id FROM plays")}
    assert got == {"Orion": 14, "Beta": 13, "Atlas": 9, "Red": 12, "Galaxy": 11, "ORION-3": 14, "Gone": None}
    # the original name stays as the record of where it was watched
    assert {r["server"] for r in store.db.execute("SELECT server FROM plays")} >= {"Orion", "Atlas"}
    d = Directory(store)
    assert d.art_for(14, "100") == "/library/metadata/100/thumb/1"  # saved under "Orion", found by id
    assert d.art_for(14, "105") == "/library/metadata/105/thumb/1"
    assert store.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'plays_server_item'").fetchone()
    # seeding happens once: an alias removed later does not come back
    store.db.execute("DELETE FROM server_aliases WHERE old_name = 'Red'")
    store.db.commit()
    store2 = Store(path, SecretBox())
    assert "Red" not in {a["old_name"] for a in store2.aliases()}


def test_seed_skips_ids_that_do_not_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    from app.secrets import SecretBox
    from app.store import Store
    store = Store(str(tmp_path / "new.db"), SecretBox())
    assert store.aliases() == []


def add(M, name, machine):
    return M.store.add_server({"name": name, "plex_url": "http://10.0.0.5:32400", "machine_id": machine, "enabled": 1}, "tok")


def play(M, sid, name, rk="500"):
    M.directory.start_play({"server": name, "server_id": sid, "session_key": "k" + rk, "user_id": "2001", "rating_key": rk,
                            "title": "Snowfall"}, f"/library/metadata/{rk}/thumb/9", 1000)


def test_history_shows_current_name_and_cover(app):
    sid = add(app, "Orion", "mach-orion")
    play(app, sid, "Orion")
    app.store.update_server(sid, {"name": "ORION-3"})
    app.poller.reload()
    v = signed_in(app, "2001", "s@x.com", ["history"])
    row = v.get("/api/me/history").json()["rows"][0]
    assert row["server"] == "ORION-3" and row["watched_on"] == "Orion"
    assert row["poster"] == f"/api/poster/{sid}/500"


def test_delete_can_move_history_to_another_server(app):
    old, new = add(app, "Orion", "mach-a"), add(app, "ORION-3", "mach-b")
    play(app, old, "Orion")
    app.poller.reload()
    a = signed_in(app, "9001", "cluster@mail.com")
    assert a.delete(f"/api/settings/servers/{old}?move_to={old}", headers=H).status_code == 400
    assert a.delete(f"/api/settings/servers/{old}?move_to={new}", headers=H).status_code == 200
    assert app.directory.art_for(new, "500")
    assert {"old_name": "Orion", "server_id": new} in app.store.aliases()
    row = signed_in(app, "2001", "s@x.com", ["history"]).get("/api/me/history").json()["rows"][0]
    assert row["server"] == "ORION-3" and row["poster"] == f"/api/poster/{new}/500"


def test_readding_the_same_plex_server_brings_history_back(app, monkeypatch):
    old = add(app, "Orion", "mach-orion")
    app.store.remember_machine(old, "mach-orion")
    play(app, old, "Orion")
    app.poller.reload()
    a = signed_in(app, "9001", "cluster@mail.com")
    assert a.delete(f"/api/settings/servers/{old}", headers=H).status_code == 200

    async def fake_test(url, token):
        return {"machine_id": "mach-orion", "name": "ORION-3"}
    monkeypatch.setattr(app, "_test_plex", fake_test)
    r = a.post("/api/settings/servers", json={"name": "ORION-3", "plex_url": "http://10.9.9.9:32400", "token": "t", "machine_id": "mach-orion"}, headers=H)
    assert r.status_code == 200, r.text
    new = r.json()["id"]
    assert new != old and app.directory.art_for(new, "500")


def test_importing_again_after_a_rename_adds_nothing_twice(app):
    sid = add(app, "Orion", "m")
    rec = {"server": "Orion", "server_id": sid, "rating_key": "77", "account": "5", "user_id": "2001", "title": "X", "viewed_at": 5000}
    assert app.directory.import_play(rec) is True
    assert app.directory.import_play({**rec, "server": "ORION-3"}) is False
