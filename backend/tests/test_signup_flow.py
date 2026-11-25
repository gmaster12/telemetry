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
    assert what("invite") == [] and svc.view(svc.get(row["id"]))["step"] == "pay" and svc.get(row["id"])["paid_at"] is None


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
    assert svc.view(row) == {"name": "New Bie", "email": "newbie@example.com", "server": "GALAXY", "step": "working", "confirming": False,
                             "stuck": True, "accept": False}
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


def test_a_strangers_invite_is_never_accepted_even_from_a_server_with_the_same_name(env):
    _, _, svc = env
    M.state["people"][M.OWNER_TOKEN].update(username="renamed", email="other@greystar.test")   # so the owner's own invite is not recognised
    M.state["other_invites"] = ["999"]   # a stranger's server is also called GALAXY
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    row = run(svc.advance(row["id"]))
    assert row["state"] == "invited" and what("accept") == []   # they accept it themselves; nobody else's is touched


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


# ---------- Stripe test keys: nothing is charged, so only listed test accounts get in ----------
def use_test_keys(store, testers=None):
    store.set_setting("signup_stripe_key", M.TEST_KEY)
    store.set_setting("signup_publishable_key", M.TEST_PUBLISHABLE)
    store.set_setting("signup_testers", testers)


def test_with_test_keys_a_test_card_does_not_buy_real_access(env):
    store, _, svc = env
    use_test_keys(store)
    assert svc.settings()["mode"] == "test" and svc.problems() == [] and run(svc.availability()) == "test"
    assert run(svc.begin(newbie(), "tok-new", None)) == ("off", None) and svc.rows() == []
    use_test_keys(store, "Someone@Else.com, newbie@example.com")
    assert run(svc.availability()) == "test" and run(svc.availability("newbie@example.com")) is None
    _, row = open_checkout(svc, start(svc))
    assert M.state["sessions"][row["checkout_session"]]["livemode"] is False
    M.pay(row["checkout_session"])
    assert run(svc.advance(row["id"]))["state"] == "active"   # a listed test account goes all the way, to prove the flow


def test_a_test_mode_payment_is_never_honoured_for_anyone_else(env):
    store, _, svc = env
    use_test_keys(store, "newbie@example.com")
    _, row = open_checkout(svc, start(svc))
    use_test_keys(store, None)   # taken off the list while the form was open
    M.pay(row["checkout_session"])
    row = run(svc.advance(row["id"]))
    assert row["state"] == "checkout_open" and row["pending"] and what("invite") == []
    assert run(svc.sweep(int(time.time()) + 61)) == 0 and svc.get(row["id"])["state"] == "checkout_open"


# ---------- what the review found ----------
def test_coming_back_never_changes_who_the_invite_is_for(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    out, cookie = run(svc.begin(dict(newbie(), email="somebody.else@example.com"), "tok-new", None))
    again = svc.by_cookie(cookie)
    assert out == "go" and again["id"] == row["id"] and again["email"] == "newbie@example.com"
    M.pay(row["checkout_session"])
    run(svc.advance(row["id"]))
    assert [c["body"]["shared_server"]["invited_email"] for c in what("invite")] == ["newbie@example.com"]


def test_one_payment_sends_at_most_two_invites_by_itself(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["accept"] = 500
    for expect in (1, 2):
        row = run(svc.advance(row["id"]))                    # invited (again); accepting fails
        assert row["state"] == "invited" and len(what("invite")) == expect
        M.state["invites"].clear()                           # the invite is declined
        svc._set(row["id"], invited_at=int(time.time()) - 600)
        assert run(svc.advance(row["id"]))["state"] == "paid"
    for _ in range(4):
        row = run(svc.advance(row["id"]))
    assert len(what("invite")) == 2 and row["state"] == "paid" and row["alerted"] == 1 and "Not sending another" in row["last_error"]
    assert [p["title"] for p in svc.notifier.posts] == ["Paid sign-up needs you: New Bie"]
    M.state["fail"].clear()
    svc._set(row["id"], attempts=0, invites=0)               # what Try again now does
    assert run(svc.advance(row["id"]))["state"] == "active" and len(what("invite")) == 3


def test_any_kind_of_error_is_counted_and_never_stops_the_others(env, monkeypatch):
    _, _, svc = env
    _, a = open_checkout(svc, start(svc))
    _, b = open_checkout(svc, start(svc, dict(M.state["people"]["tok-two"], id="3002"), "tok-two"))
    M.pay(a["checkout_session"]); M.pay(b["checkout_session"])
    real = svc._invite

    async def broken(row):
        if row["id"] == a["id"]:
            raise KeyError("something nobody planned for")
        return await real(row)
    monkeypatch.setattr(svc, "_invite", broken)
    now = int(time.time())
    for i in (1, 2, 3):
        run(svc.sweep(now + 61 * i))
    ra, rb = svc.get(a["id"]), svc.get(b["id"])
    assert ra["state"] == "paid" and ra["attempts"] == 3 and ra["alerted"] == 1 and "planned" in ra["last_error"]
    assert rb["state"] == "active"

    async def worse(sid, min_gap=0):
        raise RuntimeError("the sweep itself must survive this")
    monkeypatch.setattr(svc, "advance", worse)
    svc._set(a["id"], touch=False, checked=0)
    assert run(svc.sweep(now + 9000)) == 0   # no exception escapes


def test_someone_who_left_but_still_pays_is_not_charged_again(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    row = run(svc.advance(row["id"]))
    M.state["shares"] = [s for s in M.state["shares"] if s["userID"] != "3001"]   # removed from the server by hand
    assert run(svc.begin(newbie(), "tok-new", None)) == ("paying", None)          # Stripe says the subscription still runs
    M.state["fail"]["sub"] = 500
    assert run(svc.begin(newbie(), "tok-new", None)) == ("busy", None)            # cannot tell: take no chances
    M.state["fail"].clear()
    M.state["subs"][row["subscription"]] = "canceled"
    out, cookie = run(svc.begin(newbie(), "tok-new", None))
    fresh = svc.by_cookie(cookie)
    assert out == "go" and fresh["state"] == "plex_ok" and fresh["subscription"] is None and fresh["invites"] == 0
    assert len(what("checkout")) == 1


def test_a_finished_form_without_confirmed_money_waits_and_the_admins_hear(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    s = M.state["sessions"][row["checkout_session"]]
    s.update(status="complete", payment_status="unpaid", subscription={"id": "sub_slow", "status": "incomplete"})
    assert run(svc.checkout(row, "https://streaming.test/")) == {"paid": True} and len(what("checkout")) == 1   # no second form
    row = run(svc.advance(row["id"]))
    v = svc.view(row)
    assert row["state"] == "checkout_open" and row["pending"] and v["step"] == "working" and v["confirming"] is True and what("invite") == []
    now = int(time.time())
    run(svc.sweep(now + 600))
    assert svc.notifier.posts == []
    run(svc.sweep(now + 4000))
    assert [p["title"] for p in svc.notifier.posts] == ["Sign-up payment not confirmed: New Bie"] and svc.counts()["stuck"] == 1
    run(svc.sweep(now + 8000))
    assert len(svc.notifier.posts) == 1
    s.update(payment_status="paid", subscription={"id": "sub_slow", "status": "active"})
    run(svc.sweep(now + 9000))
    assert svc.get(row["id"])["state"] == "active"


def test_admins_hear_when_stripe_cannot_be_asked_about_payments(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["session"] = 403   # say the key cannot read the checkout
    now = int(time.time())
    run(svc.sweep(now + 61)); run(svc.sweep(now + 122))
    assert svc.notifier.posts == [] and svc.get(row["id"])["state"] == "checkout_open"
    run(svc.sweep(now + 183)); run(svc.sweep(now + 244))
    assert {p["key"] for p in svc.notifier.posts} == {"signup:stripe"} and svc.notifier.posts[0]["urgent"]
    M.state["fail"].clear()
    run(svc.sweep(now + 305))
    assert svc.get(row["id"])["state"] == "active" and "signup:stripe" in svc.notifier.resolved


def test_a_share_plex_has_not_marked_accepted_does_not_count(env):
    _, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["shares"].append({"id": "600", "userID": "3001", "username": "newbie", "email": "newbie@example.com", "sections": ["11"], "acceptedAt": ""})
    M.state["fail"]["requests"] = 503
    row = run(svc.advance(row["id"]))
    assert row["state"] == "invited" and what("invite") == [] and store_token(env) == "tok-new"
    M.state["shares"][-1]["acceptedAt"] = "1700000000"
    assert run(svc.advance(row["id"]))["state"] == "active"


def store_token(env):
    store, _, svc = env
    return store.box.open(svc.rows() and svc.get(svc.rows()[0]["id"])["plex_token_enc"])


def test_a_stuck_sign_up_still_loses_its_plex_sign_in_after_a_week(env):
    store, _, svc = env
    _, row = open_checkout(svc, start(svc))
    M.pay(row["checkout_session"])
    M.state["fail"]["requests"] = 503
    run(svc.advance(row["id"]))
    with store.lock:
        store.db.execute("UPDATE signups SET created = created - 8 * 86400 WHERE id = ?", (row["id"],))
        store.db.commit()
    run(svc.advance(row["id"]))   # it was looked at a moment ago, as a stuck one always is
    run(svc.sweep())
    assert svc.get(row["id"])["plex_token_enc"] is None


def test_the_sign_up_cookie_runs_out(env):
    store, _, svc = env
    out, cookie = run(svc.begin(newbie(), "tok-new", None))
    assert svc.by_cookie(cookie) is not None
    run(svc.advance(svc.by_cookie(cookie)["id"]))
    with store.lock:
        store.db.execute("UPDATE signups SET updated = updated - 8 * 86400")
        store.db.commit()
    assert svc.by_cookie(cookie) is None


def test_two_tabs_at_once_make_one_sign_up(env):
    _, _, svc = env

    async def both():
        return await asyncio.gather(svc.begin(newbie(), "tok-new", None), svc.begin(newbie(), "tok-new", None))
    outs = run(both())
    assert [o for o, _ in outs] == ["go", "go"] and len(svc.rows()) == 1


def test_a_form_opened_in_another_tab_is_not_wiped_by_signing_in_again(env):
    _, _, svc = env
    row = start(svc)

    async def race():
        # the second sign-in is past its checks and about to save when the first tab opens the payment form
        real = svc.availability
        done = []

        async def slow(email=None):
            why = await real(email)
            if not done:
                done.append(1)
                await svc.checkout(svc.get(row["id"]), "https://streaming.test/")
            return why
        svc.availability = slow
        try:
            return await svc.begin(newbie(), "tok-new", None)
        finally:
            svc.availability = real
    out, _ = run(race())
    after = svc.get(row["id"])
    assert out == "go" and after["state"] == "checkout_open" and after["checkout_session"]


# ---------- after they are in: Billing match, Billing refresh, Requests account ----------
class FakeRequests:
    def __init__(self, fail=0):
        self.made, self.fail, self.on = [], fail, True

    def configured(self):
        return self.on

    async def seerr_user(self, who, directory):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("Seerr is down")
        assert who.member and who.kind == "plex" and not who.is_admin
        self.made.append((who.subject, who.email))
        return 77


def joined(svc):
    row = start(svc)
    _, row = open_checkout(svc, row)
    M.pay(row["checkout_session"])
    row = run(svc.advance(row["id"]))
    assert row["state"] == "active"
    return row


def billing_poller(svc, store, fail=0):
    svc.poller.syncs, svc.poller.fail = 0, fail

    async def sync_billing():
        if svc.poller.fail:
            svc.poller.fail -= 1
            raise RuntimeError("Stripe is down")
        svc.poller.syncs += 1
        return {}
    svc.poller.sync_billing = sync_billing
    store.set_setting("stripe_key", "rk_live_billing")


def test_the_new_member_is_matched_in_billing_and_made_ready(env):
    store, d, svc = env
    billing_poller(svc, store)
    svc.requests = FakeRequests()
    row = joined(svc)
    # Matched the moment they are in, before anything else is waited for.
    assert [(l["email"], l["plex_id"]) for l in svc.billing.links()] == [("newbie@example.com", "3001")]
    assert svc.ready(row) == {"matched": True, "billing": False, "requests": False, "done": False, "late": False}
    assert svc.view(row)["ready"]["done"] is False
    row = run(svc.prepare(row["id"]))
    # Only their own customer is read from Stripe (seconds), not the whole account (minutes).
    assert svc.poller.syncs == 0 and what("customer") == [{"what": "customer", "id": row["customer"]}]
    mine = svc.billing.summary("3001")
    assert mine["status"] == "active" and [x["id"] for x in mine["subscriptions"]] == [row["subscription"]] and mine["subscriptions"][0]["amount"] == 1599
    assert mine["subscriptions"][0]["plan"] == "Greystar Monthly" and mine["last_payment"]["amount"] == 1599
    assert svc.requests.made == [("3001", "newbie@example.com")]
    assert row["ready_at"] and svc.ready(row) == {"matched": True, "billing": True, "requests": True, "done": True, "late": False}
    run(svc.prepare(row["id"]))                     # asked again: nothing is done twice
    assert len(what("customer")) == 1 and len(svc.requests.made) == 1
    assert run(svc.sweep()) == 0 and len(what("customer")) == 1 and svc.poller.syncs == 0


def test_a_part_that_fails_is_tried_again_and_the_page_does_not_wait_for_ever(env):
    store, d, svc = env
    billing_poller(svc, store, fail=1)
    svc.requests = FakeRequests(fail=1)
    M.state["fail"]["customer"] = 403               # the Billing key cannot read one customer: the whole account is read instead
    row = joined(svc)
    row = run(svc.prepare(row["id"]))
    assert not row["billing_at"] and not row["requests_at"] and not row["ready_at"] and "not fully ready" in row["last_error"]
    assert svc.ready(row)["done"] is False
    # Signing in never needed any of it: after a while the page lets them through and says the rest follows.
    late = dict(row, active_at=row["active_at"] - 121)
    assert svc.ready(late)["done"] is True and svc.ready(late)["late"] is True
    run(svc.sweep())                                # the sweep comes back for what failed
    row = svc.get(row["id"])
    assert row["billing_at"] and row["requests_at"] and row["ready_at"] and row["last_error"] is None
    assert svc.poller.syncs == 1 and len(svc.requests.made) == 1


def test_a_link_made_by_hand_to_someone_else_is_left_alone(env):
    store, d, svc = env
    svc.billing.link("newbie@example.com", "555")
    row = joined(svc)
    assert [(l["email"], l["plex_id"]) for l in svc.billing.links()] == [("newbie@example.com", "555")]
    assert row["linked_at"]


def test_with_no_billing_key_and_no_requests_there_is_nothing_to_wait_for(env):
    store, d, svc = env
    row = joined(svc)                               # FakePoller has no sync_billing: it must not be called
    row = run(svc.prepare(row["id"]))
    assert row["ready_at"] and svc.ready(row)["done"] is True


def test_one_billing_refresh_serves_everyone_who_joined_before_it_started(env):
    store, d, svc = env
    billing_poller(svc, store)
    M.state["fail"]["customer"] = 403
    row = joined(svc)
    svc._billing_from = row["active_at"] + 5        # a refresh that started after they were added
    run(svc.prepare(row["id"]))
    assert svc.poller.syncs == 0 and svc.get(row["id"])["billing_at"]
