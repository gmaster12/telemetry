"""My streams: viewers see stream cards only for their own account, on by default, and can be turned off."""
import json

from tests.test_security import app, signed_in  # noqa: F401  (fixture)


import dataclasses

import pytest


@pytest.fixture(autouse=True)
def live(app):
    app.poller.cfg = dataclasses.replace(app.poller.cfg, sample_mode="never")


def rows():
    return [
        {"id": "A-1", "user": "sable", "user_id": "2001", "server": "Alpha", "rating_key": "11", "title": "Mine"},
        {"id": "A-2", "user": "rook", "user_id": "2002", "server": "Alpha", "rating_key": "12", "title": "Someone else"},
        {"id": "B-1", "user": "owner", "user_id": "1", "server": "Beta", "rating_key": "13", "title": "Owner of Beta"},
    ]


def test_viewer_sees_only_their_own_streams(app):
    app.poller.streams = rows()
    v = signed_in(app, "2001", "s@x.com", ["mystreams"])
    got = v.get("/api/streams").json()["rows"]
    assert [r["id"] for r in got] == ["A-1"]


def test_full_streams_still_sees_everyone(app):
    app.poller.streams = rows()
    v = signed_in(app, "2002", "r@x.com", ["streams", "mystreams"])
    assert len(v.get("/api/streams").json()["rows"]) == 3


def test_server_owner_shown_as_account_1_is_matched(app, monkeypatch):
    app.poller.streams = rows()
    monkeypatch.setattr(app.poller, "owner_for", lambda server: "3003" if server == "Beta" else None)
    v = signed_in(app, "3003", "o@x.com", ["mystreams"])
    assert [r["id"] for r in v.get("/api/streams").json()["rows"]] == ["B-1"]


def test_turned_off_means_403(app):
    app.poller.streams = rows()
    v = signed_in(app, "2001", "s@x.com", ["history"])
    assert v.get("/api/streams").status_code == 403


def test_clip_of_someone_elses_stream_is_hidden(app):
    app.poller.streams = rows()
    v = signed_in(app, "2001", "s@x.com", ["mystreams"])
    assert v.get("/api/clip/1/12").status_code == 404


def test_on_by_default_and_added_once_for_existing_people(app):
    from app.auth import DEFAULT_SECTIONS, add_mystreams_section, default_sections
    assert "mystreams" in DEFAULT_SECTIONS and "requests" in DEFAULT_SECTIONS
    store, d = app.store, app.directory
    store.set_setting("mystreams_section_added", "")
    store.set_setting("default_sections", json.dumps(["history", "requests"]))
    d.upsert_user({"id": "2005", "username": "kit", "email": "k@x.com"}, shared=True)
    d.set_user_access("2005", sections=["history"], use_defaults=False, disabled=False)
    add_mystreams_section(store)
    assert default_sections(store) == ["history", "requests", "mystreams"]
    assert json.loads(d.get_user("2005")["sections"]) == ["history", "mystreams"]
    store.set_setting("default_sections", json.dumps(["history"]))  # Glenn turns it off: it stays off
    add_mystreams_section(store)
    assert default_sections(store) == ["history"]
