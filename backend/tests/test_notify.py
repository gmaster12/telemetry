"""Notifications and the Welcome back sheet (app/notify.py, Glenn's pick A, Nov 14 2026)."""
import asyncio
import time

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests import mock_seerr as M
from tests.test_requests import env  # noqa: F401  (fixture)


def principal(pid, role="viewer", kind="plex"):
    from app.auth import Principal
    return Principal(kind=kind, subject=pid, name=pid, role=role, sections=["requests"])


def notifier(env, **kw):
    from app.billing import Billing
    from app.notify import Notifier
    store, d, svc = env
    return Notifier(store, seerr=svc, directory=d, billing=Billing(store), **kw)


def req(rid, by=5, status=2, media_status=3, tmdb=27205, kind="movie", seasons=()):
    M.state["requests"].append({"id": rid, "type": kind, "status": status, "is4k": False, "by": by,
                                "created": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "media": {"tmdbId": tmdb},
                                "media_status": media_status, "seasons": list(seasons)})


def tick(n, now=None):
    asyncio.run(n.tick(now))


def test_first_run_is_silent_then_each_move_is_told_once_to_the_requester_only(env):
    store, d, svc = env
    svc.save_link("2001", 5, "email")          # sable (Seerr 5) is Plex 2001
    req(7)                                      # already approved before notifications existed
    n = notifier(env)
    tick(n)
    sable, other = principal("2001"), principal("2002")
    assert n.items_for(sable)["items"] == []    # nothing old announced
    M.state["requests"][0]["media_status"] = 5  # on Plex now
    svc.clear_cache()
    tick(n)
    got = n.items_for(sable)
    assert [x["title"] for x in got["items"]] == ["Inception is ready"]
    assert got["items"][0]["href"] == "#/requests/movie/27205" and got["unseen"] == 1 and got["welcome"]
    assert n.items_for(other)["items"] == []    # never someone else's request
    tick(n)
    assert len(n.items_for(sable)["items"]) == 1  # told once


def test_failed_request_and_a_new_one_after_seeding(env):
    store, d, svc = env
    svc.save_link("2001", 5, "email")
    n = notifier(env)
    tick(n)                                     # seeded with nothing
    req(8, status=4)                            # a new request that failed
    svc.clear_cache()
    tick(n)
    items = n.items_for(principal("2001"))["items"]
    assert items[0]["kind"] == "problem" and "could not be downloaded" in items[0]["title"]


def test_welcome_opens_once_per_visit_and_for_new_news(env):
    from app.notify import ADMINS, WELCOME_GAP
    store, d, svc = env
    n = notifier(env)
    p = principal("2001")
    now = int(time.time())
    n.post("plex:2001", "x:1", "ready", "One is ready", now=now)
    assert n.items_for(p, now)["welcome"]
    n.shown(p, now)                              # the page opened it ("Remind me next time" leaves it unseen)
    assert not n.items_for(p, now + 60)["welcome"]
    n.post("plex:2001", "x:2", "started", "Two started", now=now + 120)
    assert n.items_for(p, now + 180)["welcome"]  # something new since
    n.shown(p, now + 180)
    assert n.items_for(p, now + 180 + WELCOME_GAP + 1)["welcome"]  # a new visit, still unseen
    assert n.mark_seen(p, None, now + 200) == 2
    got = n.items_for(p, now + 300)
    assert got["unseen"] == 0 and not got["welcome"] and got["since"] == now + 200
    # admins' shared ones are not a member's
    n.post(ADMINS, "needs-you", "needs", "1 request needs you", urgent=True, now=now)
    assert all(x["kind"] != "needs" for x in n.items_for(p, now + 400)["items"])


def test_billing_problem_is_pinned_until_paid_and_admins_get_names(env):
    store, d, svc = env
    d.upsert_user({"id": "2001", "username": "sable", "email": "s@x.com"}, shared=True)
    store.set_setting("stripe_key", "rk_test_x")
    db = store.db
    from app.billing import Billing
    Billing(store)
    db.execute("INSERT INTO stripe_customers VALUES ('cus_1', 's@x.com', 'Sable', 1)")
    db.execute("INSERT INTO stripe_subscriptions (id, customer, status, plan, created) VALUES ('sub_1', 'cus_1', 'past_due', 'Plex', 1)")
    db.execute("INSERT INTO stripe_charges (id, customer, email, amount, currency, status, refunded, created) "
               "VALUES ('ch_1', 'cus_1', 's@x.com', 1000, 'usd', 'failed', 0, 1794500000)")
    db.commit()
    n = notifier(env)
    n.billing_tick(int(time.time()))
    member, boss = principal("2001"), principal("cluster", role="admin")
    m = n.items_for(member)
    assert m["pinned"] == 1 and m["items"][0]["title"] == "Your payment did not go through" and "declined" in m["items"][0]["body"]
    n.mark_seen(member)
    assert n.items_for(member)["pinned"] == 1          # seen, but still pinned until it is paid
    a = n.items_for(boss)
    bill = next(x for x in a["items"] if x["kind"] == "billing")
    assert bill["title"] == "1 payment failed" and "sable" in bill["body"] and bill["pinned"]
    db.execute("UPDATE stripe_subscriptions SET status = 'active'")
    db.commit()
    n.billing_tick(int(time.time()))
    m = n.items_for(member)
    assert m["pinned"] == 0 and m["items"][0]["title"] == "Your payment went through" and not m["items"][0]["seen"]
    assert next(x for x in n.items_for(boss)["items"] if x["kind"] == "billing")["resolved"]


def test_server_down_then_back_up(env):
    from app.notify import ADMINS

    class P:
        since = {}
    store, d, svc = env
    sid = store.add_server({"name": "Atlas", "plex_url": "http://atlas:32400", "enabled": 1}, None)
    poller = P()
    n = notifier(env, poller=poller)
    t0 = int(time.time())
    poller.since = {str(sid): {"online": False, "since": t0}}
    n.admin_tick(t0 + 60)
    boss = principal("cluster", role="admin")
    down = [x for x in n.items_for(boss)["items"] if x["kind"] == "server"]
    assert down[0]["title"] == "Atlas is down" and down[0]["pinned"]
    poller.since = {str(sid): {"online": True, "since": t0 + 14 * 60}}
    n.admin_tick(t0 + 15 * 60)
    back = [x for x in n.items_for(boss)["items"] if x["kind"] == "server"]
    assert back[0]["title"] == "Atlas was down for 14 minutes" and not back[0]["pinned"]
    assert n.open_keys(ADMINS, "server:") == []


def test_routes_are_own_only(env):
    from app.notify import make_router
    store, d, svc = env
    n = notifier(env)
    n.post("plex:2001", "x:1", "ready", "Mine")
    n.post("plex:2002", "x:1", "ready", "Theirs")
    who = {"p": principal("2001")}
    a = FastAPI()
    a.include_router(make_router(n, lambda request: who["p"]))
    c = TestClient(a)
    body = c.get("/api/notifications").json()
    assert [x["title"] for x in body["items"]] == ["Mine"]
    theirs = store.db.execute("SELECT id FROM notifications WHERE title = 'Theirs'").fetchone()["id"]
    assert c.post("/api/notifications/seen", json={"ids": [theirs]}).json()["seen"] == 0  # not theirs to mark
    assert c.post("/api/notifications/seen", json={}).json()["seen"] == 1
    assert c.get("/api/notifications").json()["unseen"] == 0
    assert c.post("/api/notifications/shown").json()["ok"]


def test_a_server_deleted_or_turned_off_while_down_does_not_stay_pinned(env):
    """Glenn's pick 1A (Nov 15 2026): a notice whose cause is gone is cleared, never left pinned forever."""
    class P:
        since = {}
    store, d, svc = env
    a = store.add_server({"name": "Atlas", "plex_url": "http://atlas:32400", "enabled": 1}, None)
    b = store.add_server({"name": "Beta", "plex_url": "http://beta:32400", "enabled": 1}, None)
    poller = P()
    n = notifier(env, poller=poller)
    t0 = int(time.time())
    poller.since = {str(a): {"online": False, "since": t0}, str(b): {"online": False, "since": t0}}
    n.admin_tick(t0 + 60)
    boss = principal("cluster", role="admin")
    assert n.items_for(boss)["pinned"] == 2
    store.delete_server(a)                                  # deleted while down: taken back
    store.update_server(b, {"enabled": 0})                  # turned off while down: closed with a word
    n.admin_tick(t0 + 3600)
    got = n.items_for(boss)
    assert got["pinned"] == 0
    assert [x["title"] for x in got["items"] if x["kind"] == "server"] == ["Beta is turned off"]


def test_removing_the_stripe_key_takes_back_payment_notices(env):
    store, d, svc = env
    n = notifier(env)
    n.post("plex:2001", "billing:payment", "billing", "Your payment did not go through", urgent=True)
    n.post("admins", "billing:failed", "billing", "1 payment failed", urgent=True)
    store.set_setting("stripe_key", None)
    n.billing_tick(int(time.time()) + 60)
    assert n.items_for(principal("2001"))["items"] == []
    assert [x for x in n.items_for(principal("cluster", role="admin"))["items"] if x["kind"] == "billing"] == []


def test_reported_issues_are_pinned_for_admins_until_resolved(env):
    from app.notify import ADMINS
    store, d, svc = env
    M.state["issues"].append({"id": 23, "type": 1, "message": "It keeps giving an error that it is only in 4K", "mediaId": 27205, "by": 5})
    n = notifier(env)
    tick(n)                                              # first run: an issue already open is still shown
    boss, member = principal("cluster", role="admin"), principal("2001")
    got = [x for x in n.items_for(boss)["items"] if x["kind"] == "issue"]
    assert len(got) == 1 and got[0]["pinned"] and got[0]["href"] == "#/requests/issues"
    assert got[0]["title"] == "sable reported a video problem with Inception" and "only in 4K" in got[0]["body"]
    assert all(x["kind"] != "issue" for x in n.items_for(member)["items"])   # admins only
    tick(n)
    assert len([x for x in n.items_for(boss)["items"] if x["kind"] == "issue"]) == 1   # not repeated
    M.state["issues"].clear()                            # resolved in Seerr
    svc.clear_cache()
    tick(n)
    done = [x for x in n.items_for(boss)["items"] if x["kind"] == "issue"]
    assert done[0]["title"] == "Issue resolved: Inception" and not done[0]["pinned"] and done[0]["resolved"]
    assert n.open_keys(ADMINS, "issue:") == []
    M.state["issues"].append({"id": 24, "type": 4, "message": "Unable to load content", "mediaId": 27205, "by": 5})
    M.state["issues"].append({"id": 25, "type": 2, "message": "", "mediaId": 27205, "by": 5})
    svc.clear_cache()
    tick(n)
    t = {x["title"] for x in n.items_for(boss)["items"] if x["kind"] == "issue" and x["pinned"]}
    assert t == {"sable reported a problem with Inception", "sable reported an audio problem with Inception"}


def _ago(days):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - days * 86400))


def test_notify_me_too_followers_hear_the_same_news(env):
    store, d, svc = env
    svc.save_link("2001", 5, "email")            # the requester
    req(9)
    n = notifier(env)
    tick(n)                                      # seeded
    svc.help.watch(9, "3003", {"kind": "movie", "tmdbId": 27205, "title": "Inception"})
    svc.help.watch(9, "2001", {"kind": "movie", "tmdbId": 27205, "title": "Inception"})  # the requester too: told once
    M.state["requests"][-1]["media_status"] = 5
    svc.clear_cache()
    tick(n)
    assert [x["title"] for x in n.items_for(principal("3003"))["items"]] == ["Inception is ready"]
    assert [x["title"] for x in n.items_for(principal("2001"))["items"]] == ["Inception is ready"]
    assert n.items_for(principal("4004"))["items"] == []


def test_a_request_older_than_request_days_is_followed_until_it_is_on_plex(env):
    store, d, svc = env
    svc.save_link("2001", 5, "email")
    n = notifier(env)
    tick(n)                                      # seeded with nothing
    req(10)                                      # waiting for its release, asked for 80 days ago
    M.state["requests"][-1]["created"] = _ago(80)
    req(11, media_status=5)                      # old and on Plex long ago: never news
    M.state["requests"][-1]["created"] = _ago(90)
    M.state["requests"][-1]["media_extra"] = {"mediaAddedAt": _ago(60)}
    svc.clear_cache()
    tick(n)                                      # first look at the old one: silent
    assert n.items_for(principal("2001"))["items"] == []
    M.state["requests"][-2]["media_status"] = 5  # released and on Plex now
    M.state["requests"][-2]["media_extra"] = {"mediaAddedAt": _ago(0)}
    svc.clear_cache()
    tick(n)
    assert [x["title"] for x in n.items_for(principal("2001"))["items"]] == ["Inception is ready"]


def test_an_old_request_that_just_arrived_before_it_was_ever_seen_is_still_told(env):
    store, d, svc = env
    svc.save_link("2001", 5, "email")
    n = notifier(env)
    tick(n)
    req(12, media_status=5)
    M.state["requests"][-1]["created"] = _ago(78)
    M.state["requests"][-1]["media_extra"] = {"mediaAddedAt": _ago(0.2)}
    svc.help.watch(12, "3003", {"kind": "movie", "tmdbId": 27205, "title": "Inception"})
    svc.clear_cache()
    tick(n)
    assert [x["title"] for x in n.items_for(principal("2001"))["items"]] == ["Inception is ready"]
    assert [x["title"] for x in n.items_for(principal("3003"))["items"]] == ["Inception is ready"]
