"""Settings kept in memory (Glenn's pick H2A, Nov 15 2026): a page's files no longer cost database reads and a
decryption each; a save is seen at once, and a change made outside this process within a minute."""
import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    from app.secrets import SecretBox
    from app.store import Store
    return Store(str(tmp_path / "t.db"), SecretBox())


def reads(store):
    seen = []
    store.db.set_trace_callback(lambda q: seen.append(q) if "FROM settings" in q else None)
    return seen


def test_settings_are_read_once_and_saves_show_at_once(store):
    store.set_setting("media_url", "https://media.example")
    store.set_setting("media_secret", "s3cret")
    store.get_setting("media_url")
    seen = reads(store)
    opened = []
    real = store.box.open
    store.box.open = lambda v: opened.append(1) or real(v)
    for _ in range(50):   # one page load's worth of files
        assert store.get_setting("media_url") == "https://media.example"
        assert store.get_setting("media_secret") == "s3cret"
        assert store.get_setting("missing", "default") == "default"
    assert seen == [] and len(opened) == 1
    store.set_setting("media_secret", "new")
    assert store.get_setting("media_secret") == "new"
    raw = store.db.execute("SELECT value FROM settings WHERE key = 'media_secret'").fetchone()[0]
    assert raw != "new"   # still saved encrypted


def test_a_change_from_outside_is_seen_after_the_wait(store, monkeypatch):
    import app.store as S
    store.set_setting("hot_cpu_pct", "85")
    store.get_setting("hot_cpu_pct")
    with store.lock:   # another process (python -m app.tools ...) writes the database directly
        store.db.execute("INSERT OR REPLACE INTO settings VALUES ('hot_cpu_pct', '90')")
        store.db.commit()
    assert store.get_setting("hot_cpu_pct") == "85"
    t = S.time.monotonic() + S.Store.SETTINGS_TTL + 1
    monkeypatch.setattr(S.time, "monotonic", lambda: t)
    assert store.get_setting("hot_cpu_pct") == "90"
