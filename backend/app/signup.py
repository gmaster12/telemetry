"""Sign-up: a new person pays for a subscription and is added to one Plex server, with nobody doing it by hand.

Each sign-up is one row in `signups`, so a closed tab or a restart loses nothing. Its states, in order:

  plex_ok        signed in with Plex (the Plex window also makes new accounts); not a member, not already paying
  checkout_open  a Stripe Checkout was opened for them (embedded in web/signup.html)
  paid           Stripe itself says that checkout is complete and paid, and its subscription is active
  invited        the server's owner account invited them to the chosen libraries
  active         the share is live: they can sign in here and see the server in the Plex apps

Rules this file keeps:

- Payment is never taken on the browser's word. `advance` asks Stripe for the session and checks it is ours.
- One invite per paid sign-up, to the one server and the libraries chosen in Settings. Nothing here edits or
  removes a share that already exists.
- The new member's Plex token is kept (encrypted) only to accept the invite for them, then erased.
- Paid but not on the server never fails quietly: after FAIL_ALERT tries the admins are told, and it keeps trying.
- The key that opens checkouts (`signup_stripe_key`) is separate from Billing's read-only key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import is_admin_email, new_session, record_failure, set_cookie, throttled
from .billing import STRIPE_API, STRIPE_VERSION, StripeError
from .plex import PlexAccount, PlexError

log = logging.getLogger("telemetry.signup")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
  id TEXT PRIMARY KEY,
  token_hash TEXT,
  plex_id TEXT NOT NULL UNIQUE,
  username TEXT,
  title TEXT,
  email TEXT,
  thumb TEXT,
  plex_token_enc TEXT,
  state TEXT NOT NULL,
  server_id INTEGER,
  sections TEXT,
  checkout_session TEXT,
  checkout_n INTEGER NOT NULL DEFAULT 0,
  customer TEXT,
  subscription TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  alerted INTEGER NOT NULL DEFAULT 0,
  ip TEXT,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  checked INTEGER NOT NULL DEFAULT 0,
  paid_at INTEGER,
  invited_at INTEGER,
  active_at INTEGER
);
CREATE INDEX IF NOT EXISTS signups_state ON signups (state, updated);
CREATE INDEX IF NOT EXISTS signups_token ON signups (token_hash);
"""

COOKIE = "gst_signup"
PIN_COOKIE = "gst_signup_pin"
COOKIE_PATH = "/api/signup/"
COOKIE_TTL = 7 * 86400
STATES = ("plex_ok", "checkout_open", "paid", "invited", "active")
IN_FLIGHT = ("checkout_open", "paid", "invited")
PLEX_LIMIT = 100           # plex.tv will not share one server with more people than this
CHECKOUT_MINUTES = 120     # how long an opened checkout stays payable
FAIL_ALERT = 3             # failed tries before the admins are told
INVITE_SETTLE = 180        # plex.tv can take a moment to list a new invite; do not call it gone before this
CAPACITY_TTL = 120
PAYING = ("active", "trialing", "past_due", "unpaid")
ADMINS = "admins"          # notify.ADMINS (not imported: notify.py pulls in half the app)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def key_mode(key: str | None) -> str | None:
    """'test' or 'live', read from a Stripe key's prefix (pk_test_, sk_live_, rk_test_...)."""
    parts = (key or "").split("_")
    return parts[1] if len(parts) > 2 and parts[1] in ("test", "live") else None


class SignupStripe:
    """The few Stripe calls sign-up needs. Uses its own restricted key: Checkout Sessions write, Prices read."""

    def __init__(self, key: str, http: httpx.AsyncClient):
        self.key = key
        self.http = http

    async def _call(self, method: str, path: str, *, data: dict | None = None, params: dict | None = None, idem: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.key}", "Stripe-Version": STRIPE_VERSION}
        if idem:
            headers["Idempotency-Key"] = idem
        r = await self.http.request(method, f"{STRIPE_API}{path}", data=data, params=params, headers=headers, timeout=30.0)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message")
            except ValueError:
                msg = None
            raise StripeError(msg or f"Stripe answered HTTP {r.status_code}")
        return r.json()

    async def create_checkout(self, signup_id: str, n: int, price: str, email: str, meta: dict, return_url: str, expires_at: int) -> dict:
        data = {"mode": "subscription", "ui_mode": "embedded", "redirect_on_completion": "if_required", "return_url": return_url,
                "line_items[0][price]": price, "line_items[0][quantity]": "1", "customer_email": email,
                "client_reference_id": signup_id, "expires_at": str(int(expires_at))}
        for k, v in meta.items():
            if v:
                data[f"metadata[{k}]"] = str(v)[:400]
                data[f"subscription_data[metadata][{k}]"] = str(v)[:400]
        return await self._call("POST", "/v1/checkout/sessions", data=data, idem=f"signup-{signup_id}-{n}")

    async def get_checkout(self, session_id: str) -> dict:
        return await self._call("GET", f"/v1/checkout/sessions/{session_id}", params={"expand[]": "subscription"})

    async def expire_checkout(self, session_id: str) -> dict:
        return await self._call("POST", f"/v1/checkout/sessions/{session_id}/expire")

    async def price(self, price_id: str) -> dict:
        try:
            return await self._call("GET", f"/v1/prices/{price_id}", params={"expand[]": "product"})
        except StripeError:   # a key without Products read still gets the price
            return await self._call("GET", f"/v1/prices/{price_id}")

    async def prices(self) -> list[dict]:
        base = {"active": "true", "type": "recurring", "limit": 100}
        try:
            page = await self._call("GET", "/v1/prices", params={**base, "expand[]": "data.product"})
        except StripeError:
            page = await self._call("GET", "/v1/prices", params=base)
        return page.get("data") or []


def plan_of(price: dict) -> dict:
    """What the sign-up page says about the plan: amount and how often. Names stay Stripe's."""
    prod = price.get("product")
    rec = price.get("recurring") or {}
    return {"id": price.get("id"), "amount": int(price.get("unit_amount") or 0), "currency": (price.get("currency") or "usd").lower(),
            "interval": rec.get("interval"), "interval_count": int(rec.get("interval_count") or 1),
            "name": (prod.get("name") if isinstance(prod, dict) else None) or price.get("nickname") or None,
            "active": bool(price.get("active")), "recurring": price.get("type") == "recurring" and bool(rec)}


class SignupClosed(Exception):
    """Sign-up cannot take this person right now. `reason`: off, setup, full or busy."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Signup:
    def __init__(self, store, directory, http: httpx.AsyncClient, client_id: Callable[[], str], billing=None):
        self.store = store
        self.db = store.db
        self.lock = store.lock
        self.directory = directory
        self.http = http
        self.client_id = client_id
        self.billing = billing
        self.notifier: Any = None   # notify.Notifier (main.py): admins hear about new and stuck sign-ups
        self.poller: Any = None     # poller.Poller (main.py): reload the people list after someone joins
        self._locks: dict[str, asyncio.Lock] = {}
        self._capacity: tuple[float, dict] | None = None
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # ---------- settings ----------
    def settings(self) -> dict:
        g = self.store.get_setting
        try:
            sections = [str(x) for x in json.loads(g("signup_sections") or "[]")]
        except ValueError:
            sections = []
        try:
            plan = json.loads(g("signup_plan") or "null")
        except ValueError:
            plan = None
        sid = g("signup_server_id")
        cap = g("signup_cap")
        return {"enabled": g("signup_enabled") == "1", "server_id": int(sid) if sid and sid.isdigit() else None, "sections": sections,
                "price_id": g("signup_price_id"), "publishable_key": g("signup_publishable_key"), "has_key": bool(g("signup_stripe_key")),
                "mode": key_mode(g("signup_stripe_key")), "cap": max(1, min(int(cap), PLEX_LIMIT)) if cap and cap.isdigit() else PLEX_LIMIT,
                "support": g("signup_support") or "", "plan": plan}

    def server(self, server_id: int | None = None) -> dict | None:
        sid = server_id if server_id is not None else self.settings()["server_id"]
        return self.store.get_server(int(sid)) if sid is not None else None

    def owner(self, server: dict | None) -> tuple[dict, str] | None:
        """(account, token) of the linked Plex account that owns this server."""
        if not server or not server.get("owner_plex_id"):
            return None
        for a, tok in self.store.account_tokens():
            if a["plex_id"] == server["owner_plex_id"]:
                return a, tok
        return None

    def problems(self, s: dict | None = None) -> list[str]:
        """What still has to be set before sign-up can be turned on. Empty when it is ready."""
        s = s or self.settings()
        out = []
        srv = self.server(s["server_id"])
        if not srv:
            out.append("Choose the server new people join.")
        elif not srv.get("enabled"):
            out.append(f"{srv['name']} is turned off.")
        elif not srv.get("machine_id") or not self.owner(srv):
            out.append(f"Link the Plex account that owns {srv['name']} (Settings, Plex servers).")
        if not s["sections"]:
            out.append("Choose at least one library to share.")
        if not s["has_key"]:
            out.append("Add the Stripe key that opens checkouts.")
        if not (s["publishable_key"] or "").startswith("pk_"):
            out.append("Add the Stripe publishable key (pk_...).")
        elif s["has_key"] and key_mode(s["publishable_key"]) != s["mode"]:
            out.append("The two Stripe keys are not from the same mode (one is test, one is live).")
        if not s["price_id"] or not s["plan"]:
            out.append("Choose the subscription price.")
        return out

    def stripe(self) -> SignupStripe:
        key = self.store.get_setting("signup_stripe_key")
        if not key:
            raise StripeError("No Stripe key is saved for sign-up.")
        return SignupStripe(key, self.http)

    def plex(self) -> PlexAccount:
        return PlexAccount(self.http, self.client_id())

    # ---------- rows ----------
    def get(self, sid: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM signups WHERE id = ?", (sid,)).fetchone()
        return dict(r) if r else None

    def by_cookie(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self.lock:
            r = self.db.execute("SELECT * FROM signups WHERE token_hash = ?", (_hash(token),)).fetchone()
        return dict(r) if r else None

    def by_plex(self, plex_id: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM signups WHERE plex_id = ?", (str(plex_id),)).fetchone()
        return dict(r) if r else None

    def _set(self, sid: str, **cols) -> None:
        cols["updated"] = int(time.time())
        with self.lock:
            self.db.execute(f"UPDATE signups SET {', '.join(k + ' = ?' for k in cols)} WHERE id = ?", list(cols.values()) + [sid])
            self.db.commit()

    def delete(self, sid: str) -> bool:
        with self.lock:
            n = self.db.execute("DELETE FROM signups WHERE id = ?", (sid,)).rowcount
            self.db.commit()
        return n > 0

    def rows(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rs = self.db.execute("SELECT * FROM signups ORDER BY updated DESC LIMIT ?", (int(limit),)).fetchall()
        return [self.admin_row(dict(r)) for r in rs]

    def counts(self) -> dict:
        with self.lock:
            rs = self.db.execute("SELECT state, COUNT(*) AS n FROM signups GROUP BY state").fetchall()
            stuck = self.db.execute("SELECT COUNT(*) AS n FROM signups WHERE state IN ('paid', 'invited') AND alerted = 1").fetchone()["n"]
        return {**{s: 0 for s in STATES}, **{r["state"]: r["n"] for r in rs}, "stuck": stuck}

    def admin_row(self, r: dict) -> dict:
        """One sign-up for the Settings list. Never the Plex token or the cookie."""
        srv = self.store.get_server(r["server_id"]) if r.get("server_id") is not None else None
        return {"id": r["id"], "plex_id": r["plex_id"], "name": r.get("title") or r.get("username"), "username": r.get("username"),
                "email": r.get("email"), "state": r["state"], "server": srv["name"] if srv else None, "customer": r.get("customer"),
                "subscription": r.get("subscription"), "attempts": r["attempts"], "last_error": r.get("last_error"),
                "stuck": bool(r["alerted"]) and r["state"] in ("paid", "invited"), "created": r["created"], "updated": r["updated"],
                "paid_at": r.get("paid_at"), "active_at": r.get("active_at")}

    # ---------- room on the server ----------
    async def capacity(self, fresh: bool = False) -> dict:
        """How many people the sign-up server has against the cap: accepted shares, invites still waiting, and
        sign-ups that have paid (or are paying) but are not invited yet. Asked of plex.tv, kept for two minutes."""
        s = self.settings()
        now = time.monotonic()
        if not fresh and self._capacity and now - self._capacity[0] < CAPACITY_TTL and self._capacity[1].get("server_id") == s["server_id"]:
            live = self._capacity[1]
        else:
            srv = self.server(s["server_id"])
            own = self.owner(srv)
            if not srv or not own:
                return {"server_id": s["server_id"], "shares": None, "invites": None, "used": None, "cap": s["cap"], "full": False,
                        "error": "The sign-up server is not set up."}
            try:
                acct = self.plex()
                shares, invites = await asyncio.gather(acct.shares(own[1], srv["machine_id"]), acct.sent_invites(own[1]))
                live = {"server_id": s["server_id"], "shares": len(shares), "invites": len(invites), "error": None}
            except (PlexError, httpx.HTTPError) as e:
                live = {"server_id": s["server_id"], "shares": None, "invites": None, "error": str(e) or type(e).__name__}
            self._capacity = (now - (CAPACITY_TTL - 20 if live["error"] else 0), live)
        with self.lock:
            held = self.db.execute(
                "SELECT COUNT(*) AS n FROM signups WHERE server_id = ? AND (state = 'paid' OR (state = 'checkout_open' AND updated > ?))",
                (s["server_id"], int(time.time()) - 1800)).fetchone()["n"]
        used = None if live["shares"] is None else live["shares"] + live["invites"] + held
        return {**live, "held": held, "used": used, "cap": s["cap"], "full": used is not None and used >= s["cap"]}

    async def availability(self) -> str | None:
        """None when a new person can sign up now; otherwise why not: off, setup, full or busy (plex.tv did not answer)."""
        s = self.settings()
        if not s["enabled"]:
            return "off"
        if self.problems(s):
            return "setup"
        cap = await self.capacity()
        if cap["error"]:
            return "busy"
        return "full" if cap["full"] else None

    # ---------- step 1: who is this ----------
    async def live_member(self, plex_id: str, email: str | None) -> bool:
        """Is this Plex account shared a library on any Greystar server right now. Asked of plex.tv for every linked
        owner account, because the saved people list keeps people who left."""
        acct = self.plex()
        lists = await asyncio.gather(*[acct.shared_users(tok) for _, tok in self.store.account_tokens()])
        for users in lists:
            for u in users:
                if (u["id"] == str(plex_id) or (email and u.get("email") == email.lower())) and (u.get("server_ids") or u.get("server_names")):
                    return True
        return False

    def paying(self, plex_id: str, email: str) -> bool:
        """Does Stripe already have a running subscription under this person's email (or one linked to them by hand)."""
        if self.billing is None:
            return False
        emails = {email.lower()}
        with self.lock:
            emails |= {r["email"] for r in self.db.execute("SELECT email FROM billing_links WHERE plex_id = ?", (str(plex_id),))}
            q = ",".join("?" * len(emails))
            n = self.db.execute(
                f"SELECT COUNT(*) AS n FROM stripe_subscriptions s JOIN stripe_customers c ON c.id = s.customer "
                f"WHERE c.email IN ({q}) AND s.status IN ({','.join('?' * len(PAYING))})", list(emails) + list(PAYING)).fetchone()["n"]
        return n > 0

    async def begin(self, info: dict, token: str, ip: str | None) -> tuple[str, str | None]:
        """Someone finished the Plex sign-in on the sign-up page. Returns (outcome, cookie token).

        Outcomes: `go` (a sign-up was started or picked up again; the cookie is theirs), `member` (already on a
        server: sign in instead), `paying` (already has a subscription), `disabled`, `noemail`, or a closed
        reason (off, setup, full, busy)."""
        pid, email = str(info["id"]), (info.get("email") or "").lower() or None
        if not email:
            return "noemail", None
        if is_admin_email(email):
            return "member", None
        user = self.directory.get_user(pid)
        if user and user.get("disabled"):
            return "disabled", None
        row = self.by_plex(pid)
        cookie = secrets.token_urlsafe(32)
        fresh = {"token_hash": _hash(cookie), "plex_token_enc": self.store.box.seal(token), "username": info.get("username"),
                 "title": info.get("title"), "email": email, "thumb": info.get("thumb"), "ip": ip}
        if row and row["state"] in IN_FLIGHT:   # they came back (closed the tab after paying, say): carry on where it stood
            self._set(row["id"], **fresh)
            return "go", cookie
        try:
            member = await self.live_member(pid, email)
        except (PlexError, httpx.HTTPError):
            return "busy", None
        if member:
            return "member", None
        if self.paying(pid, email):
            return "paying", None
        why = await self.availability()
        if why:
            return why, None
        now = int(time.time())
        if row:   # an earlier sign-up that never paid, or a member who left and is coming back
            self._set(row["id"], **fresh, state="plex_ok", server_id=None, sections=None, checkout_session=None, customer=None,
                      subscription=None, attempts=0, last_error=None, alerted=0, created=now, checked=0, paid_at=None,
                      invited_at=None, active_at=None)
        else:
            with self.lock:
                self.db.execute(
                    "INSERT INTO signups (id, token_hash, plex_id, username, title, email, thumb, plex_token_enc, state, ip, created, updated) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (secrets.token_urlsafe(12), fresh["token_hash"], pid, fresh["username"], fresh["title"], email, fresh["thumb"],
                     fresh["plex_token_enc"], "plex_ok", ip, now, now))
                self.db.commit()
        return "go", cookie

    # ---------- step 2: pay ----------
    async def checkout(self, row: dict, base_url: str) -> dict:
        """The Stripe Checkout for this sign-up: the one already open, or a new one. Returns {client_secret} or {paid: True}."""
        if row["state"] not in ("plex_ok", "checkout_open"):
            return {"paid": True}
        s = self.settings()
        api = self.stripe()
        if row["state"] == "checkout_open" and row.get("checkout_session"):
            sess = await api.get_checkout(row["checkout_session"])
            if sess.get("status") == "open" and sess.get("client_secret"):
                return {"client_secret": sess["client_secret"]}
            if sess.get("status") == "complete":
                return {"paid": True}
        why = await self.availability()
        if why:
            raise SignupClosed(why)
        # Counted before Stripe is asked: if the answer is lost, the next try uses a new idempotency key instead of
        # tripping over the old one (the times in the request differ). A checkout nobody was shown just expires.
        n = int(row["checkout_n"] or 0) + 1
        self._set(row["id"], checkout_n=n)
        meta = {"signup_id": row["id"], "plex_id": row["plex_id"], "plex_username": row.get("username")}
        sess = await api.create_checkout(row["id"], n, s["price_id"], row["email"], meta, base_url.rstrip("/") + "/signup.html",
                                         int(time.time()) + CHECKOUT_MINUTES * 60)
        if not sess.get("client_secret"):
            raise StripeError("Stripe did not return a checkout to show.")
        self._set(row["id"], state="checkout_open", checkout_session=sess["id"], server_id=s["server_id"],
                  sections=json.dumps(s["sections"]), last_error=None)
        return {"client_secret": sess["client_secret"]}

    # ---------- steps 3 and 4: paid, invited, on the server ----------
    def _lock_for(self, sid: str) -> asyncio.Lock:
        return self._locks.setdefault(sid, asyncio.Lock())

    async def advance(self, sid: str) -> dict | None:
        """Move one sign-up as far forward as it can go right now. Safe to call as often as you like: one runs at a
        time per sign-up, and every step first looks at what is already true at Stripe and plex.tv."""
        async with self._lock_for(sid):
            row = self.get(sid)
            if not row or row["state"] not in IN_FLIGHT:
                return row
            self._set(sid, checked=int(time.time()))
            try:
                if row["state"] == "checkout_open":
                    row = await self._payment(row)
                if row["state"] == "paid":
                    row = await self._invite(row)
                if row["state"] == "invited":
                    row = await self._accept(row)
            except (PlexError, StripeError, httpx.HTTPError) as e:
                self._failed(self.get(sid), e)
            return self.get(sid)

    async def _payment(self, row: dict) -> dict:
        if not row.get("checkout_session"):
            self._set(row["id"], state="plex_ok")
            return self.get(row["id"])
        sess = await self.stripe().get_checkout(row["checkout_session"])
        sub = sess.get("subscription")
        if sess.get("status") == "expired":
            self._set(row["id"], state="plex_ok", checkout_session=None)
        elif (sess.get("status") == "complete" and sess.get("payment_status") == "paid" and sess.get("mode") == "subscription"
              and sess.get("client_reference_id") == row["id"] and isinstance(sub, dict) and sub.get("status") == "active"):
            cust = sess.get("customer")
            self._set(row["id"], state="paid", paid_at=int(time.time()), subscription=sub.get("id"),
                      customer=cust if isinstance(cust, str) else (cust or {}).get("id"), attempts=0, last_error=None)
            self._capacity = None
            log.info("Sign-up %s (%s) paid: %s", row["id"], row.get("username"), sub.get("id"))
        return self.get(row["id"])

    async def _share_state(self, tok: str, machine_id: str, row: dict) -> tuple[str, dict | None]:
        """Where this person stands on the server: ('accepted', share), ('pending', invite) or ('none', None)."""
        acct = self.plex()
        for sh in await acct.shares(tok, machine_id):
            if sh["user_id"] == row["plex_id"] or (sh.get("email") and sh["email"] == row["email"]):
                return "accepted", sh
        for inv in await acct.sent_invites(tok):
            if inv.get("email") and inv["email"] == row["email"]:
                return "pending", inv
        return "none", None

    def _target(self, row: dict) -> tuple[dict, dict, str]:
        srv = self.server(row["server_id"])
        own = self.owner(srv)
        if not srv or not srv.get("machine_id") or not own:
            raise PlexError("The sign-up server has no linked owner account any more.")
        return srv, own[0], own[1]

    async def _invite(self, row: dict) -> dict:
        srv, _, tok = self._target(row)
        where, _ = await self._share_state(tok, srv["machine_id"], row)
        if where == "accepted":
            return self._finish(row, srv)
        if where == "none":
            await self.plex().invite(tok, srv["machine_id"], row["email"], json.loads(row.get("sections") or "[]"))
            self._capacity = None
            log.info("Sign-up %s (%s) invited to %s", row["id"], row.get("username"), srv["name"])
        self._set(row["id"], state="invited", invited_at=int(time.time()), attempts=0, last_error=None)
        return self.get(row["id"])

    async def _accept(self, row: dict) -> dict:
        srv, owner, tok = self._target(row)
        where, _ = await self._share_state(tok, srv["machine_id"], row)
        if where == "accepted":
            return self._finish(row, srv)
        acct = self.plex()
        user_token = self.store.box.open(row.get("plex_token_enc"))
        if user_token:
            try:
                waiting = await acct.received_invites(user_token)
            except PlexError as e:
                if getattr(e, "status", None) in (401, 403):   # their sign-in ran out: stop asking with it
                    self._set(row["id"], plex_token_enc=None)
                raise
            names = {str(owner.get(k) or "").lower() for k in ("username", "email")} - {""}
            shares = [i for i in waiting if i["server"] == "1"]
            mine = [i for i in shares if {str(i.get("username") or "").lower(), i.get("email") or ""} & names]
            if not mine:   # plex.tv did not name the sender the way we know them: take the one invite to this server, if it is the only one
                named = [i for i in shares if srv["name"].lower() in [str(x).lower() for x in i.get("servers") or []]]
                mine = named if len(named) == 1 else []
            if mine:
                await acct.accept_invite(user_token, mine[0])
                where, _ = await self._share_state(tok, srv["machine_id"], row)
                return self._finish(row, srv) if where == "accepted" else row
        if where == "none" and int(time.time()) - int(row["invited_at"] or 0) > INVITE_SETTLE:
            # No invite on either side any more (declined, or removed by hand): send it again on the next pass.
            self._set(row["id"], state="paid")
            raise PlexError("The Plex invite was no longer there. Sending it again.")
        return row   # still waiting: they can also accept it themselves (Plex emailed them); the sweep notices

    def _finish(self, row: dict, srv: dict) -> dict:
        """The share is live. Put them in the people list so Sign in with Plex works at once, forget their token."""
        self.directory.upsert_user({"id": row["plex_id"], "username": row.get("username"), "title": row.get("title"),
                                    "email": row.get("email"), "thumb": row.get("thumb")}, shared=True)
        user = self.directory.get_user(row["plex_id"]) or {}
        if user.get("servers") is None:
            self.directory.upsert_user({"id": row["plex_id"], "servers": [srv["name"]]})
        else:
            self.directory.add_user_server(row["plex_id"], srv["name"])
        was_stuck = bool(row["alerted"])
        self._set(row["id"], state="active", active_at=int(time.time()), plex_token_enc=None, attempts=0, last_error=None, alerted=0)
        self._capacity = None
        log.info("Sign-up %s (%s) is on %s", row["id"], row.get("username"), srv["name"])
        name = row.get("title") or row.get("username") or row.get("email")
        if self.notifier is not None:
            try:
                if was_stuck:
                    self.notifier.resolve(ADMINS, f"signup:stuck:{row['id']}", f"{name} is on {srv['name']} now", "The sign-up finished.")
                self.notifier.post(ADMINS, f"signup:new:{row['id']}", "billing", f"New sign-up: {name}",
                                   f"Paid and added to {srv['name']}.", "Open Sign-up", "settings:signup")
            except Exception:  # noqa: BLE001  a notice is extra; the member is already in
                log.exception("Sign-up: could not post the new-member notice")
        if self.poller is not None:
            self.poller.users_synced_at = 0   # read the people list from plex.tv again
            self.poller.kick()
        return self.get(row["id"])

    def _failed(self, row: dict | None, e: Exception) -> None:
        if not row:
            return
        msg = (str(e) or type(e).__name__)[:300]
        if row["state"] == "checkout_open":   # Stripe did not answer: nothing is owed yet, just try again
            self._set(row["id"], last_error=msg)
            return
        n = int(row["attempts"] or 0) + 1
        self._set(row["id"], attempts=n, last_error=msg)
        log.warning("Sign-up %s (%s) could not move on from %s (try %d): %s", row["id"], row.get("username"), row["state"], n, msg)
        if n >= FAIL_ALERT and not row["alerted"]:
            self._alert(row, f"They paid but could not be added to Plex: {msg}")

    def _alert(self, row: dict, body: str) -> None:
        self._set(row["id"], alerted=1)
        name = row.get("title") or row.get("username") or row.get("email")
        if self.notifier is not None:
            try:
                self.notifier.post(ADMINS, f"signup:stuck:{row['id']}", "billing", f"Paid sign-up needs you: {name}", body,
                                   "Open Sign-up", "settings:signup", urgent=True)
            except Exception:  # noqa: BLE001
                log.exception("Sign-up: could not post the stuck notice")

    # ---------- keeping unfinished sign-ups moving ----------
    @staticmethod
    def due(row: dict, now: int) -> bool:
        """Is it time for the sweep to look at this sign-up again."""
        since = now - int(row["checked"] or 0)
        if row["state"] == "checkout_open":
            return since >= 60
        if row["state"] == "paid":
            return since >= (60 if int(row["attempts"] or 0) < FAIL_ALERT else 3600)
        if row["state"] == "invited":
            return since >= (60 if now - int(row["invited_at"] or 0) < 3600 else 900)
        return False

    async def sweep(self, now: int | None = None) -> int:
        """Finish what people left half done (paid, then closed the tab), and tidy up what never got paid for."""
        now = int(now or time.time())
        with self.lock:
            self.db.execute("DELETE FROM signups WHERE state = 'plex_ok' AND updated < ?", (now - 86400,))
            # Nobody's Plex sign-in is kept for more than a week, used or not.
            self.db.execute("UPDATE signups SET plex_token_enc = NULL WHERE plex_token_enc IS NOT NULL AND updated < ?", (now - 7 * 86400,))
            self.db.commit()
            rows = [dict(r) for r in self.db.execute(f"SELECT * FROM signups WHERE state IN ({','.join('?' * len(IN_FLIGHT))})", IN_FLIGHT)]
        moved = 0
        for r in rows:
            if r["state"] == "invited" and not r["alerted"] and now - int(r["invited_at"] or now) > 86400:
                self._alert(r, "They paid a day ago and still have not accepted the Plex invite.")
            if not self.due(r, now):
                continue
            after = await self.advance(r["id"])
            moved += int(bool(after) and after["state"] != r["state"])
        for sid in [k for k, lk in self._locks.items() if not lk.locked() and (self.get(k) or {}).get("state") not in IN_FLIGHT]:
            self._locks.pop(sid, None)
        return moved

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001  the next pass tries again
                log.exception("Sign-up sweep failed")

    # ---------- what the sign-up page is told ----------
    def view(self, row: dict | None) -> dict:
        if not row:
            return {"step": "plex"}
        srv = self.server(row["server_id"]) if row.get("server_id") is not None else self.server()
        out = {"name": row.get("title") or row.get("username"), "email": row.get("email"), "server": srv["name"] if srv else None}
        st = row["state"]
        if st in ("plex_ok", "checkout_open"):
            return {**out, "step": "pay"}
        if st == "active":
            return {**out, "step": "done"}
        waiting = st == "invited" and not row.get("plex_token_enc")
        return {**out, "step": "working", "stuck": bool(row["alerted"]) or int(row["attempts"] or 0) >= FAIL_ALERT,
                "accept": waiting or (st == "invited" and int(time.time()) - int(row["invited_at"] or 0) > 45)}


# ---------- routes ----------
class SettingsIn(BaseModel):
    enabled: bool | None = None
    server_id: int | None = None
    sections: list[str] | None = Field(default=None, max_length=100)
    price_id: str | None = Field(default=None, max_length=120)
    publishable_key: str | None = Field(default=None, max_length=300)
    stripe_key: str | None = Field(default=None, max_length=300)
    cap: int | None = Field(default=None, ge=1, le=PLEX_LIMIT)
    support: str | None = Field(default=None, max_length=300)


CLOSED_WORDS = {
    "off": "Sign-ups are closed right now.",
    "setup": "Sign-ups are closed right now.",
    "full": "We are full right now. Please check back soon.",
    "busy": "We could not reach Plex just now. Please try again in a minute.",
}


def make_router(svc: Signup, admin: Callable[[Request], Any], client_ip: Callable[[Request], str],
                on_member: Callable[[Request, dict, str], None] | None = None,
                on_change: Callable[[Request, str], None] | None = None) -> APIRouter:
    r = APIRouter()
    pins: dict[str, tuple[int, float]] = {}

    def secure(request: Request) -> bool:
        return request.url.scheme == "https"

    def mine(request: Request) -> dict:
        row = svc.by_cookie(request.cookies.get(COOKIE))
        if not row:
            raise HTTPException(401, "Start the sign-up again.")
        return row

    # ----- public: the sign-up page -----
    @r.get("/api/signup/config")
    async def config():
        s = svc.settings()
        why = await svc.availability()
        srv = svc.server(s["server_id"])
        return {"open": why is None, "reason": why, "message": CLOSED_WORDS.get(why or ""), "plan": s["plan"] if not svc.problems(s) else None,
                "publishable_key": s["publishable_key"] if why is None or why in ("full", "busy") else None,
                "server": srv["name"] if srv and s["enabled"] else None, "support": s["support"]}

    @r.post("/api/signup/plex/start")
    async def plex_start(request: Request):
        ip = client_ip(request)
        if throttled("signup:" + ip, limit=20, window=600):
            raise HTTPException(429, "Too many attempts. Wait a few minutes.")
        record_failure("signup:" + ip)   # every start counts, so one address gets 20 per 10 minutes
        if not svc.settings()["enabled"]:
            raise HTTPException(403, CLOSED_WORDS["off"])
        pin = await svc.plex().create_pin()
        now = time.time()
        for k, (_, t) in list(pins.items()):
            if now - t > 900:
                pins.pop(k, None)
        nonce = uuid.uuid4().hex
        pins[nonce] = (int(pin["pin_id"]), now)
        resp = JSONResponse(pin)
        resp.set_cookie(PIN_COOKIE, nonce, max_age=900, httponly=True, samesite="lax", path=COOKIE_PATH, secure=secure(request))
        return resp

    @r.get("/api/signup/plex/check/{pin_id}")
    async def plex_check(pin_id: int, request: Request):
        nonce = request.cookies.get(PIN_COOKIE, "")
        bound = pins.get(nonce)
        if not bound or bound[0] != pin_id:
            raise HTTPException(400, "This sign-in expired. Start again.")
        acct = svc.plex()
        token = await acct.check_pin(pin_id)
        if not token:
            return {"pending": True}
        pins.pop(nonce, None)
        info = await acct.user_info(token)
        outcome, cookie = await svc.begin(info, token, client_ip(request))
        if outcome == "noemail":
            raise HTTPException(400, "Your Plex account has no email address. Add one in Plex, then try again.")
        if outcome == "disabled":
            raise HTTPException(403, "Your access has been turned off. Contact us to turn it back on.")
        if outcome in CLOSED_WORDS:
            return {"step": "closed", "reason": outcome, "message": CLOSED_WORDS[outcome]}
        if outcome != "go":
            return {"step": outcome}   # member, or paying
        resp = JSONResponse(svc.view(svc.by_cookie(cookie)))
        resp.set_cookie(COOKIE, cookie, max_age=COOKIE_TTL, httponly=True, samesite="lax", path=COOKIE_PATH, secure=secure(request))
        resp.delete_cookie(PIN_COOKIE, path=COOKIE_PATH)
        return resp

    @r.get("/api/signup/status")
    async def status(request: Request):
        row = svc.by_cookie(request.cookies.get(COOKIE))
        # The page asks every few seconds. Stripe and plex.tv are asked less often than that, and less often still
        # after a failed try, so a short plex.tv hiccup does not count as three failures and page the admins.
        gap = 5 if row and row["state"] == "checkout_open" else min(60, 3 + 10 * int((row or {}).get("attempts") or 0))
        if row and row["state"] in IN_FLIGHT and time.time() - int(row["checked"] or 0) >= gap:
            row = await svc.advance(row["id"])
        return svc.view(row)

    @r.post("/api/signup/checkout")
    async def checkout(request: Request):
        row = mine(request)
        if throttled("signup-pay:" + row["id"], limit=30, window=600):
            raise HTTPException(429, "Too many tries. Wait a few minutes.")
        record_failure("signup-pay:" + row["id"])
        try:
            out = await svc.checkout(row, str(request.base_url))
        except SignupClosed as e:
            raise HTTPException(409, CLOSED_WORDS[e.reason])
        except StripeError as e:
            log.warning("Sign-up %s: Stripe would not open a checkout: %s", row["id"], e)
            raise HTTPException(502, "We could not open the payment form. Please try again in a minute.")
        if out.get("paid"):
            row = await svc.advance(row["id"])
            return {"paid": True, **svc.view(row)}
        return {"client_secret": out["client_secret"], "publishable_key": svc.settings()["publishable_key"]}

    @r.post("/api/signup/finish")
    def finish(request: Request):
        """They are on the server: sign them in to the dashboard, as Sign in with Plex would."""
        row = mine(request)
        if row["state"] != "active":
            raise HTTPException(409, "Your sign-up is not finished yet.")
        user = svc.directory.get_user(row["plex_id"])
        if not user or user.get("disabled"):
            raise HTTPException(403, "Your access to this dashboard has been turned off.")
        session = new_session(svc.directory, "plex", row["plex_id"])
        if on_member:
            on_member(request, row, session)
        svc._set(row["id"], token_hash=None)
        resp = JSONResponse({"ok": True})
        set_cookie(resp, request, session)
        resp.delete_cookie(COOKIE, path=COOKIE_PATH)
        return resp

    # ----- admins: Settings, Sign-up (main.py also turns everyone else away from /api/settings) -----
    async def overview() -> dict:
        s = svc.settings()
        servers = [{"id": x["id"], "name": x["name"], "enabled": bool(x["enabled"]), "linked": bool(x.get("machine_id") and svc.owner(x))}
                   for x in svc.store.list_servers()]
        return {**s, "problems": svc.problems(s), "capacity": await svc.capacity() if s["server_id"] is not None else None,
                "servers": servers, "counts": svc.counts(), "rows": svc.rows(), "plex_limit": PLEX_LIMIT}

    @r.get("/api/settings/signup")
    async def get_settings(request: Request):
        admin(request)
        return await overview()

    @r.put("/api/settings/signup")
    async def put_settings(body: SettingsIn, request: Request):
        admin(request)
        set_ = svc.store.set_setting
        if body.stripe_key is not None and body.stripe_key.strip():
            key = body.stripe_key.strip()
            if not key.startswith(("rk_", "sk_")):
                raise HTTPException(422, "That does not look like a Stripe secret key. Use a restricted key (rk_live_...).")
            try:
                await SignupStripe(key, svc.http).prices()
            except StripeError as e:
                raise HTTPException(422, f"Stripe refused the key: {e}")
            set_("signup_stripe_key", key)
        if body.publishable_key is not None:
            pk = body.publishable_key.strip()
            if pk and not pk.startswith("pk_"):
                raise HTTPException(422, "The publishable key starts with pk_.")
            set_("signup_publishable_key", pk or None)
        if body.server_id is not None:
            srv = svc.store.get_server(body.server_id)
            if not srv:
                raise HTTPException(404, "No such server.")
            if not srv["enabled"] or not srv.get("machine_id") or not svc.owner(srv):
                raise HTTPException(422, f"{srv['name']} is turned off, or the Plex account that owns it is not linked.")
            if svc.settings()["server_id"] != body.server_id and body.sections is None:
                set_("signup_sections", "[]")   # another server's library ids mean nothing here
            set_("signup_server_id", str(body.server_id))
            svc._capacity = None
        if body.sections is not None:
            srv = svc.server()
            own = svc.owner(srv)
            if not srv or not own:
                raise HTTPException(422, "Choose the server first.")
            try:
                known = {x["id"] for x in await svc.plex().server_sections(own[1], srv["machine_id"])}
            except (PlexError, httpx.HTTPError) as e:
                raise HTTPException(502, f"Could not read the libraries from Plex: {e}")
            picked = [str(x) for x in dict.fromkeys(body.sections)]
            if not picked or any(x not in known for x in picked):
                raise HTTPException(422, "Choose at least one library, all from this server.")
            set_("signup_sections", json.dumps(picked))
        if body.price_id is not None:
            pid = body.price_id.strip()
            if not pid:
                set_("signup_price_id", None)
                set_("signup_plan", None)
            else:
                try:
                    plan = plan_of(await svc.stripe().price(pid))
                except StripeError as e:
                    raise HTTPException(422, f"Stripe does not know that price: {e}")
                if not plan["active"] or not plan["recurring"]:
                    raise HTTPException(422, "Choose an active price that repeats (monthly or yearly).")
                set_("signup_price_id", pid)
                set_("signup_plan", json.dumps(plan))
        if body.cap is not None:
            set_("signup_cap", str(body.cap))
        if body.support is not None:
            set_("signup_support", body.support.strip() or None)
        if body.enabled is not None:
            if body.enabled and svc.problems():
                raise HTTPException(422, "Sign-up is not ready to turn on: " + " ".join(svc.problems()))
            set_("signup_enabled", "1" if body.enabled else None)
        elif svc.settings()["enabled"] and svc.problems():
            set_("signup_enabled", None)   # a change that breaks the setup also closes the door
        if on_change:
            on_change(request, "Changed the sign-up settings")
        return await overview()

    @r.get("/api/settings/signup/libraries")
    async def libraries(request: Request, server_id: int):
        admin(request)
        srv = svc.store.get_server(server_id)
        own = svc.owner(srv)
        if not srv or not own or not srv.get("machine_id"):
            raise HTTPException(422, "Link the Plex account that owns this server first.")
        try:
            return {"libraries": await svc.plex().server_sections(own[1], srv["machine_id"])}
        except (PlexError, httpx.HTTPError) as e:
            raise HTTPException(502, f"Could not read the libraries from Plex: {e}")

    @r.get("/api/settings/signup/prices")
    async def prices(request: Request):
        admin(request)
        try:
            rows = [plan_of(p) for p in await svc.stripe().prices()]
        except StripeError as e:
            raise HTTPException(502, f"Stripe: {e}")
        return {"prices": sorted([p for p in rows if p["recurring"]], key=lambda p: (p["amount"], p["id"]))}

    @r.post("/api/settings/signup/test")
    async def test(request: Request):
        """Proves the setup without charging anyone: opens a checkout and closes it at once, and reads the server's
        libraries and how full it is."""
        admin(request)
        s = svc.settings()
        out: dict = {"stripe": None, "plex": None, "capacity": None}
        try:
            tid = "test-" + secrets.token_urlsafe(8)
            sess = await svc.stripe().create_checkout(tid, 1, s["price_id"] or "", "signup-test@example.com", {"signup_test": "1"},
                                                      str(request.base_url).rstrip("/") + "/signup.html", int(time.time()) + 1900)
            await svc.stripe().expire_checkout(sess["id"])
            out["stripe"] = {"ok": True, "livemode": bool(sess.get("livemode"))}
        except StripeError as e:
            out["stripe"] = {"ok": False, "error": str(e)}
        srv = svc.server(s["server_id"])
        own = svc.owner(srv)
        if not srv or not own:
            out["plex"] = {"ok": False, "error": "The server's owner account is not linked."}
        else:
            try:
                libs = await svc.plex().server_sections(own[1], srv["machine_id"])
                gone = [x for x in s["sections"] if x not in {y["id"] for y in libs}]
                out["plex"] = {"ok": not gone and bool(s["sections"]), "server": srv["name"],
                               "sharing": [y["title"] for y in libs if y["id"] in s["sections"]],
                               "not_sharing": [y["title"] for y in libs if y["id"] not in s["sections"]],
                               "error": "A chosen library is no longer on the server." if gone else None}
            except (PlexError, httpx.HTTPError) as e:
                out["plex"] = {"ok": False, "error": str(e) or type(e).__name__}
            out["capacity"] = await svc.capacity(fresh=True)
        return out

    @r.post("/api/settings/signup/{sid}/retry")
    async def retry(sid: str, request: Request):
        admin(request)
        row = svc.get(sid)
        if not row:
            raise HTTPException(404, "No such sign-up.")
        if row["state"] not in IN_FLIGHT:
            raise HTTPException(409, "There is nothing to retry for this sign-up.")
        svc._set(sid, attempts=0)
        return {"row": svc.admin_row(await svc.advance(sid))}

    @r.delete("/api/settings/signup/{sid}")
    def forget(sid: str, request: Request, force: bool = False):
        """Removes the record here only. Nothing changes at Stripe or on Plex."""
        admin(request)
        row = svc.get(sid)
        if not row:
            raise HTTPException(404, "No such sign-up.")
        if row["state"] in ("paid", "invited") and not force:
            raise HTTPException(409, "This person has paid and is not on the server yet. Retry it, or remove it anyway.")
        if svc.notifier is not None and row["alerted"]:
            svc.notifier.withdraw(ADMINS, f"signup:stuck:{sid}")
        svc.delete(sid)
        return {"ok": True}

    return r
