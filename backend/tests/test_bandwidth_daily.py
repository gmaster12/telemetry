"""Peak bandwidth by day: summed across servers per minute, grouped by the viewer's local day."""
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


IDS = {"Atlas": 1, "Orion": 2}


def row(server, mbps):
    return {"server": server, "server_id": IDS[server], "online": True, "streams": 1, "bandwidth_mbps": mbps}


def test_daily_peak_sums_servers_and_keeps_the_time(store, monkeypatch):
    import app.store as S
    now = 1_760_000_000 // 86400 * 86400 + 20 * 3600  # 20:00 UTC
    monkeypatch.setattr(S.time, "time", lambda: now)
    today, yesterday = now // 86400 * 86400, now // 86400 * 86400 - 86400
    store.write_minute([row("Atlas", 100), row("Orion", 50)], yesterday + 21 * 3600)       # 150 at 21:00
    store.write_minute([row("Atlas", 120), row("Orion", 60)], yesterday + 22 * 3600)       # 180 at 22:00, the peak
    store.write_minute([row("Atlas", 90)], today + 3600)                                    # 90 today
    out = store.bandwidth_daily([1, 2])
    assert len(out) == 7 and out[-1]["today"] and not out[0]["today"]
    assert out[-2]["peak_mbps"] == 180.0 and out[-2]["peak_at"].endswith("T22:00:00Z")
    assert out[-1]["peak_mbps"] == 90.0
    assert out[0]["peak_mbps"] is None and out[0]["peak_at"] is None   # no history that day


def test_daily_peak_uses_the_viewers_day(store, monkeypatch):
    import app.store as S
    now = 1_760_000_000 // 86400 * 86400 + 20 * 3600
    monkeypatch.setattr(S.time, "time", lambda: now)
    midnight = now // 86400 * 86400
    store.write_minute([row("Atlas", 200)], midnight + 2 * 3600)  # 02:00 UTC is still yesterday at UTC-5
    utc, central = store.bandwidth_daily([1]), store.bandwidth_daily([1], tz_offset_min=-300)
    assert utc[-1]["peak_mbps"] == 200.0
    assert central[-1]["peak_mbps"] is None and central[-2]["peak_mbps"] == 200.0


def test_sample_daily_has_seven_days_ending_today():
    from app import sample
    out = sample.bandwidth_daily(-300)
    assert len(out) == 7 and out[-1]["today"] and all(r["peak_mbps"] for r in out)


def test_history_follows_the_server_through_a_rename(store):
    """Glenn's pick O3A (Nov 15 2026): the minute history is kept by server id, so a renamed server keeps its chart,
    and rows saved by name before the change are linked to their server at start."""
    sid = store.add_server({"name": "Atlas", "plex_url": "http://a:32400"}, None)
    now = int(time.time()) // 60 * 60
    store.write_minute([{"server": "Atlas", "online": True, "streams": 1, "bandwidth_mbps": 40}], now - 120)   # an old row: name only
    store.link_plays()                       # at start: the old row gets Atlas's id
    store.update_server(sid, {"name": "Atlas-2"})
    store.write_minute([{"server": "Atlas-2", "server_id": sid, "online": True, "streams": 1, "bandwidth_mbps": 50}], now - 60)
    assert [p["mbps"] for p in store.bandwidth_last_hour([sid])] == [40.0, 50.0]
    assert store.hot_minutes(sid, 0) == 0   # no CPU known: not hot
