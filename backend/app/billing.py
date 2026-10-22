"""Stripe subscriptions for Plex users.

Every 15 minutes the poller copies Stripe customers, subscriptions, products and
recent charges into SQLite (read-only Stripe key, saved encrypted from
Settings, Billing). A Stripe customer belongs to a Plex user when:

1. its email is linked to that user by hand (billing_links: permanent, wins), or
2. its email equals the user's Plex email (case-insensitive).

PayPal is not connected yet.
"""
from __future__ import annotations

import os
import time

import httpx

STRIPE_API = os.environ.get("STRIPE_API_URL", "https://api.stripe.com")
# Pinned so field names stay put (current_period_end on the subscription).
STRIPE_VERSION = "2024-06-20"
CHARGE_DAYS = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS stripe_customers (
  id TEXT PRIMARY KEY,
  email TEXT,
  name TEXT,
  created INTEGER
);
CREATE INDEX IF NOT EXISTS stripe_customers_email ON stripe_customers (email);
CREATE TABLE IF NOT EXISTS stripe_subscriptions (
  id TEXT PRIMARY KEY,
  customer TEXT,
  status TEXT,
  plan TEXT,
  amount INTEGER,
  currency TEXT,
  interval TEXT,
  interval_count INTEGER,
  current_period_end INTEGER,
  cancel_at_period_end INTEGER,
  cancel_at INTEGER,
  canceled_at INTEGER,
  ended_at INTEGER,
  trial_end INTEGER,
  created INTEGER
);
CREATE INDEX IF NOT EXISTS stripe_subscriptions_customer ON stripe_subscriptions (customer);
CREATE TABLE IF NOT EXISTS stripe_charges (
  id TEXT PRIMARY KEY,
  customer TEXT,
  email TEXT,
  amount INTEGER,
  amount_refunded INTEGER,
  currency TEXT,
  status TEXT,
  refunded INTEGER,
  description TEXT,
  created INTEGER
);
CREATE INDEX IF NOT EXISTS stripe_charges_customer ON stripe_charges (customer, created);
CREATE TABLE IF NOT EXISTS billing_links (
  email TEXT PRIMARY KEY,
  plex_id TEXT NOT NULL,
  created INTEGER NOT NULL
);
"""

# Which subscription to show first when someone has several.
STATUS_ORDER = ["active", "trialing", "past_due", "unpaid", "incomplete", "paused", "canceled", "incomplete_expired"]
LAPSED = {"past_due", "unpaid", "canceled", "incomplete", "incomplete_expired", "paused"}


class StripeError(Exception):
    pass


class StripeClient:
    """Just the read-only calls the dashboard needs."""

    def __init__(self, key: str, http: httpx.AsyncClient):
        self.key = key
        self.http = http

    async def _get(self, path: str, params: dict | None = None) -> dict:
        r = await self.http.get(f"{STRIPE_API}{path}", params=params,
                                headers={"Authorization": f"Bearer {self.key}", "Stripe-Version": STRIPE_VERSION}, timeout=30.0)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message")
            except ValueError:
                msg = None
            raise StripeError(msg or f"Stripe answered HTTP {r.status_code}")
        return r.json()

    async def _all(self, path: str, params: dict | None = None, cap: int = 50000) -> list[dict]:
        out: list[dict] = []
        params = dict(params or {}, limit=100)
        while True:
            page = await self._get(path, params)
            data = page.get("data") or []
            out.extend(data)
            if not page.get("has_more") or not data or len(out) >= cap:
                return out
            params["starting_after"] = data[-1]["id"]

    async def check(self) -> dict:
        """Which of the needed read permissions the key has."""
        result = {}
        for name, path in (("customers", "/v1/customers"), ("subscriptions", "/v1/subscriptions"),
                           ("products", "/v1/products"), ("charges", "/v1/charges")):
            try:
                await self._get(path, {"limit": 1})
                result[name] = True
            except StripeError as e:
                result[name] = str(e)
        return result

    async def snapshot(self) -> dict:
        customers = await self._all("/v1/customers")
        subs = await self._all("/v1/subscriptions", {"status": "all"})
        products = {p["id"]: p.get("name") for p in await self._all("/v1/products")}
        charges = await self._all("/v1/charges", {"created[gte]": int(time.time()) - CHARGE_DAYS * 86400})
        return {"customers": customers, "subscriptions": subs, "products": products, "charges": charges}


def _lower(v) -> str | None:
    return v.strip().lower() if isinstance(v, str) and v.strip() else None


def subscription_row(s: dict, products: dict) -> dict:
    items = (s.get("items") or {}).get("data") or []
    names, amount, currency, interval, count, period_end = [], 0, s.get("currency"), None, 1, s.get("current_period_end")
    for it in items:
        price = it.get("price") or it.get("plan") or {}
        prod = price.get("product")
        prod_id = prod.get("id") if isinstance(prod, dict) else prod
        name = (prod.get("name") if isinstance(prod, dict) else None) or products.get(prod_id) or price.get("nickname")
        if name and name not in names:
            names.append(name)
        amount += int(price.get("unit_amount") or price.get("amount") or 0) * int(it.get("quantity") or 1)
        currency = price.get("currency") or currency
        rec = price.get("recurring") or {}
        interval = rec.get("interval") or price.get("interval") or interval
        count = rec.get("interval_count") or price.get("interval_count") or count
        period_end = period_end or it.get("current_period_end")  # newer Stripe versions keep it on the item
    return {
        "id": s["id"], "customer": s.get("customer") if isinstance(s.get("customer"), str) else (s.get("customer") or {}).get("id"),
        "status": s.get("status"), "plan": ", ".join(names) or "Subscription", "amount": amount, "currency": (currency or "usd").lower(),
        "interval": interval, "interval_count": int(count or 1), "current_period_end": period_end,
        "cancel_at_period_end": int(bool(s.get("cancel_at_period_end"))), "cancel_at": s.get("cancel_at"),
        "canceled_at": s.get("canceled_at"), "ended_at": s.get("ended_at"), "trial_end": s.get("trial_end"), "created": s.get("created"),
    }


def charge_row(c: dict) -> dict:
    bd = c.get("billing_details") or {}
    cust = c.get("customer")
    return {
        "id": c["id"], "customer": cust if isinstance(cust, str) else (cust or {}).get("id"),
        "email": _lower(bd.get("email")) or _lower(c.get("receipt_email")),
        "amount": int(c.get("amount") or 0), "amount_refunded": int(c.get("amount_refunded") or 0),
        "currency": (c.get("currency") or "usd").lower(), "status": c.get("status"), "refunded": int(bool(c.get("refunded"))),
        "description": c.get("description"), "created": c.get("created"),
    }


class Billing:
    def __init__(self, store):
        self.s = store
        self.db = store.db
        self.lock = store.lock
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- sync ----------
    def save_snapshot(self, snap: dict) -> dict:
        customers = [c for c in snap["customers"] if not c.get("deleted")]
        subs = [subscription_row(s, snap["products"]) for s in snap["subscriptions"]]
        charges = [charge_row(c) for c in snap["charges"]]
        cutoff = int(time.time()) - CHARGE_DAYS * 86400
        with self.lock:
            self.db.execute("DELETE FROM stripe_customers")
            self.db.executemany("INSERT INTO stripe_customers VALUES (?,?,?,?)",
                                [(c["id"], _lower(c.get("email")), c.get("name"), c.get("created")) for c in customers])
            self.db.execute("DELETE FROM stripe_subscriptions")
            cols = ["id", "customer", "status", "plan", "amount", "currency", "interval", "interval_count", "current_period_end",
                    "cancel_at_period_end", "cancel_at", "canceled_at", "ended_at", "trial_end", "created"]
            self.db.executemany(f"INSERT INTO stripe_subscriptions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [[r[c] for c in cols] for r in subs])
            ccols = ["id", "customer", "email", "amount", "amount_refunded", "currency", "status", "refunded", "description", "created"]
            self.db.executemany(f"INSERT OR REPLACE INTO stripe_charges ({', '.join(ccols)}) VALUES ({', '.join('?' * len(ccols))})",
                                [[r[c] for c in ccols] for r in charges])
            self.db.execute("DELETE FROM stripe_charges WHERE created < ?", (cutoff,))
            self.db.commit()
        return {"customers": len(customers), "subscriptions": len(subs), "charges": len(charges)}

    # ---------- links (permanent, set by hand) ----------
    def links(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT l.email, l.plex_id, l.created, u.title, u.username, u.email AS plex_email FROM billing_links l "
            "LEFT JOIN users u ON u.plex_id = l.plex_id ORDER BY l.created DESC").fetchall()
        return [dict(r) for r in rows]

    def link(self, email: str, plex_id: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO billing_links (email, plex_id, created) VALUES (?,?,?) "
                            "ON CONFLICT(email) DO UPDATE SET plex_id = excluded.plex_id", (_lower(email), plex_id, int(time.time())))
            self.db.commit()

    def unlink(self, email: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM billing_links WHERE email = ?", (_lower(email),))
            self.db.commit()

    # ---------- matching ----------
    def owner_by_email(self) -> dict[str, tuple[str, str]]:
        """Stripe email -> (plex_id, 'link' | 'email'). Manual links win over matching Plex emails."""
        out = {}
        for r in self.db.execute("SELECT plex_id, lower(email) AS email FROM users WHERE email IS NOT NULL AND email != ''"):
            out[r["email"]] = (r["plex_id"], "email")
        for r in self.db.execute("SELECT email, plex_id FROM billing_links"):
            out[r["email"]] = (r["plex_id"], "link")
        return out

    def _emails_for(self, plex_id: str) -> tuple[set[str], bool]:
        own = self.db.execute("SELECT lower(email) AS email FROM users WHERE plex_id = ?", (plex_id,)).fetchone()
        linked = {r["email"] for r in self.db.execute("SELECT email FROM billing_links WHERE plex_id = ?", (plex_id,))}
        emails = set(linked)
        if own and own["email"]:
            taken = self.db.execute("SELECT plex_id FROM billing_links WHERE email = ?", (own["email"],)).fetchone()
            if not taken or taken["plex_id"] == plex_id:  # someone else's manual link wins
                emails.add(own["email"])
        return emails, bool(linked)

    @staticmethod
    def _sub_public(r: dict) -> dict:
        return {k: r[k] for k in ("id", "status", "plan", "amount", "currency", "interval", "interval_count", "current_period_end",
                                  "cancel_at_period_end", "cancel_at", "canceled_at", "ended_at", "trial_end", "created")}

    def summary(self, plex_id: str) -> dict:
        """Everything the subscription card shows for one person."""
        emails, has_link = self._emails_for(plex_id)
        if not emails:
            return {"status": "none", "subscriptions": [], "last_payment": None, "emails": [], "linked": False}
        q = ",".join("?" * len(emails))
        custs = [r["id"] for r in self.db.execute(f"SELECT id FROM stripe_customers WHERE email IN ({q})", list(emails))]
        subs = []
        if custs:
            cq = ",".join("?" * len(custs))
            subs = [dict(r) for r in self.db.execute(f"SELECT * FROM stripe_subscriptions WHERE customer IN ({cq})", custs)]
        subs = [s for s in subs if s["status"] != "incomplete_expired"]
        subs.sort(key=lambda s: (STATUS_ORDER.index(s["status"]) if s["status"] in STATUS_ORDER else 99, -(s["created"] or 0)))
        live = [s for s in subs if s["status"] not in ("canceled",)]
        shown = live or subs[:1]  # if everything ended, show the most recent one
        where = (f"customer IN ({','.join('?' * len(custs))})" if custs else "0") + f" OR email IN ({q})"
        pay = self.db.execute(f"SELECT * FROM stripe_charges WHERE {where} ORDER BY created DESC LIMIT 1", custs + list(emails)).fetchone()
        last = None
        if pay:
            p = dict(pay)
            last = {"amount": p["amount"], "amount_refunded": p["amount_refunded"], "currency": p["currency"], "created": p["created"],
                    "status": "refunded" if p["refunded"] else p["status"]}
        return {"status": shown[0]["status"] if shown else "none", "subscriptions": [self._sub_public(s) for s in shown],
                "last_payment": last, "emails": sorted(emails), "linked": has_link, "customers": len(custs)}

    def status_map(self) -> dict[str, str]:
        """plex_id -> status of the subscription shown first (for flags in the Users search)."""
        owner = self.owner_by_email()
        by_customer: dict[str, str] = {}
        for r in self.db.execute("SELECT id, email FROM stripe_customers WHERE email IS NOT NULL"):
            if r["email"] in owner:
                by_customer[r["id"]] = owner[r["email"]][0]
        best: dict[str, str] = {}
        rank = lambda st: STATUS_ORDER.index(st) if st in STATUS_ORDER else 99
        for r in self.db.execute("SELECT customer, status FROM stripe_subscriptions"):
            pid = by_customer.get(r["customer"])
            if pid and r["status"] != "incomplete_expired" and (pid not in best or rank(r["status"]) < rank(best[pid])):
                best[pid] = r["status"]
        return best

    def unmatched(self, q: str = "", limit: int = 100) -> list[dict]:
        """Stripe customers with an email that no Plex user has, and no manual link."""
        owner = self.owner_by_email()
        like = f"%{q.strip().lower()}%"
        rows = self.db.execute(
            "SELECT c.id, c.email, c.name, c.created, "
            " (SELECT s.status FROM stripe_subscriptions s WHERE s.customer = c.id ORDER BY s.created DESC LIMIT 1) AS status, "
            " (SELECT s.plan FROM stripe_subscriptions s WHERE s.customer = c.id ORDER BY s.created DESC LIMIT 1) AS plan "
            "FROM stripe_customers c WHERE c.email IS NOT NULL AND (? = '%%' OR c.email LIKE ? OR lower(c.name) LIKE ?) "
            "ORDER BY c.created DESC", (like, like, like)).fetchall()
        out = [dict(r) for r in rows if r["email"] not in owner]
        return out[:limit]

    def counts(self) -> dict:
        owner = self.owner_by_email()
        emails = [r["email"] for r in self.db.execute("SELECT email FROM stripe_customers WHERE email IS NOT NULL")]
        matched = sum(1 for e in emails if e in owner)
        return {"customers": len(emails), "matched": matched, "unmatched": len(emails) - matched,
                "links": self.db.execute("SELECT COUNT(*) AS n FROM billing_links").fetchone()["n"],
                "subscriptions": self.db.execute("SELECT COUNT(*) AS n FROM stripe_subscriptions").fetchone()["n"]}
