"""A small stand-in for plex.tv and Stripe together, for the sign-up tests (app/signup.py).

One app answers both, because the tests hand the code a single httpx client. It keeps everything in `state`,
and writes every call that would change something for real into `calls`, so a test can say "no invite was sent".
"""
from __future__ import annotations

import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

STRIPE_KEY = "rk_live_signupkey"
PUBLISHABLE = "pk_live_signuppub"
TEST_KEY = "rk_test_signupkey"          # Stripe test mode: nothing is charged
TEST_PUBLISHABLE = "pk_test_signuppub"
OWNER_TOKEN = "owner-token"
MACHINE = "machine-galaxy"
PRICE = "price_month"

app = FastAPI()
calls: list[dict] = []
state: dict = {}


def reset():
    state.clear()
    state.update({
        # plex.tv
        "people": {   # token -> account
            OWNER_TOKEN: {"id": 900, "username": "galaxyowner", "title": "Galaxy Owner", "email": "owner@greystar.test"},
            "tok-new": {"id": 3001, "username": "newbie", "title": "New Bie", "email": "Newbie@Example.com"},
            "tok-old": {"id": 2001, "username": "sable", "title": "Sable", "email": "sable@example.com"},
            "tok-two": {"id": 3002, "username": "second", "title": "Second One", "email": "second@example.com"},
        },
        "pins": {},          # pin id -> token (None until approved)
        "sections": [{"id": "11", "key": "1", "title": "TV Shows", "type": "show"}, {"id": "12", "key": "2", "title": "Movies", "type": "movie"},
                     {"id": "14", "key": "4", "title": "4k Movies", "type": "movie"}],
        "shares": [{"id": "500", "userID": "2001", "username": "sable", "email": "sable@example.com", "sections": ["11", "12"]}],
        "invites": [],       # {"id", "email", "sections"}: sent by the owner, not accepted yet
        "fail": {},          # route name -> HTTP status to answer with (invite, shares, requests, accept, checkout)
        "instant": set(),    # emails plex.tv shares with at once (already a friend of the owner)
        "garbage": False,    # answer the share list with something that is not XML
        "subs": {},          # subscription id -> status, when it differs from what its checkout says
        # Stripe
        "sessions": {},
        "prices": {PRICE: {"id": PRICE, "active": True, "type": "recurring", "unit_amount": 1599, "currency": "usd",
                           "recurring": {"interval": "month", "interval_count": 1}, "nickname": None,
                           "product": {"id": "prod_1", "name": "Standard Monthly"}},
                   "price_once": {"id": "price_once", "active": True, "type": "one_time", "unit_amount": 500, "currency": "usd",
                                  "recurring": None, "product": {"id": "prod_2", "name": "Setup fee"}}},
        "next": 100,
    })
    calls.clear()


reset()


def _next() -> int:
    state["next"] += 1
    return state["next"]


def _fail(name: str):
    code = state["fail"].get(name)
    if code:
        raise HTTPException(code, "the mock was told to fail")


def _who(request: Request) -> dict:
    p = state["people"].get(request.headers.get("x-plex-token", ""))
    if not p:
        raise HTTPException(401, "bad token")
    return p


def _owner(request: Request) -> dict:
    if request.headers.get("x-plex-token") != OWNER_TOKEN:
        raise HTTPException(401, "not the owner")
    return state["people"][OWNER_TOKEN]


def _xml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><MediaContainer>{body}</MediaContainer>', media_type="application/xml")


# ---------- plex.tv ----------
@app.post("/api/v2/pins")
def new_pin():
    pid = _next()
    state["pins"][pid] = None
    return JSONResponse({"id": pid, "code": f"code{pid}"}, status_code=201)


def approve(pin_id: int, token: str):
    """The person finished the Plex window."""
    state["pins"][int(pin_id)] = token


@app.get("/api/v2/pins/{pin_id}")
def pin(pin_id: int):
    if pin_id not in state["pins"]:
        raise HTTPException(404, "no such pin")
    return {"id": pin_id, "authToken": state["pins"][pin_id]}


@app.get("/api/v2/user")
def user(request: Request):
    return _who(request)


@app.get("/api/users")
def friends(request: Request):
    _owner(request)
    _fail("users")
    return _xml("".join(
        f'<User id="{s["userID"]}" username="{s["username"]}" title="{s["username"]}" email="{s["email"]}">'
        f'<Server id="1" serverId="1" machineIdentifier="{MACHINE}" name="GALAXY" pending="0"/></User>' for s in state["shares"]))


@app.get("/api/servers/{mid}")
def server(mid: str, request: Request):
    _owner(request)
    return _xml('<Server name="GALAXY">' + "".join(
        f'<Section id="{s["id"]}" key="{s["key"]}" type="{s["type"]}" title="{s["title"]}"/>' for s in state["sections"]) + "</Server>")


@app.get("/api/servers/{mid}/shared_servers")
def shares(mid: str, request: Request):
    _owner(request)
    _fail("shares")
    if state["garbage"]:
        return Response("<html>Bad gateway", media_type="text/html")
    out = ""
    for s in state["shares"]:
        secs = "".join(f'<Section id="{x["id"]}" key="{x["key"]}" title="{x["title"]}" type="{x["type"]}" '
                       f'shared="{1 if x["id"] in s["sections"] else 0}"/>' for x in state["sections"])
        out += (f'<SharedServer id="{s["id"]}" username="{s["username"]}" email="{s["email"]}" userID="{s["userID"]}" '
                f'accessToken="SECRET-ACCESS-TOKEN" acceptedAt="{s.get("acceptedAt", "1")}" invitedAt="1">{secs}</SharedServer>')
    return _xml(out)


@app.post("/api/servers/{mid}/shared_servers")
async def invite(mid: str, request: Request):
    _owner(request)
    body = await request.json()
    calls.append({"what": "invite", "mid": mid, "body": body})
    _fail("invite")
    email = body["shared_server"]["invited_email"].lower()
    secs = [str(x) for x in body["shared_server"]["library_section_ids"]]
    if any(i["email"] == email for i in state["invites"]) or any(s["email"] == email for s in state["shares"]):
        raise HTTPException(422, "already shared")
    if email in state["instant"]:
        p = next(x for x in state["people"].values() if x["email"].lower() == email)
        state["shares"].append({"id": str(_next()), "userID": str(p["id"]), "username": p["username"], "email": email, "sections": secs})
    else:
        state["invites"].append({"id": str(_next()), "email": email, "sections": secs})
    return Response("<MediaContainer/>", media_type="application/xml", status_code=201)


@app.get("/api/invites/requested")
def sent(request: Request):
    _owner(request)
    return _xml("".join(f'<Invite id="{i["id"]}" email="{i["email"]}" username="" friend="1" home="0" server="1">'
                        f'<Server name="GALAXY" numLibraries="{len(i["sections"])}"/></Invite>' for i in state["invites"]))


@app.get("/api/invites/requests")
def received(request: Request):
    me = _who(request)
    _fail("requests")
    o = state["people"][OWNER_TOKEN]
    mine = [i for i in state["invites"] if i["email"] == me["email"].lower()]
    extra = state.get("other_invites", [])   # invites from somebody who is not the owner
    return _xml("".join(f'<Invite id="{i["id"]}" email="{o["email"]}" username="{o["username"]}" friend="1" home="0" server="1">'
                        f'<Server name="GALAXY" numLibraries="2"/></Invite>' for i in mine)
                + "".join(f'<Invite id="{i}" email="stranger@x.com" username="stranger" friend="1" home="0" server="1">'
                          f'<Server name="GALAXY" numLibraries="9"/></Invite>' for i in extra))   # a stranger whose server has the same name


def accept(invite_id: str):
    """The invite becomes a share (they accepted, here or in the Plex app)."""
    i = next(x for x in state["invites"] if x["id"] == str(invite_id))
    p = next(x for x in state["people"].values() if x["email"].lower() == i["email"])
    state["invites"].remove(i)
    state["shares"].append({"id": str(_next()), "userID": str(p["id"]), "username": p["username"], "email": i["email"], "sections": i["sections"]})


@app.put("/api/invites/requests/{invite_id}")
def accept_route(invite_id: str, request: Request):
    me = _who(request)
    calls.append({"what": "accept", "id": invite_id, "by": me["id"], "params": dict(request.query_params)})
    _fail("accept")
    i = next((x for x in state["invites"] if x["id"] == invite_id and x["email"] == me["email"].lower()), None)
    if not i:
        raise HTTPException(404, "no such invite")
    accept(invite_id)
    return Response("", status_code=200)


# ---------- Stripe ----------
def _stripe(request: Request):
    if request.headers.get("authorization") not in (f"Bearer {STRIPE_KEY}", f"Bearer {TEST_KEY}"):
        return JSONResponse({"error": {"message": "Invalid API Key provided"}}, status_code=401)
    return None


def _live(request: Request) -> bool:
    return request.headers.get("authorization") == f"Bearer {STRIPE_KEY}"


@app.post("/v1/checkout/sessions")
async def new_session(request: Request):
    bad = _stripe(request)
    if bad:
        return bad
    form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))   # no python-multipart needed
    calls.append({"what": "checkout", "form": form, "idem": request.headers.get("idempotency-key")})
    if state["fail"].get("checkout"):
        return JSONResponse({"error": {"message": "the mock was told to fail"}}, status_code=state["fail"]["checkout"])
    if form.get("line_items[0][price]") not in state["prices"]:
        return JSONResponse({"error": {"message": "No such price"}}, status_code=400)
    for s in state["sessions"].values():   # the same idempotency key gives the same session back
        if s["_idem"] == request.headers.get("idempotency-key"):
            return s
    sid = f"cs_test_{_next()}"
    state["sessions"][sid] = {
        "id": sid, "object": "checkout.session", "status": "open", "payment_status": "unpaid", "mode": form.get("mode"),
        "ui_mode": form.get("ui_mode"), "client_secret": f"{sid}_secret_abc", "client_reference_id": form.get("client_reference_id"),
        "customer_email": form.get("customer_email"), "customer": None, "subscription": None, "livemode": _live(request),
        "expires_at": int(form.get("expires_at") or time.time() + 3600), "_idem": request.headers.get("idempotency-key")}
    return state["sessions"][sid]


def pay(session_id: str, sub_status: str = "active"):
    """The person paid in Stripe's form."""
    s = state["sessions"][session_id]
    n = _next()
    s.update(status="complete", payment_status="paid", customer=f"cus_{n}", subscription={"id": f"sub_{n}", "status": sub_status})


@app.get("/v1/checkout/sessions/{sid}")
def get_session(sid: str, request: Request):
    bad = _stripe(request)
    if bad:
        return bad
    if state["fail"].get("session"):
        return JSONResponse({"error": {"message": "the mock was told to fail"}}, status_code=state["fail"]["session"])
    if sid not in state["sessions"]:
        return JSONResponse({"error": {"message": "No such checkout.session"}}, status_code=404)
    state.setdefault("read", set()).add(sid)
    return state["sessions"][sid]


@app.get("/v1/subscriptions/{sub_id}")
def get_subscription(sub_id: str, request: Request):
    bad = _stripe(request)
    if bad:
        return bad
    if state["fail"].get("sub"):
        return JSONResponse({"error": {"message": "the mock was told to fail"}}, status_code=state["fail"]["sub"])
    for s in state["sessions"].values():
        if isinstance(s.get("subscription"), dict) and s["subscription"]["id"] == sub_id:
            return {"id": sub_id, "status": state["subs"].get(sub_id, s["subscription"]["status"])}
    return JSONResponse({"error": {"message": "No such subscription"}}, status_code=404)


@app.post("/v1/checkout/sessions/{sid}/expire")
def expire_session(sid: str, request: Request):
    bad = _stripe(request)
    if bad:
        return bad
    calls.append({"what": "expire", "id": sid, "read_first": sid in state.get("read", set())})
    state["sessions"][sid]["status"] = "expired"
    return state["sessions"][sid]


@app.get("/v1/prices")
def prices(request: Request):
    return _stripe(request) or {"object": "list", "data": list(state["prices"].values()), "has_more": False}


@app.get("/v1/prices/{pid}")
def price(pid: str, request: Request):
    bad = _stripe(request)
    if bad:
        return bad
    if pid not in state["prices"]:
        return JSONResponse({"error": {"message": f"No such price: '{pid}'"}}, status_code=404)
    return state["prices"][pid]
