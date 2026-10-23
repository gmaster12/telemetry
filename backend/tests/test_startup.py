"""Startup: live streams come first; slow account syncs run beside them and are remembered across restarts."""
import asyncio
import time

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    from app.secrets import SecretBox
    from app.store import Store
    return Store(str(tmp_path / "t.db"), SecretBox())


def test_first_plex_poll_does_not_wait_for_slow_syncs(store, monkeypatch):
    from app.poller import Poller
    p = Poller(store)
    p.cfg.sample_mode = "never"
    events = []

    async def slow_sync():
        events.append("sync start")
        await asyncio.sleep(3)  # plex.tv and Stripe can take a long while for hundreds of people
        events.append("sync end")

    async def poll_plex():
        events.append("plex")

    async def poll_netdata():
        await asyncio.sleep(3)  # a slow Netdata Cloud answer must not hold up Plex either
        events.append("netdata")

    monkeypatch.setattr(p, "upkeep", slow_sync)
    monkeypatch.setattr(p, "poll_plex", poll_plex)
    monkeypatch.setattr(p, "poll_netdata", poll_netdata)
    monkeypatch.setattr(p, "record_minute", lambda: None)

    async def go():
        task = asyncio.create_task(p.run())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert events and events[0] == "plex"
    assert "sync end" not in events and "netdata" not in events


def test_sync_times_survive_a_restart(store):
    from app.poller import Poller
    store.set_setting("users_synced_at", str(time.time() - 60))
    store.set_setting("billing_synced_at", str(time.time() - 30))
    p = Poller(store)
    assert time.time() - p.users_synced_at < 6 * 3600   # no full plex.tv sync on restart
    assert time.time() - p.billing_synced_at < 15 * 60  # no full Stripe copy on restart


def test_unreadable_sync_time_means_sync_now(store):
    from app.poller import Poller
    store.set_setting("users_synced_at", "not a number")
    assert Poller(store).users_synced_at == 0.0
