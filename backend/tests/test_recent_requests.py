"""Recently requested on the viewer home: everyone's requests from the last 7 days, never who asked."""
import datetime as dt

from tests import mock_seerr as M
from tests.test_everything import client
from tests.test_requests import env  # noqa: F401  (fixture)


def ago(days, hours=0):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def req(rid, kind, tmdb, status, created, by=1, **extra):
    M.state["requests"].append({"id": rid, "type": kind, "status": status, "is4k": False, "by": by, "created": created,
                                "media": {"tmdbId": tmdb}, "seasons": [], **extra})


class RecentMedia:
    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = items or [], fail, 0

    def enabled(self):
        return True

    async def cover(self, kind, item_id):
        return b"RIFFxxxxWEBP" if (kind, item_id) == ("ebook", 7) else None

    async def recent_requests(self, days=7):
        self.calls += 1
        if self.fail:
            raise RuntimeError("media site down")
        return self.items


def bk(kind, title, state, label, created):
    return {"kind": kind, "title": title, "sub": "Someone", "created": created, "state": state, "label": label}


def test_everyone_last_week_newest_first_in_plain_words(env):
    req(1, "movie", 603, 5, ago(0, 2), media_status=5)                                  # Available
    req(2, "tv", 1396, 2, ago(1), media_status=3, downloading=[{"title": "x"}])         # Downloading
    req(3, "movie", 27205, 4, ago(2))                                                   # Failed: shows as Searching
    req(4, "movie", 157336, 3, ago(3))                                                  # Declined: left out
    req(5, "movie", 550, 5, ago(9), media_status=5)                                     # older than a week: left out
    media = RecentMedia([bk("comic", "Saga", "follow", "Following", ago(0, 5)),
                         bk("audiobook", "Red Rising", "work", "Downloading 40%", ago(2, 3)),
                         bk("ebook", "Onyx Storm", "release", "Waiting for release", ago(4))])
    got = client(env, media).get("/api/requests/recent").json()
    assert got["sources"] == {"seerr": "ok", "books": "ok"}
    assert [(x["kind"], x["status"], x["shape"]) for x in got["items"]] == [
        ("movie", "Available", "circle"), ("comic", "Following", "circle"), ("show", "Downloading", "triangle"),
        ("movie", "Searching", "square"), ("audiobook", "Downloading", "triangle"), ("ebook", "Not out yet", "ring")]
    assert got["total"] == 6 and got["days"] == 7
    assert got["counts"] == {"movie": 2, "show": 1, "ebook": 1, "audiobook": 1, "comic": 1}
    assert got["status"]["Downloading"] == 2
    assert got["items"][0]["open"] == "#/requests/movie/603" and got["items"][0]["poster"]


def test_the_same_title_twice_shows_once_with_a_count(env):
    media = RecentMedia([bk("ebook", "Dune", "ready", "Available", ago(0, 1)), bk("ebook", "dune ", "work", "Searching", ago(1))])
    got = client(env, media).get("/api/requests/recent").json()
    assert [(x["title"], x["times"], x["status"]) for x in got["items"]] == [("Dune", 2, "Available")]


def test_never_says_who_asked(env):
    req(1, "movie", 603, 5, ago(0, 2), media_status=5, by=1)
    body = client(env, RecentMedia([bk("ebook", "Dune", "ready", "Available", ago(0, 1))])).get("/api/requests/recent").text
    for secret in ("Glenn", "glenn", "cluster@mail.com", "requestedBy", "avatar", '"by"', '"id"'):
        assert secret not in body


def test_books_only_with_the_section_and_one_side_down(env):
    req(1, "movie", 603, 5, ago(0, 2), media_status=5)
    media = RecentMedia([bk("ebook", "Dune", "ready", "Available", ago(0, 1))])
    got = client(env, media, sections=("requests",)).get("/api/requests/recent").json()
    assert got["sources"]["books"] == "off" and media.calls == 0 and got["counts"]["ebook"] == 0
    got = client(env, RecentMedia(fail=True)).get("/api/requests/recent").json()
    assert got["sources"]["books"] == "down" and [x["kind"] for x in got["items"]] == ["movie"]


def test_needs_the_requests_section(env):
    assert client(env, RecentMedia(), sections=("history",)).get("/api/requests/recent").status_code == 403


def test_book_rows_get_covers_from_this_site(env):
    media = RecentMedia([{**bk("ebook", "Dune", "ready", "Available", ago(0, 1)), "ref": "ebook:7"},
                         {**bk("comic", "Saga", "work", "Searching", ago(0, 2)), "ref": "nonsense"}])
    c = client(env, media)
    items = c.get("/api/requests/recent").json()["items"]
    assert [x["poster"] for x in items] == ["/api/requests/recent/cover/ebook/7", None]
    r = c.get("/api/requests/recent/cover/ebook/7")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and r.content.startswith(b"RIFF")
    assert c.get("/api/requests/recent/cover/ebook/8").status_code == 404
    assert c.get("/api/requests/recent/cover/movie/7").status_code == 404
    assert client(env, media, sections=("requests",)).get("/api/requests/recent/cover/ebook/7").status_code == 404
