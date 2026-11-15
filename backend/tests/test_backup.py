"""Nightly copies of the database (Glenn's pick K2A, Nov 15 2026)."""
import asyncio
import gzip
import os
import sqlite3
import time

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def bk(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    from app.backup import Backups
    from app.secrets import SecretBox
    from app.store import Store
    store = Store(str(tmp_path / "telemetry.db"), SecretBox())
    store.add_server({"name": "Atlas", "plex_url": "http://atlas:32400", "enabled": 1}, "plex-token")
    return store, Backups(store), tmp_path


def test_a_copy_is_a_whole_checked_database_readable_only_by_us(bk):
    store, b, tmp = bk
    made = asyncio.run(b.run_now())
    path = tmp / "backups" / made["name"]
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
    raw = tmp / "restored.db"
    raw.write_bytes(gzip.decompress(path.read_bytes()))
    db = sqlite3.connect(raw)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT name FROM servers").fetchall() == [("Atlas",)]
    st = b.status()
    assert st["count"] == 1 and st["error"] is None and st["overdue"] is False
    assert not [p for p in (tmp / "backups").iterdir() if p.name.endswith(".part")]


def test_only_the_last_14_are_kept(bk):
    store, b, tmp = bk
    folder = tmp / "backups"
    folder.mkdir()
    for i in range(20):
        p = folder / f"telemetry-2026-10-{i + 1:02d}-0400.db.gz"
        p.write_bytes(b"x")
        os.utime(p, (time.time() - (30 - i) * 86400,) * 2)
    (folder / "notes.txt").write_text("left alone")
    b.prune()
    names = sorted(p.name for p in folder.iterdir())
    assert len([n for n in names if n.endswith(".db.gz")]) == 14 and "notes.txt" in names
    assert "telemetry-2026-10-01-0400.db.gz" not in names and "telemetry-2026-10-20-0400.db.gz" in names


def test_due_at_four_once_a_day_or_when_overdue(bk):
    store, b, tmp = bk
    from datetime import datetime
    from zoneinfo import ZoneInfo
    z = ZoneInfo("America/Chicago")
    assert b.due()                                          # nothing yet: overdue
    asyncio.run(b.run_now())
    newest = b.status()["newest"]
    assert not b.due(newest + 60)                           # just made one: not again
    four = datetime.fromtimestamp(newest, z).replace(hour=4, minute=5).timestamp()
    if four <= newest:
        four += 86400
    assert b.due(four)                                      # the next 4 AM after the last copy


def test_a_failed_copy_is_recorded_and_tells_the_admins(bk, monkeypatch):
    store, b, tmp = bk
    from app.notify import Notifier
    from tests.test_notify import principal
    monkeypatch.setattr(b, "run", lambda: (_ for _ in ()).throw(OSError("No space left on device")))
    with pytest.raises(OSError):
        asyncio.run(b.run_now())
    assert "No space" in b.status()["error"]
    n = Notifier(store, backups=b)
    n.admin_tick(int(time.time()))
    boss = principal("cluster", role="admin")
    got = [x for x in n.items_for(boss)["items"] if x["kind"] == "backup"]
    assert got and got[0]["pinned"] and got[0]["href"] == "settings:backups"
    monkeypatch.undo()
    asyncio.run(b.run_now())
    n.admin_tick(int(time.time()) + 60)
    got = [x for x in n.items_for(boss)["items"] if x["kind"] == "backup"]
    assert not got[0]["pinned"] and got[0]["title"] == "The database copy is working again"
