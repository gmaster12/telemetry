"""Sign-up, step by step: nobody is invited before Stripe says they paid, nobody is invited twice, and a paid
sign-up that cannot be finished is never dropped quietly."""
import asyncio
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from tests import mock_signup as M


class FakeNotifier:
    def __init__(self):
        self.posts, self.resolved, self.withdrawn = [], [], []

    def post(self, who, key, kind, title, body=None, action=None, href=None, urgent=False, **kw):
        self.posts.append({"who": who, "key": key, "title": title, "body": body, "urgent": urgent})

    def resolve(self, who, key, title=None, body=None, now=None):
        self.resolved.append(key)

    def withdraw(self, who, key):
        self.withdrawn.append(key)


class FakePoller:
    def __init__(self):
        self.users_synced_at, self.kicks = 5, 0

    def kick(self):
        self.kicks += 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    from app.billing import Billing
    from app.secrets import SecretBox
    from app.signup import Signup
    from app.store import Directory, Store
    M.reset()
    store = Store(str(tmp_path / "t.db"), SecretBox())
    d = Directory(store)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=M.app))
    sid = store.add_server({"name": "GALAXY", "plex_url": "http://galaxy.test:32400", "machine_id": M.MACHINE, "owner_plex_id": "900"}, "srv-token")
    store.save_account({"id": "900", "username": "galaxyowner", "title": "Galaxy Owner", "email": "owner@greystar.test"}, M.OWNER_TOKEN)
    svc = Signup(store, d, http, lambda: "test-client", billing=Billing(store))
    svc.notifier, svc.poller = FakeNotifier(), FakePoller()
    for k, v in {"signup_server_id": str(sid), "signup_sections": json.dumps(["11", "12"]), "signup_price_id": M.PRICE,
                 "signup_plan": json.dumps({"id": M.PRICE, "amount": 1599, "currency": "usd", "interval": "month", "interval_count": 1}),
                 "signup_publishable_key": M.PUBLISHABLE, "signup_stripe_key": M.STRIPE_KEY, "signup_cap": "100", "signup_enabled": "1"}.items():
        store.set_setting(k, v)
    return store, d, svc


def run(coro):
    return asyncio.run(coro)


def newbie():
    return dict(M.state["people"]["tok-new"], id="3001", email="newbie@example.com")


def start(svc, info=None, token="tok-new"):
    out, cookie = run(svc.begin(info or newbie(), token, "203.0.113.5"))
    assert out == "go", out
    return svc.by_cookie(cookie)


def open_checkout(svc, row):
    out = run(svc.checkout(row, "https://streaming.test/"))
    return out, svc.get(row["id"])


def what(name):
    return [c for c in M.calls if c["what"] == name]


def test_the_whole_way_through(env):
    store, d, svc = env
    row = start(svc)
    assert row["state"] == "plex_ok" and "tok-new" not in (row["plex_token_enc"] or "") and store.box.open(row["plex_token_enc"]) == "tok-new"
    out, row = open_checkout(svc, row)
    form = what("checkout")[0]["form"]
    assert out["client_secret"].startswith("cs_test_") and row["state"] == "checkout_open"
    assert form["mode"] == "subscription" and form["ui_mode"] == "embedded" and form["line_items[0][price]"] == M.PRICE
    assert form["customer_email"] == "newbie@example.com" and form["client_reference_id"] == row["id"]
    assert form["subscription_data[metadata][plex_id]"] == "3001" and form["return_url"] == "https://streaming.test/signup.html"
    assert what("checkout")[0]["idem"] == f"signup-{row['id']}-1"
    M.pay(row["checkout_session"])
    row = run(svc.advance(row["id"]))
    assert row["state"] == "active" and row["plex_token_enc"] is None and row["subscription"].startswith("sub_") and row["customer"].startswith("cus_")
    inv = what("invite")
    assert len(inv) == 1 and inv[0]["mid"] == M.MACHINE
    assert inv[0]["body"]["shared_server"] == {"library_section_ids": [11, 12], "invited_email": "newbie@example.com"}
    assert len(what("accept")) == 1 and what("accept")[0]["params"] == {"friend": "1", "home": "0", "server": "1"}
    assert [s for s in M.state["shares"] if s["userID"] == "3001"][0]["sections"] == ["11", "12"]   # not the 4k library
    u = d.get_user("3001")
    assert u["shared"] == 1 and json.loads(u["servers"]) == ["GALAXY"] and u["email"] == "newbie@example.com"
    assert svc.notifier.posts[-1]["title"] == "New sign-up: New Bie" and not svc.notifier.posts[-1]["urgent"]
    assert svc.poller.users_synced_at == 0 and svc.poller.kicks == 1
    assert svc.view(row)["step"] == "done"


def test_nobody_is_invited_before_stripe_says_paid(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    assert run(svc.advance(row["id"]))["state"] == "checkout_open"
    s = M.state["sessions"][row["checkout_session"]]
    s.update(status="complete", payment_status="unpaid", subscription={"id": "sub_x", "status": "active"})   # the money has not arrived
    assert run(svc.advance(row["id"]))["state"] == "checkout_open"
    s.update(payment_status="paid", subscription={"id": "sub_x", "status": "incomplete"})   # paid, but the subscription is not running
    assert run(svc.advance(row["id"]))["state"] == "checkout_open"
    s.update(status="open", subscription={"id": "sub_x", "status": "active"})   # paid and running, but the checkout never finished
    assert run(svc.advance(row["id"]))["state"] == "checkout_open"
    assert what("invite") == [] and svc.view(svc.get(row["id"]))["step"] == "pay"


def test_a_checkout_that_belongs_to_someone_else_is_refused(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    other = "cs_test_other"
    M.state["sessions"][other] = {"id": other, "status": "complete", "payment_status": "paid", "mode": "subscription",
                                  "client_reference_id": "somebody-else", "customer": "cus_o", "subscription": {"id": "sub_o", "status": "active"}}
    svc._set(row["id"], checkout_session=other)
    assert run(svc.advance(row["id"]))["state"] == "checkout_open" and what("invite") == []


def test_paid_but_plex_refuses_the_admins_hear_once_and_it_keeps_trying(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["invite"] = 500
    for n in (1, 2):
        row = run(svc.advance(row["id"]))
        assert row["state"] == "paid" and row["attempts"] == n and not row["alerted"]
    assert svc.notifier.posts == []
    row = run(svc.advance(row["id"]))
    assert row["attempts"] == 3 and row["alerted"] == 1 and "HTTP 500" in row["last_error"]
    assert [p["title"] for p in svc.notifier.posts] == ["Paid sign-up needs you: New Bie"] and svc.notifier.posts[0]["urgent"]
    assert svc.view(row) == {"name": "New Bie", "email": "newbie@example.com", "server": "GALAXY", "step": "working", "stuck": True, "accept": False}
    run(svc.advance(row["id"]))
    assert len(svc.notifier.posts) == 1   # told once, not every try
    now = int(time.time())
    assert not svc.due(svc.get(row["id"]), now + 120) and svc.due(svc.get(row["id"]), now + 3700)   # hourly once stuck
    M.state["fail"].clear()
    row = run(svc.advance(row["id"]))
    assert row["state"] == "active" and row["alerted"] == 0 and svc.notifier.resolved == [f"signup:stuck:{row['id']}"]


def test_once_they_are_in_nothing_more_is_sent_to_plex(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    run(svc.advance(row["id"]))
    before = len(M.calls)
    assert run(svc.advance(row["id"]))["state"] == "active" and run(svc.sweep()) == 0
    assert len(M.calls) == before


def test_closing_the_tab_after_paying_loses_nothing(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    assert run(svc.sweep(int(time.time()) + 61)) == 1 and svc.get(row["id"])["state"] == "active"


def test_coming_back_mid_way_carries_on_and_never_opens_a_second_checkout(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    again = start(svc)   # same Plex account, new browser
    assert again["id"] == row["id"] and again["state"] == "checkout_open" and again["token_hash"] != row["token_hash"]
    out, again = open_checkout(svc, again)
    assert out["client_secret"] == row["checkout_session"] + "_secret_abc" and len(what("checkout")) == 1
    M.pay(row["checkout_session"])
    assert run(svc.checkout(again, "https://streaming.test/")) == {"paid": True} and len(what("checkout")) == 1
    M.state["fail"]["invite"] = 500
    run(svc.advance(row["id"]))
    M.state["shares"].clear()   # even with sign-ups shut, someone who paid is carried through
    env[0].set_setting("signup_enabled", None)
    out, _ = run(svc.begin(newbie(), "tok-new", None))
    assert out == "go"


def test_an_expired_checkout_starts_over_with_a_new_one(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.state["sessions"][row["checkout_session"]]["status"] = "expired"
    row = run(svc.advance(row["id"]))
    assert row["state"] == "plex_ok" and row["checkout_session"] is None
    _, row2 = open_checkout(svc, row)
    assert row2["checkout_session"] != None and [c["idem"] for c in what("checkout")] == [f"signup-{row['id']}-1", f"signup-{row['id']}-2"]


def test_who_is_turned_away_before_any_payment(env):
    store, d, svc = env
    sable = dict(M.state["people"]["tok-old"], id="2001")
    assert run(svc.begin(sable, "tok-old", None)) == ("member", None)             # already on a server
    assert run(svc.begin(dict(newbie(), email="Cluster@Mail.com"), "t", None)) == ("member", None)   # an administrator
    assert run(svc.begin(dict(newbie(), email=None), "t", None)) == ("noemail", None)
    d.upsert_user({"id": "3001", "username": "newbie"})
    d.set_user_access("3001", disabled=True)
    assert run(svc.begin(newbie(), "tok-new", None)) == ("disabled", None)
    d.set_user_access("3001", disabled=False)
    svc.billing.save_snapshot({"customers": [{"id": "cus_n", "email": "NEWBIE@example.com", "created": 1}], "products": {}, "charges": [],
                               "subscriptions": [{"id": "sub_n", "customer": "cus_n", "status": "active", "items": {"data": []}, "created": 1}]})
    assert run(svc.begin(newbie(), "tok-new", None)) == ("paying", None)          # already has a subscription
    svc.billing.save_snapshot({"customers": [{"id": "cus_n", "email": "newbie@example.com", "created": 1}], "products": {}, "charges": [],
                               "subscriptions": [{"id": "sub_n", "customer": "cus_n", "status": "canceled", "items": {"data": []}, "created": 1}]})
    assert run(svc.begin(newbie(), "tok-new", None))[0] == "go"                   # a canceled one does not count
    assert what("checkout") == [] and what("invite") == []


def test_closed_full_or_plex_not_answering(env):
    store, _, svc = env
    store.set_setting("signup_cap", "1")   # one share is there already
    svc._capacity = None
    assert run(svc.availability()) == "full" and run(svc.begin(newbie(), "tok-new", None)) == ("full", None)
    store.set_setting("signup_cap", "3")
    M.state["invites"].append({"id": "77", "email": "waiting@x.com", "sections": ["11"]})   # an invite still waiting takes a place too
    svc._capacity = None
    cap = run(svc.capacity())
    assert (cap["shares"], cap["invites"], cap["used"], cap["full"]) == (1, 1, 2, False)
    row = start(svc)
    open_checkout(svc, row)   # someone at the payment form holds the last place
    assert run(svc.capacity())["used"] == 3 and run(svc.availability()) == "full"
    store.set_setting("signup_cap", "100")
    M.state["fail"]["shares"] = 503
    svc._capacity = None
    assert run(svc.availability()) == "busy"
    M.state["fail"].clear()
    store.set_setting("signup_enabled", None)
    assert run(svc.availability()) == "off"
    store.set_setting("signup_enabled", "1")
    store.set_setting("signup_sections", "[]")
    assert run(svc.availability()) == "setup" and "Choose at least one library to share." in svc.problems()


def test_plex_not_answering_when_they_sign_in_takes_no_chances(env):
    _, _, svc = env
    M.state["fail"]["users"] = 503   # cannot tell whether they are a member already
    assert run(svc.begin(newbie(), "tok-new", None)) == ("busy", None)


def test_an_invite_that_was_already_waiting_is_used_not_sent_again(env):
    _, _, svc = env
    M.state["invites"].append({"id": "88", "email": "newbie@example.com", "sections": ["11"]})   # invited by hand earlier
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    assert run(svc.advance(row["id"]))["state"] == "active" and what("invite") == [] and what("accept")[0]["id"] == "88"


def test_someone_plex_shares_with_at_once_needs_no_accepting(env):
    _, _, svc = env
    M.state["instant"].add("newbie@example.com")
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    assert run(svc.advance(row["id"]))["state"] == "active" and what("accept") == []


def test_only_the_owners_invite_is_accepted(env):
    _, _, svc = env
    M.state["other_invites"] = ["999"]   # a stranger also invited this person to something
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    run(svc.advance(row["id"]))
    assert [c["id"] for c in what("accept")] != ["999"] and len(what("accept")) == 1


def test_the_invite_is_still_found_when_plex_names_the_sender_differently(env):
    _, _, svc = env
    M.state["people"][M.OWNER_TOKEN].update(username="GalaxyC58-renamed", email="other@greystar.test")   # not what we have saved
    M.state["other_invites"] = ["999"]
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    assert run(svc.advance(row["id"]))["state"] == "active" and [c["id"] for c in what("accept")] != ["999"]


def test_a_lost_answer_from_stripe_does_not_wedge_the_next_try(env):
    _, _, svc = env
    row = start(svc)
    M.state["fail"]["checkout"] = 500
    with pytest.raises(Exception):
        run(svc.checkout(row, "https://streaming.test/"))
    M.state["fail"].clear()
    out, row = open_checkout(svc, svc.get(row["id"]))
    assert out["client_secret"] and [c["idem"] for c in what("checkout")] == [f"signup-{row['id']}-1", f"signup-{row['id']}-2"]


def test_when_their_plex_sign_in_has_run_out_they_accept_it_themselves(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["requests"] = 401
    row = run(svc.advance(row["id"]))
    assert row["state"] == "invited" and row["plex_token_enc"] is None and svc.view(row)["accept"] is True
    M.state["fail"].clear()
    assert run(svc.advance(row["id"]))["state"] == "invited" and what("accept") == []
    M.accept(M.state["invites"][0]["id"])   # they clicked Accept in Plex's email
    assert run(svc.sweep(int(time.time()) + 61)) == 1 and svc.get(row["id"])["state"] == "active"


def test_plex_having_a_moment_does_not_throw_their_sign_in_away(env):
    store, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["requests"] = 503
    row = run(svc.advance(row["id"]))
    assert row["state"] == "invited" and store.box.open(row["plex_token_enc"]) == "tok-new" and row["attempts"] == 1
    M.state["fail"].clear()
    assert run(svc.advance(row["id"]))["state"] == "active"


def test_an_invite_that_disappeared_is_sent_again_but_not_straight_away(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["accept"] = 500
    row = run(svc.advance(row["id"]))
    assert row["state"] == "invited"
    M.state["fail"].clear()
    M.state["invites"].clear()   # declined, or removed by hand
    assert run(svc.advance(row["id"]))["state"] == "invited" and len(what("invite")) == 1   # plex.tv may just be slow to list it
    svc._set(row["id"], invited_at=int(time.time()) - 600)
    assert run(svc.advance(row["id"]))["state"] == "paid"
    assert run(svc.advance(row["id"]))["state"] == "active" and len(what("invite")) == 2


def test_the_sweep_tidies_up(env):
    store, _, svc = env
    row = start(svc)
    now = int(time.time())
    run(svc.sweep(now + 3600))
    assert svc.get(row["id"]) is not None
    run(svc.sweep(now + 90000))   # never paid: gone after a day, Plex sign-in and all
    assert svc.get(row["id"]) is None
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["requests"] = 503
    run(svc.advance(row["id"]))
    M.state["fail"]["shares"] = 503   # nothing can be checked while the sweep runs
    run(svc.sweep(now + 8 * 86400))
    row = svc.get(row["id"])
    assert row["plex_token_enc"] is None   # nobody's Plex sign-in is kept past a week
    assert row["alerted"] == 1 and "still have not accepted" in svc.notifier.posts[-1]["body"]


def test_the_page_is_never_told_secrets(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    run(svc.advance(row["id"]))
    text = json.dumps([svc.view(svc.get(row["id"])), svc.rows(), svc.settings()])
    for secret in ("tok-new", M.STRIPE_KEY, M.OWNER_TOKEN, "SECRET-ACCESS-TOKEN", row["token_hash"]):
        assert secret not in text


def test_stripe_key_is_saved_encrypted(env):
    store, _, _ = env
    raw = store.db.execute("SELECT value FROM settings WHERE key = 'signup_stripe_key'").fetchone()["value"]
    assert M.STRIPE_KEY not in raw and store.get_setting("signup_stripe_key") == M.STRIPE_KEY
