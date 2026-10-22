"""Stripe subscriptions matched to Plex users."""
import time

import pytest
from cryptography.fernet import Fernet

NOW = int(time.time())


def sub(id, customer, status, amount=1000, created=NOW - 100, plan_product="prod_basic", period_end=None, item_period=False, **kw):
    item = {"price": {"unit_amount": amount, "currency": "usd", "recurring": {"interval": "month", "interval_count": 1}, "product": plan_product},
            "quantity": 1}
    s = {"id": id, "customer": customer, "status": status, "items": {"data": [item]}, "created": created,
         "cancel_at_period_end": kw.get("cancel_at_period_end", False), "currency": "usd"}
    if item_period:
        item["current_period_end"] = period_end
    else:
        s["current_period_end"] = period_end
    return s


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    from app.billing import Billing
    from app.secrets import SecretBox
    from app.store import Directory, Store
    store = Store(str(tmp_path / "t.db"), SecretBox())
    d = Directory(store)
    d.upsert_user({"id": "2001", "username": "sable", "email": "Sable@Example.com"})
    d.upsert_user({"id": "2002", "username": "quill", "email": "quill@example.com"})
    d.upsert_user({"id": "2003", "username": "wren", "email": "wren@plex-only.org"})
    b = Billing(store)
    b.save_snapshot({
        "customers": [
            {"id": "cus_s", "email": "SABLE@example.com", "name": "Sable", "created": NOW - 900},
            {"id": "cus_q", "email": "quill@example.com", "created": NOW - 800},
            {"id": "cus_w", "email": "wren.pays@gmail.com", "name": "Wren", "created": NOW - 700},
            {"id": "cus_x", "email": "nobody@example.net", "created": NOW - 600},
        ],
        "subscriptions": [
            sub("sub_s_old", "cus_s", "canceled", created=NOW - 5000),
            sub("sub_s", "cus_s", "active", period_end=NOW + 86400 * 20, item_period=True),
            sub("sub_q", "cus_q", "past_due", amount=1500, period_end=NOW + 86400),
            sub("sub_w", "cus_w", "active", amount=800, plan_product="prod_plus", period_end=NOW + 86400 * 3),
            sub("sub_dead", "cus_x", "incomplete_expired"),
        ],
        "products": {"prod_basic": "Basic", "prod_plus": "Plus"},
        "charges": [
            {"id": "ch_1", "customer": "cus_s", "amount": 1000, "currency": "usd", "status": "succeeded", "created": NOW - 86400 * 10},
            {"id": "ch_2", "customer": "cus_s", "amount": 1000, "currency": "usd", "status": "failed", "created": NOW - 86400 * 2},
            {"id": "ch_3", "customer": None, "amount": 800, "currency": "usd", "status": "succeeded", "created": NOW - 86400,
             "billing_details": {"email": "Wren.Pays@gmail.com"}},
        ],
    })
    return store, d, b


def test_matches_by_email_case_insensitive(env):
    _, _, b = env
    s = b.summary("2001")
    assert s["status"] == "active" and [x["id"] for x in s["subscriptions"]] == ["sub_s"]  # ended one hidden while one is live
    assert s["subscriptions"][0]["plan"] == "Basic" and s["subscriptions"][0]["amount"] == 1000
    assert s["subscriptions"][0]["current_period_end"] == NOW + 86400 * 20  # read from the item on newer Stripe versions
    assert s["last_payment"]["status"] == "failed"  # newest payment, whatever happened to it


def test_unmatched_until_linked_then_permanent(env):
    _, _, b = env
    assert b.summary("2003")["status"] == "none"
    assert {r["email"] for r in b.unmatched()} == {"wren.pays@gmail.com", "nobody@example.net"}
    b.link("Wren.Pays@Gmail.com", "2003")
    s = b.summary("2003")
    assert s["status"] == "active" and s["linked"] and s["subscriptions"][0]["plan"] == "Plus"
    assert s["last_payment"]["amount"] == 800  # charge with no customer, matched by its billing email
    assert {r["email"] for r in b.unmatched()} == {"nobody@example.net"}
    # a new Stripe sync does not undo the link
    b.save_snapshot({"customers": [{"id": "cus_w", "email": "wren.pays@gmail.com", "created": NOW}], "subscriptions": [], "products": {}, "charges": []})
    assert b.summary("2003")["linked"] is True and b.summary("2003")["emails"] == ["wren.pays@gmail.com", "wren@plex-only.org"]


def test_manual_link_beats_email_match(env):
    _, _, b = env
    b.link("quill@example.com", "2001")  # Quill's Stripe customer actually belongs to sable
    assert b.summary("2002")["status"] == "none"
    assert {x["id"] for x in b.summary("2001")["subscriptions"]} == {"sub_s", "sub_q"}


def test_status_map_flags(env):
    _, _, b = env
    m = b.status_map()
    assert m == {"2001": "active", "2002": "past_due"}
    c = b.counts()
    assert c["customers"] == 4 and c["matched"] == 2 and c["unmatched"] == 2


def test_cancel_at_period_end_and_canceled_only(env):
    _, d, b = env
    d.upsert_user({"id": "2004", "username": "lark", "email": "lark@example.com"})
    b.save_snapshot({"customers": [{"id": "cus_l", "email": "lark@example.com", "created": NOW}],
                     "subscriptions": [sub("sub_l1", "cus_l", "canceled", created=NOW - 50), sub("sub_l0", "cus_l", "canceled", created=NOW - 500)],
                     "products": {}, "charges": []})
    s = b.summary("2004")
    assert s["status"] == "canceled" and [x["id"] for x in s["subscriptions"]] == ["sub_l1"]
