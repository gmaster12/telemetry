"""Greystar Telemetry: API + static dashboard."""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets as pysecrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import sample
from .config import DB_PATH, WEB_DIR, ServerCfg, env
from .netdata import NetdataClient
from .plex import PlexAccount, PlexClient
from .poller import Poller
from .secrets import SecretBox
from .store import Store

logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("telemetry")

AUTH_USER = env("BASIC_AUTH_USER")
AUTH_PASS = env("BASIC_AUTH_PASSWORD")
ALLOW_OPEN_SETTINGS = env("ALLOW_OPEN_SETTINGS", "false").lower() == "true"

box = SecretBox()
store = Store(DB_PATH, box)
poller = Poller(store)


def client_id() -> str:
    cid = store.get_setting("plex_client_id")
    if not cid:
        cid = f"greystar-telemetry-{uuid.uuid4()}"
        store.set_setting("plex_client_id", cid)
    return cid


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller.run())
    yield
    task.cancel()


app = FastAPI(title="Greystar Telemetry", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------- auth ----------
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)
    if AUTH_USER and AUTH_PASS:
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = pysecrets.compare_digest(user, AUTH_USER) and pysecrets.compare_digest(pw, AUTH_PASS)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Greystar Telemetry"'})
    if request.url.path.startswith("/api/settings") and request.method != "GET":
        if not (AUTH_USER and AUTH_PASS) and not ALLOW_OPEN_SETTINGS:
            return JSONResponse({"detail": "Set BASIC_AUTH_USER and BASIC_AUTH_PASSWORD before changing settings."}, status_code=403)
        if request.headers.get("x-requested-with") != "greystar":
            return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- data for the dashboard ----------
def wrap(rows: list, is_sample: bool) -> dict:
    return {"rows": rows, "sample": is_sample, "updated": poller.last_poll}


@app.get("/api/streams")
def api_streams():
    if poller.cfg.use_sample:
        return wrap(sample.streams(), True)
    return wrap(poller.streams, False)


@app.get("/api/servers")
def api_servers():
    if poller.cfg.use_sample:
        return wrap(sample.servers(), True)
    return wrap(poller.servers_view(), False)


@app.get("/api/bandwidth")
def api_bandwidth():
    if poller.cfg.use_sample:
        return wrap(sample.bandwidth(), True)
    return wrap(store.bandwidth_last_hour([s.name for s in poller.cfg.servers if s.enabled]), False)


@app.get("/api/viewing")
def api_viewing(tz: int = 0):
    """tz: viewer's offset from UTC in minutes (JavaScript's -getTimezoneOffset())."""
    if poller.cfg.use_sample:
        return wrap(sample.viewing(), True)
    return wrap(store.viewing_week(max(-840, min(840, tz))), False)


@app.get("/api/status")
def api_status():
    cfg = poller.cfg
    return {
        "sample": cfg.use_sample,
        "last_poll": poller.last_poll,
        "history_minutes": store.history_minutes(),
        "servers": [{"name": s.name, "enabled": s.enabled, **poller.server_state.get(s.name, {})} for s in cfg.servers],
        "netdata": {"mode": cfg.netdata.mode, "error": poller.netdata_error, "ok_at": poller.netdata_ok_at},
    }


@app.get("/api/poster/{server_id}/{rating_key}")
async def api_poster(server_id: int, rating_key: str):
    path = poller.art.get((server_id, rating_key))
    s = next((x for x in poller.cfg.servers if x.id == server_id), None)
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


async def _account_token_for(machine_id: str | None) -> str | None:
    """Per-server access token from the linked Plex account, resolved server-side."""
    acct = store.get_setting("plex_account_token")
    if not acct:
        return None
    if not machine_id:
        return acct
    for srv in await PlexAccount(poller.http, client_id()).servers(acct):
        if srv["machine_id"] == machine_id:
            return srv["token"] or acct
    return acct


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
        "plex_account_linked": bool(store.get_setting("plex_account_token")),
        "netdata": {
            "mode": cfg.netdata.mode, "space_id": cfg.netdata.space_id, "room_id": cfg.netdata.room_id,
            "has_token": bool(cfg.netdata.token), "cpu_context": cfg.netdata.cpu_context,
            "net_context": cfg.netdata.net_context, "net_out_dimension": cfg.netdata.net_out_dimension,
            "error": poller.netdata_error,
        },
        "hot_cpu_pct": cfg.hot_cpu_pct,
        "sample": cfg.use_sample,
        "auth_configured": bool(AUTH_USER and AUTH_PASS),
    }


@app.post("/api/settings/servers/test")
async def test_server(body: TestIn):
    token = body.token
    if not token and body.server_id:
        s = next((x for x in poller.cfg.servers if x.id == body.server_id), None)
        token = s.token if s else None
    if not token:
        token = await _account_token_for(body.machine_id)
    return await _test_plex(_clean_url(body.plex_url), token)


@app.post("/api/settings/servers")
async def add_server(body: ServerIn):
    url = _clean_url(body.plex_url)
    token = body.token or await _account_token_for(body.machine_id)
    info = await _test_plex(url, token)
    if any(r["name"].lower() == body.name.strip().lower() for r in store.list_servers()):
        raise HTTPException(409, f"A server named {body.name} already exists.")
    data = body.model_dump(exclude={"token"})
    data.update(name=body.name.strip(), plex_url=url, machine_id=body.machine_id or info.get("machine_id"), enabled=int(body.enabled))
    sid = store.add_server(data, token)
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
    token = await PlexAccount(poller.http, client_id()).check_pin(pin_id)
    if token:
        store.set_setting("plex_account_token", token)
    return {"linked": bool(token)}


@app.post("/api/settings/plex/unlink")
def plex_unlink():
    store.set_setting("plex_account_token", None)
    return {"ok": True}


@app.get("/api/settings/plex/discover")
async def plex_discover():
    acct = store.get_setting("plex_account_token")
    if not acct:
        raise HTTPException(409, "Sign in with Plex first.")
    found = await PlexAccount(poller.http, client_id()).servers(acct)
    added = {r["machine_id"] for r in store.list_servers() if r["machine_id"]}
    return {"servers": [{k: v for k, v in s.items() if k != "token"} | {"added": s["machine_id"] in added} for s in found]}


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
        snap = await nd.snapshot()
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "nodes": len(nodes), "values": snap}


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


# ---------- static dashboard ----------
if Path(WEB_DIR).exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
