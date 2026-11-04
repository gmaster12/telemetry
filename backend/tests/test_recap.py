"""My month: a viewer's own viewing for the week, month or year."""
import datetime as dt

from tests.test_security import app, signed_in  # noqa: F401  (fixture)


def play(M, uid, title, kind, when, sec, sub=""):
    M.store.db.execute("INSERT INTO plays (server, session_key, user_id, title, subtitle, kind, started_at, last_seen, watched_sec) VALUES ('A', ?, ?, ?, ?, ?, ?, ?, ?)",
                       (f"k{when}{title}", uid, title, sub, kind, when, when + sec, sec))
    M.store.db.commit()


def test_month_recap_is_own_and_adds_up(app):
    from app.main import recap_for
    now = dt.datetime(2026, 11, 20, 18, 0, tzinfo=dt.timezone.utc).timestamp()
    day = 86400
    base = int(dt.datetime(2026, 11, 10, 21, 0, tzinfo=dt.timezone.utc).timestamp())  # 9 PM UTC
    for i in range(3):                                       # 3 days in a row
        play(app, "2001", "Snowfall", "Episode", base + i * day, 3600)
    play(app, "2001", "Dune", "Movie", base + 6 * day, 1800)
    play(app, "2001", "Old", "Movie", int(dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc).timestamp()), 7200)  # last month, same stretch
    play(app, "2002", "Not mine", "Movie", base, 9999)
    r = recap_for("2001", "month", 0, now=now)
    assert r["plays"] == 4 and r["episodes"] == 3 and r["movies"] == 1
    assert r["hours"] == 3.5 and r["prev_hours"] == 2.0
    assert r["streak"]["days"] == 3 and r["streak"]["from"] == "2026-11-10"
    assert r["prime"]["hour"] == 21
    assert [t["title"] for t in r["top"]] == ["Snowfall", "Dune"]
    assert r["label"] == "November"
    assert sum(r["by_hour"]) == 3.5


def test_recap_route_needs_history_and_checks_period(app):
    v = signed_in(app, "2001", "s@x.com", ["history"])
    assert v.get("/api/me/recap?period=month&tz=-360").status_code == 200
    assert v.get("/api/me/recap?period=decade").status_code == 400
    assert signed_in(app, "2003", "t@x.com", ["requests"]).get("/api/me/recap").status_code == 403
