"""Viewers see only their own servers, Online or Offline and since when; a short blip does not reset up time."""
import json

from tests.test_security import app, signed_in  # noqa: F401  (fixture)


def test_viewer_sees_only_their_servers_with_since(app):
    a = app.store.add_server({"name": "ATLAS Network", "plex_url": "http://a:32400", "enabled": 1}, "t")
    o = app.store.add_server({"name": "ORION-3", "plex_url": "http://o:32400", "enabled": 1}, "t")
    app.store.add_server({"name": "GALAXY", "plex_url": "http://g:32400", "enabled": 1}, "t")
    app.store.set_alias("Orion", o)
    app.poller.reload()
    v = signed_in(app, "2001", "s@x.com", ["history"])
    app.store.db.execute("UPDATE users SET servers = ? WHERE plex_id = '2001'", (json.dumps(["ATLAS Network", "Orion"]),))
    app.store.db.commit()
    sa = next(s for s in app.poller.cfg.servers if s.id == a)
    so = next(s for s in app.poller.cfg.servers if s.id == o)
    app.poller._mark(sa, True, now=1000)
    for t in (2000, 2010, 2020):  # three failed polls in a row: down since the first
        app.poller._mark(so, False, now=t)
    got = {x["name"]: x for x in v.get("/api/me/servers").json()["servers"]}
    assert set(got) == {"ATLAS Network", "ORION-3"}
    assert got["ATLAS Network"]["online"] is True and got["ATLAS Network"]["since"] == 1000
    assert got["ORION-3"]["online"] is False and got["ORION-3"]["since"] == 2000
    assert set(got["ORION-3"]) == {"id", "name", "online", "since"}  # no address, errors or load
    # saved: a restart remembers
    assert json.loads(app.store.get_setting("server_since"))[str(o)] == {"online": False, "since": 2000}


def test_one_failed_poll_keeps_up_time(app):
    a = app.store.add_server({"name": "ATLAS", "plex_url": "http://a:32400", "enabled": 1}, "t")
    app.poller.reload()
    s = app.poller.cfg.servers[0]
    app.poller._mark(s, True, now=1000)
    app.poller._mark(s, False, now=5000)
    app.poller._mark(s, True, now=5010)
    assert app.poller.state_since(a) == {"online": True, "since": 1000}
