"""Settings home: one call says what is set up, what is working and what needs a look."""
import importlib
import sys

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def main(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    for m in [m for m in sys.modules if m == "app.config" or m == "app.main"]:
        del sys.modules[m]
    import app.main as M
    yield M
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    importlib.invalidate_caches()


def test_summary_counts_servers_people_and_problems(main):
    M = main
    s = M.store
    s.add_server({"name": "Atlas", "plex_url": "http://a:32400", "max_streams": 8, "netdata_node": "n1"}, "t")
    s.add_server({"name": "Lyra", "plex_url": "http://l:32400", "max_streams": 8}, "t")
    M.poller.reload()
    M.poller.server_state = {"Atlas": {"online": True}, "Lyra": {"online": False, "error": "timed out"}}
    M.directory.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    M.directory.upsert_user({"id": "2002", "username": "kit", "email": "k@x.com"}, shared=True)
    M.directory.set_user_access("2002", sections=["streams"], use_defaults=False, disabled=False)
    out = M.settings_summary()
    assert out["servers"] == {"total": 2, "enabled": 2, "online": 1, "offline": ["Lyra"], "errors": [{"name": "Lyra", "error": "timed out"}]}
    assert out["access"]["people"] == 2 and out["access"]["custom"] == 1 and out["access"]["off"] == 0
    assert out["billing"]["connected"] is False and out["billing"]["unmatched"] == 0
    assert out["alerts"]["hot_cpu_pct"] == M.poller.cfg.hot_cpu_pct
    if out["netdata"]["mode"] == "cloud":
        assert out["netdata"]["unlinked"] == ["Lyra"]
