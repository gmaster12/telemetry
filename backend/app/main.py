"""Greystar Telemetry: API + static dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import sample
from .auth import (SECTION_KEYS, SECTIONS, SESSION_COOKIE, Principal, admin_plex_email, check_password, default_sections,
                   end_session, hash_password, new_session, password_problem, plex_principal, principal_for, record_failure,
                   set_cookie, throttled, user_sections)
from .config import DB_PATH, WEB_DIR, ServerCfg, env
from .billing import StripeClient
from .netdata import NetdataClient
from .plex import PlexAccount, PlexClient
from .poller import Poller
from .secrets import SecretBox
from .store import Directory, Store

logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("telemetry")

box = SecretBox()
store = Store(DB_PATH, box)
directory = Directory(store)
poller = Poller(store)


def client_id() -> str:
    cid = store.get_setting("plex_client_id")
    if not cid:
        cid = f"greystar-telemetry-{uuid.uuid4()}"
        store.set_setting("plex_client_id", cid)
    return cid


@asynccontextmanager
async def lifespan(app: FastAPI):
    poller.client_id = client_id()
    task = asyncio.create_task(poller.run())
    yield
    task.cancel()


app = FastAPI(title="Greystar Telemetry", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------- auth ----------
PUBLIC_PATHS = {"/healthz", "/login.html", "/login.js", "/system.css", "/manifest.webmanifest", "/favicon.ico"}
PUBLIC_PREFIXES = ("/assets/", "/vendor/", "/api/auth/")


@app.middleware("http")
async def session_auth(request: Request, call_next):
    path = request.url.path
    p = principal_for(request, store, directory)
    request.state.principal = p
    if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/") and request.headers.get("x-requested-with") != "greystar":
        return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
    public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
    if p is None and not public:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in required."}, status_code=401)
        return RedirectResponse("/login.html", status_code=302)
    if p is not None and path == "/login.html":
        return RedirectResponse("/", status_code=302)
    if path.startswith("/api/settings") and not (p and p.is_admin):
        return JSONResponse({"detail": "Administrators only."}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif path == "/" or path.endswith((".html", ".js", ".css", ".webmanifest")):
        # Always revalidate the page and its code, so an update shows up on the next load
        # instead of mixing a new page with old cached scripts.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def me(request: Request) -> Principal:
    p = request.state.principal
    if p is None:
        raise HTTPException(401, "Sign in required.")
    return p


def need(request: Request, section: str) -> Principal:
    p = me(request)
    if not p.can(section):
        raise HTTPException(403, "You do not have access to this section.")
    return p


def admin(request: Request) -> Principal:
    p = me(request)
    if not p.is_admin:
        raise HTTPException(403, "Administrators only.")
    return p


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- data for the dashboard ----------
def wrap(rows: list, is_sample: bool) -> dict:
    return {"rows": rows, "sample": is_sample, "updated": poller.last_poll}


@app.get("/api/streams")
def api_streams(request: Request):
    need(request, "streams")
    if poller.cfg.use_sample:
        return wrap(sample.streams(), True)
    return wrap(poller.streams, False)


@app.get("/api/servers")
def api_servers(request: Request):
    need(request, "servers")
    if poller.cfg.use_sample:
        return wrap(sample.servers(), True)
    return wrap(poller.servers_view(), False)


@app.get("/api/bandwidth")
def api_bandwidth(request: Request):
    need(request, "bandwidth")
    if poller.cfg.use_sample:
        return wrap(sample.bandwidth(), True)
    return wrap(store.bandwidth_last_hour([s.name for s in poller.cfg.servers if s.enabled]), False)


@app.get("/api/viewing")
def api_viewing(request: Request, tz: int = 0):
    """tz: viewer's offset from UTC in minutes (JavaScript's -getTimezoneOffset())."""
    need(request, "viewing")
    if poller.cfg.use_sample:
        return wrap(sample.viewing(), True)
    return wrap(store.viewing_week(max(-840, min(840, tz))), False)


@app.get("/api/status")
def api_status(request: Request):
    admin(request)
    cfg = poller.cfg
    return {
        "sample": cfg.use_sample,
        "last_poll": poller.last_poll,
        "history_minutes": store.history_minutes(),
        "servers": [{"name": s.name, "enabled": s.enabled, **poller.server_state.get(s.name, {})} for s in cfg.servers],
        "netdata": {"mode": cfg.netdata.mode, "error": poller.netdata_error, "ok_at": poller.netdata_ok_at},
        "users": {"synced_at": poller.users_synced_at or None, "error": poller.users_sync_error},
    }


@app.get("/api/poster/{server_id}/{rating_key}")
async def api_poster(request: Request, server_id: int, rating_key: str):
    me(request)
    s = next((x for x in poller.cfg.servers if x.id == server_id), None)
    path = poller.art.get((server_id, rating_key)) or (directory.art_for(s.name, rating_key) if s else None)
    if not path or not s:
        raise HTTPException(404)
    try:
        data, ctype = await poller.client(s).image(path)
    except Exception:
        raise HTTPException(502)
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


# ---------- settings ----------
class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    plex_url: str = Field(min_length=8, max_length=300)
    token: str | None = Field(default=None, max_length=200)
    machine_id: str | None = None
    max_streams: int = Field(default=8, ge=1, le=200)
    capacity_mbps: float = Field(default=1000, gt=0, le=100000)
    netdata_node: str | None = Field(default=None, max_length=120)
    netdata_agent_url: str | None = Field(default=None, max_length=300)
    enabled: bool = True


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    plex_url: str | None = Field(default=None, min_length=8, max_length=300)
    token: str | None = Field(default=None, max_length=200)
    max_streams: int | None = Field(default=None, ge=1, le=200)
    capacity_mbps: float | None = Field(default=None, gt=0, le=100000)
    netdata_node: str | None = Field(default=None, max_length=120)
    netdata_agent_url: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None


class TestIn(BaseModel):
    plex_url: str
    token: str | None = None
    machine_id: str | None = None
    server_id: int | None = None


class NetdataIn(BaseModel):
    mode: str = Field(pattern="^(cloud|agent|off)$")
    space_id: str | None = None
    room_id: str | None = None
    token: str | None = Field(default=None, max_length=400)
    cpu_context: str | None = None
    net_context: str | None = None
    net_out_dimension: str | None = None


class GeneralIn(BaseModel):
    hot_cpu_pct: float = Field(ge=10, le=100)


def _clean_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(422, "Plex URL must start with http:// or https://")
    return url


async def _account_token_for(machine_id: str | None) -> tuple[str | None, str | None]:
    """(server access token, owner plex id) from whichever linked Plex account owns the server."""
    accounts = store.account_tokens()
    if not machine_id:
        return (accounts[0][1], accounts[0][0]["plex_id"]) if len(accounts) == 1 else (None, None)
    acct = PlexAccount(poller.http, client_id())
    for a, tok in accounts:
        try:
            for srv in await acct.servers(tok):
                if srv["machine_id"] == machine_id:
                    return srv["token"] or tok, a["plex_id"] if srv.get("owned") else None
        except Exception:
            continue
    return None, None


async def _test_plex(url: str, token: str | None) -> dict:
    if not token:
        raise HTTPException(422, "No Plex token. Sign in with Plex or paste a token.")
    c = PlexClient(ServerCfg(id=0, name="test", plex_url=url, token=token), poller.http)
    try:
        ident = await c.identity()
        sessions = await c.sessions()
    except Exception as e:
        raise HTTPException(502, f"Could not reach Plex at {url}: {e}")
    return {"ok": True, "friendly_name": ident.get("friendlyName"), "version": ident.get("version"),
            "machine_id": ident.get("machineIdentifier"), "sessions": len(sessions)}


@app.get("/api/settings")
def get_settings():
    cfg = poller.cfg
    rows = store.list_servers()
    return {
        "servers": [{
            "id": r["id"], "name": r["name"], "plex_url": r["plex_url"], "has_token": bool(r["token_enc"]),
            "max_streams": r["max_streams"], "capacity_mbps": r["capacity_mbps"], "netdata_node": r["netdata_node"],
            "netdata_agent_url": r["netdata_agent_url"], "enabled": bool(r["enabled"]), "machine_id": r["machine_id"],
            "state": poller.server_state.get(r["name"], {}),
        } for r in rows],
        "plex_accounts": [{"id": a["plex_id"], "username": a["username"], "name": a["title"] or a["username"], "email": a["email"],
                           "servers": [r["name"] for r in rows if r.get("owner_plex_id") == a["plex_id"]]} for a in store.list_accounts()],
        "plex_account_linked": bool(store.list_accounts()),
        "netdata": {
            "mode": cfg.netdata.mode, "space_id": cfg.netdata.space_id, "room_id": cfg.netdata.room_id,
            "has_token": bool(cfg.netdata.token), "cpu_context": cfg.netdata.cpu_context,
            "net_context": cfg.netdata.net_context, "net_out_dimension": cfg.netdata.net_out_dimension,
            "error": poller.netdata_error,
        },
        "hot_cpu_pct": cfg.hot_cpu_pct,
        "sample": cfg.use_sample,
        "auth_configured": True,
        "users_sync": {"synced_at": poller.users_synced_at or None, "error": poller.users_sync_error},
    }


@app.post("/api/settings/servers/test")
async def test_server(body: TestIn):
    token = body.token
    if not token and body.server_id:
        s = next((x for x in poller.cfg.servers if x.id == body.server_id), None)
        token = s.token if s else None
    if not token:
        token, _ = await _account_token_for(body.machine_id)
    return await _test_plex(_clean_url(body.plex_url), token)


@app.post("/api/settings/servers")
async def add_server(body: ServerIn):
    url = _clean_url(body.plex_url)
    owner = None
    token = body.token
    if not token:
        token, owner = await _account_token_for(body.machine_id)
    info = await _test_plex(url, token)
    if any(r["name"].lower() == body.name.strip().lower() for r in store.list_servers()):
        raise HTTPException(409, f"A server named {body.name} already exists.")
    data = body.model_dump(exclude={"token"})
    data.update(name=body.name.strip(), plex_url=url, machine_id=body.machine_id or info.get("machine_id"), enabled=int(body.enabled))
    if owner:
        data["owner_plex_id"] = owner
    sid = store.add_server(data, token)
    poller.users_synced_at = 0  # learn the owner and who it is shared with
    poller.kick()
    poller.reload()
    return {"id": sid, **info}


@app.patch("/api/settings/servers/{server_id}")
async def update_server(server_id: int, body: ServerPatch):
    row = store.get_server(server_id)
    if not row:
        raise HTTPException(404)
    data = {k: v for k, v in body.model_dump(exclude={"token"}).items() if v is not None}
    if "plex_url" in data:
        data["plex_url"] = _clean_url(data["plex_url"])
    if "enabled" in data:
        data["enabled"] = int(data["enabled"])
    if "plex_url" in data or body.token:
        s = next((x for x in poller.cfg.servers if x.id == server_id), None)
        await _test_plex(data.get("plex_url", row["plex_url"]), body.token or (s.token if s else None))
    store.update_server(server_id, data, body.token)
    poller.reload()
    return {"ok": True}


@app.delete("/api/settings/servers/{server_id}")
def delete_server(server_id: int):
    if not store.get_server(server_id):
        raise HTTPException(404)
    store.delete_server(server_id)
    poller.reload()
    return {"ok": True}


@app.post("/api/settings/plex/link")
async def plex_link_start():
    return await PlexAccount(poller.http, client_id()).create_pin()


@app.get("/api/settings/plex/link/{pin_id}")
async def plex_link_check(pin_id: int):
    """Link one more Plex account (each server can belong to a different account)."""
    acct = PlexAccount(poller.http, client_id())
    token = await acct.check_pin(pin_id)
    if not token:
        return {"linked": False}
    info = await acct.user_info(token)
    store.save_account(info, token)
    poller.users_synced_at = 0  # load shared users right away
    poller.kick()
    poller.reload()
    return {"linked": True, "account": {"id": str(info["id"]), "name": info.get("title") or info.get("username"), "email": info.get("email")}}


@app.delete("/api/settings/plex/accounts/{plex_id}")
def plex_unlink(plex_id: str):
    """Unlink a Plex account. Its servers keep working with the tokens saved for them."""
    store.delete_account(plex_id)
    return {"ok": True}


@app.get("/api/settings/plex/discover")
async def plex_discover(account: str = ""):
    """Servers owned by the linked Plex accounts (or one of them)."""
    accounts = [x for x in store.account_tokens() if not account or x[0]["plex_id"] == account]
    if not accounts:
        raise HTTPException(409, "Link a Plex account first.")
    added = {r["machine_id"] for r in store.list_servers() if r["machine_id"]}
    out, errors = [], []
    acct = PlexAccount(poller.http, client_id())
    for a, tok in accounts:
        try:
            for s in await acct.servers(tok):
                if s.get("owned"):
                    out.append({k: v for k, v in s.items() if k != "token"} | {"added": s["machine_id"] in added,
                               "account": a["plex_id"], "account_name": a["title"] or a["username"] or a["email"]})
        except Exception as e:
            errors.append(f"{a.get('email') or a['plex_id']}: {e}")
    return {"servers": out, "errors": errors}


@app.put("/api/settings/netdata")
async def put_netdata(body: NetdataIn):
    store.set_setting("netdata_mode", body.mode)
    for key in ("space_id", "room_id", "cpu_context", "net_context", "net_out_dimension"):
        v = getattr(body, key)
        if v is not None:
            store.set_setting(f"netdata_{key}", v.strip() or None)
    if body.token:
        store.set_setting("netdata_token", body.token.strip())
    poller.reload()
    return {"ok": True}


@app.post("/api/settings/netdata/test")
async def test_netdata():
    nd = NetdataClient(poller.cfg, poller.http)
    if poller.cfg.netdata.mode == "off":
        raise HTTPException(422, "Netdata is turned off.")
    try:
        nodes = await nd.list_nodes() if poller.cfg.netdata.mode == "cloud" else []
    except Exception as e:
        raise HTTPException(502, str(e))
    out = {"ok": True, "nodes": len(nodes), "node_names": [n["name"] for n in nodes][:20], "values": {}, "error": None, "contexts": {}}
    try:
        out["values"] = await nd.snapshot()
    except Exception as e:
        out["ok"], out["error"] = False, str(e)
    if poller.cfg.netdata.mode == "cloud":
        # Show which CPU and network metric names this room really has (Windows agents may differ).
        for key, pattern in (("cpu", "*cpu*"), ("net", "*net*")):
            try:
                out["contexts"][key] = (await nd.list_contexts(pattern))[:40]
            except Exception:
                out["contexts"][key] = []
    return out


@app.get("/api/settings/netdata/nodes")
async def netdata_nodes():
    try:
        return {"nodes": await NetdataClient(poller.cfg, poller.http).list_nodes()}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.put("/api/settings/general")
def put_general(body: GeneralIn):
    store.set_setting("hot_cpu_pct", str(body.hot_cpu_pct))
    poller.reload()
    return {"ok": True}


# ---------- sign in ----------
class LocalLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# A sign-in PIN can only be finished by the browser that started it.
PIN_COOKIE = "gst_pin"
_pins: dict[str, tuple[int, float]] = {}


@app.post("/api/auth/plex/start")
async def auth_plex_start(request: Request):
    if throttled("plex:" + _client_ip(request), limit=30):
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    pin = await PlexAccount(poller.http, client_id()).create_pin()
    now = time.time()
    for k, (_, t) in list(_pins.items()):
        if now - t > 900:
            _pins.pop(k, None)
    nonce = uuid.uuid4().hex
    _pins[nonce] = (int(pin["pin_id"]), now)
    resp = JSONResponse(pin)
    resp.set_cookie(PIN_COOKIE, nonce, max_age=900, httponly=True, samesite="lax", path="/api/auth/",
                    secure=request.url.scheme == "https")
    return resp


@app.get("/api/auth/plex/check/{pin_id}")
async def auth_plex_check(pin_id: int, request: Request):
    bound = _pins.get(request.cookies.get(PIN_COOKIE, ""))
    if not bound or bound[0] != pin_id:
        raise HTTPException(400, "This sign-in expired. Start again.")
    acct = PlexAccount(poller.http, client_id())
    token = await acct.check_pin(pin_id)
    if not token:
        return {"pending": True}
    info = await acct.user_info(token)
    is_admin = bool(info.get("email") and info["email"] == admin_plex_email())
    known = directory.get_user(info["id"]) or (directory.find_user_by_email(info["email"]) if info.get("email") else None)
    if not is_admin and not known:
        record_failure("plex:" + _client_ip(request))
        raise HTTPException(403, "This Plex account does not have access to any Greystar server. Ask the administrator to share a library with you.")
    if known and known["plex_id"] != info["id"]:
        info["id"] = known["plex_id"]
    directory.upsert_user(info, is_owner=None)
    if is_admin and not store.list_accounts():
        store.save_account(info, token)  # first run: lets Settings find this account's servers
        poller.users_synced_at = 0
        poller.kick()
    user = directory.get_user(info["id"])
    if user and user.get("disabled"):
        raise HTTPException(403, "Your access to this dashboard has been turned off.")
    _pins.pop(request.cookies.get(PIN_COOKIE, ""), None)
    session = new_session(directory, "plex", info["id"])
    resp = JSONResponse({"ok": True, "role": "admin" if is_admin else "viewer"})
    set_cookie(resp, request, session)
    resp.delete_cookie(PIN_COOKIE, path="/api/auth/")
    return resp


@app.post("/api/auth/local")
def auth_local(body: LocalLogin, request: Request):
    key = "local:" + _client_ip(request)
    if throttled(key):
        raise HTTPException(429, "Too many failed sign-ins. Wait a few minutes.")
    a = directory.get_admin(body.username.strip())
    if not a or not check_password(body.password, a["pw_hash"]):
        record_failure(key)
        raise HTTPException(401, "Wrong username or password.")
    session = new_session(directory, "local", str(a["id"]))
    resp = JSONResponse({"ok": True, "role": "admin"})
    set_cookie(resp, request, session)
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    end_session(directory, request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/api/me")
def api_me(request: Request):
    return me(request).public()


# ---------- playback history and users ----------
def _history_rows(rows: list[dict]) -> list[dict]:
    ids = {s.name: s.id for s in poller.cfg.servers}
    out = []
    for r in rows:
        sid = ids.get(r["server"])
        out.append({
            "id": r["id"], "server": r["server"], "title": r["title"], "subtitle": r["subtitle"], "kind": r["kind"],
            "device": r["device"], "player": r["player"], "location": r["location"], "method": r["method"],
            "resolution": r["resolution"], "started_at": r["started_at"], "stopped_at": r["stopped_at"],
            "watched_min": round((r["watched_sec"] or 0) / 60), "progress_pct": r["progress_pct"], "source": r["source"],
            "live": r["stopped_at"] is None,
            "poster": f"/api/poster/{sid}/{r['rating_key']}" if sid and r["rating_key"] and r["art"] else None,
        })
    return out


def _user_public(u: dict, with_email: bool = True) -> dict:
    return {
        "id": u["plex_id"], "username": u.get("username"), "name": u.get("title") or u.get("username"),
        "email": u.get("email") if with_email else None, "thumb": u.get("thumb"), "shared": bool(u.get("shared")),
        "owner": bool(u.get("is_owner")), "disabled": bool(u.get("disabled")), "last_seen": u.get("last_seen"),
        "last_login": u.get("last_login"), "custom_sections": json.loads(u["sections"]) if u.get("sections") else None,
        "sections": user_sections(store, u), "admin": bool(u.get("email") and u["email"].lower() == admin_plex_email()),
        "servers": json.loads(u["servers"]) if u.get("servers") else [],
    }


@app.get("/api/me/history")
def api_my_history(request: Request, before: int | None = None, limit: int = 50):
    p = need(request, "history")
    if p.kind != "plex":
        return {"user": {"name": p.name}, "stats": None, "rows": []}
    u = directory.get_user(p.subject)
    return {"user": _user_public(u, with_email=True), "stats": directory.user_stats(p.subject),
            "rows": _history_rows(directory.history(p.subject, before, limit))}


@app.get("/api/users")
def api_users(request: Request, q: str = "", server: str = "", limit: int = 50):
    """Search people by username, display name or email. server: only people on that server."""
    admin(request)
    rows = directory.search_users(q[:100], max(1, min(limit, 5000)) if not server else 5000)
    billing = poller.billing.status_map()
    out = [{**_user_public(u), "plays": u["plays"], "plays_30d": u["plays_30d"], "last_play": u["last_play"],
            "billing_status": billing.get(u["plex_id"], "none")} for u in rows]
    if server:
        out = [u for u in out if server in u["servers"]][: max(1, min(limit, 5000))]
    return {"rows": out, "servers": [s.name for s in poller.cfg.servers]}


@app.get("/api/users/{plex_id}")
def api_user(request: Request, plex_id: str):
    admin(request)
    u = directory.get_user(plex_id)
    if not u:
        raise HTTPException(404, "No such user.")
    return {"user": _user_public(u), "stats": directory.user_stats(plex_id), "billing": poller.billing.summary(plex_id)}


@app.get("/api/users/{plex_id}/history")
def api_user_history(request: Request, plex_id: str, before: int | None = None, limit: int = 50):
    admin(request)
    if not directory.get_user(plex_id):
        raise HTTPException(404, "No such user.")
    return {"rows": _history_rows(directory.history(plex_id, before, limit))}


@app.get("/api/me/subscription")
def api_my_subscription(request: Request):
    """The signed-in person's own Stripe subscription. Everyone may see their own."""
    p = me(request)
    if p.kind != "plex":
        return {"status": "none", "subscriptions": [], "last_payment": None, "configured": bool(store.get_setting("stripe_key"))}
    return {**poller.billing.summary(p.subject), "configured": bool(store.get_setting("stripe_key"))}


# ---------- settings: billing (Stripe; PayPal later) ----------
class StripeKeyIn(BaseModel):
    key: str = Field(min_length=10, max_length=300)


class BillingLinkIn(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
    plex_id: str = Field(min_length=1, max_length=64)


@app.get("/api/settings/billing")
def get_billing():
    return {"has_key": bool(store.get_setting("stripe_key")), "synced_at": poller.billing_synced_at or None,
            "error": poller.billing_error, "counts": poller.billing.counts(), "links": poller.billing.links()}


@app.put("/api/settings/billing")
async def put_billing(body: StripeKeyIn):
    key = body.key.strip()
    if not key.startswith(("rk_", "sk_")):
        raise HTTPException(422, "That does not look like a Stripe key. Use a restricted key (rk_live_...).")
    check = await StripeClient(key, poller.http).check()
    missing = [k for k, v in check.items() if v is not True]
    if "customers" in missing:
        raise HTTPException(422, f"Stripe refused the key: {check['customers']}")
    store.set_setting("stripe_key", key)
    poller.billing_synced_at = 0  # sync right away
    poller.kick()
    return {"ok": True, "missing": missing}


@app.delete("/api/settings/billing")
def delete_billing():
    store.set_setting("stripe_key", None)
    return {"ok": True}


@app.post("/api/settings/billing/sync")
async def sync_billing():
    if not store.get_setting("stripe_key"):
        raise HTTPException(409, "Add a Stripe key first.")
    try:
        counts = await poller.sync_billing()
    except Exception as e:
        raise HTTPException(502, f"Stripe sync failed: {e}")
    return {"ok": True, **counts, **poller.billing.counts()}


@app.get("/api/settings/billing/unmatched")
def billing_unmatched(q: str = "", limit: int = 100):
    return {"rows": poller.billing.unmatched(q[:100], max(1, min(limit, 500)))}


@app.post("/api/settings/billing/links")
def billing_link(body: BillingLinkIn):
    if not directory.get_user(body.plex_id):
        raise HTTPException(404, "No such Plex user.")
    poller.billing.link(body.email, body.plex_id)
    return {"ok": True, "summary": poller.billing.summary(body.plex_id)}


@app.delete("/api/settings/billing/links/{email}")
def billing_unlink(email: str):
    poller.billing.unlink(email)
    return {"ok": True}


# ---------- settings: access, admins, users, history import ----------
class DefaultsIn(BaseModel):
    sections: list[str]


class UserAccessIn(BaseModel):
    sections: list[str] | None = None
    use_defaults: bool = False
    disabled: bool | None = None


class AdminIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


@app.get("/api/settings/access")
def get_access(request: Request):
    p = admin(request)
    return {"sections": SECTIONS, "default_sections": default_sections(store), "admin_plex_email": admin_plex_email(),
            "admins": directory.list_admins(), "me": p.public()}


@app.put("/api/settings/access/defaults")
def put_defaults(body: DefaultsIn):
    store.set_setting("default_sections", json.dumps([s for s in body.sections if s in SECTION_KEYS]))
    return {"ok": True}


@app.put("/api/settings/users/{plex_id}")
def put_user_access(plex_id: str, body: UserAccessIn):
    u = directory.get_user(plex_id)
    if not u:
        raise HTTPException(404, "No such user.")
    if body.disabled and u.get("email") and u["email"].lower() == admin_plex_email():
        raise HTTPException(409, "The administrator Plex account cannot be turned off.")
    sections = [s for s in body.sections if s in SECTION_KEYS] if body.sections is not None else None
    directory.set_user_access(plex_id, sections=sections, use_defaults=body.use_defaults, disabled=body.disabled)
    return {"ok": True}


@app.post("/api/settings/admins")
def add_admin(body: AdminIn):
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    try:
        aid = directory.add_admin(body.username, hash_password(body.password))
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"An admin login named {body.username} already exists.")
    return {"id": aid}


@app.delete("/api/settings/admins/{admin_id}")
def delete_admin(admin_id: int, request: Request):
    p = admin(request)
    if p.kind == "local" and p.subject == str(admin_id):
        raise HTTPException(409, "You cannot delete the login you are using.")
    if not directory.get_admin_by_id(admin_id):
        raise HTTPException(404)
    if len(directory.list_admins()) == 1 and not admin_plex_email():
        raise HTTPException(409, "This is the only way to administer the dashboard. Set ADMIN_PLEX_EMAIL or add another admin first.")
    directory.delete_admin(admin_id)
    return {"ok": True}


@app.put("/api/settings/admins/me/password")
def change_password(body: PasswordIn, request: Request):
    p = admin(request)
    if p.kind != "local":
        raise HTTPException(409, "Your Plex account's password is managed by Plex.")
    a = directory.get_admin_by_id(int(p.subject))
    if not a or not check_password(body.current, a["pw_hash"]):
        raise HTTPException(401, "Current password is wrong.")
    problem = password_problem(body.new)
    if problem:
        raise HTTPException(422, problem)
    directory.set_admin_password(a["id"], hash_password(body.new))
    session = new_session(directory, "local", str(a["id"]))
    resp = JSONResponse({"ok": True})
    set_cookie(resp, request, session)
    return resp


@app.post("/api/settings/users/sync")
async def sync_users():
    try:
        n = await poller.sync_users()
    except Exception as e:
        raise HTTPException(502, f"Could not load users from plex.tv: {e}")
    if poller.users_sync_error:
        raise HTTPException(409, poller.users_sync_error)
    return {"ok": True, "users": n}


@app.post("/api/settings/history/import")
async def import_history():
    if poller.cfg.use_sample:
        raise HTTPException(409, "Add a Plex server first.")
    try:
        await poller.sync_users()
    except Exception:
        pass
    return await poller.import_history()


# ---------- static dashboard ----------
if Path(WEB_DIR).exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
