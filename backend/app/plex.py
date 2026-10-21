"""Direct Plex Media Server client and session normalisation.

Talks to each server's own HTTP API (no Tautulli). Every request carries the
X-Plex-Token from the environment; tokens never leave the backend.
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .config import ServerCfg

PLEX_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Product": "Greystar Telemetry",
    "X-Plex-Client-Identifier": "greystar-telemetry",
}

DECISION = {"transcode": "Transcode", "copy": "Copy", "directplay": "Direct play", "burn": "Burn"}
STATE = {"playing": "Playing", "paused": "Paused", "buffering": "Buffering"}


class PlexError(Exception):
    pass


class PlexClient:
    def __init__(self, server: ServerCfg, http: httpx.AsyncClient):
        self.server = server
        self.http = http
        self._meta_cache: dict[str, tuple[float, dict]] = {}

    def _headers(self) -> dict:
        if not self.server.token:
            raise PlexError(f"{self.server.name}: no Plex token saved")
        return {**PLEX_HEADERS, "X-Plex-Token": self.server.token}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = self.server.plex_url.rstrip("/") + path
        r = await self.http.get(url, headers=self._headers(), params=params, timeout=8.0)
        if r.status_code != 200:
            raise PlexError(f"{self.server.name}: HTTP {r.status_code} on {path}")
        return r.json()

    async def identity(self) -> dict:
        data = await self._get("/")
        return data.get("MediaContainer", {})

    async def sessions(self) -> list[dict]:
        data = await self._get("/status/sessions")
        return data.get("MediaContainer", {}).get("Metadata", []) or []

    async def metadata(self, rating_key: str) -> dict:
        """Source media details, cached for 10 minutes (sessions describe the transcoded output)."""
        hit = self._meta_cache.get(rating_key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        data = await self._get(f"/library/metadata/{rating_key}")
        items = data.get("MediaContainer", {}).get("Metadata", []) or [{}]
        self._meta_cache[rating_key] = (time.time(), items[0])
        return items[0]

    async def image(self, path: str, width: int = 300, height: int = 450) -> tuple[bytes, str]:
        params = {"url": path, "width": width, "height": height, "minSize": 1, "upscale": 1}
        url = self.server.plex_url.rstrip("/") + "/photo/:/transcode"
        r = await self.http.get(url, headers=self._headers(), params=params, timeout=10.0)
        if r.status_code != 200:
            raise PlexError(f"{self.server.name}: image HTTP {r.status_code}")
        return r.content, r.headers.get("content-type", "image/jpeg")

    async def accounts(self) -> dict[str, str]:
        """Server-local account id -> name (used to map imported history to users)."""
        data = await self._get("/accounts")
        return {str(a.get("id")): a.get("name", "") for a in data.get("MediaContainer", {}).get("Account", []) or []}

    async def history(self, size: int = 500) -> list[dict]:
        """Plex's own watch history (newest first): when each item was viewed and by which account."""
        data = await self._get("/status/sessions/history/all", {"sort": "viewedAt:desc", "X-Plex-Container-Start": 0, "X-Plex-Container-Size": size})
        return data.get("MediaContainer", {}).get("Metadata", []) or []

    async def terminate(self, session_id: str, reason: str) -> None:
        """Plex Pass: stop a playback session. Not exposed unless ENABLE_ACTIONS=true."""
        await self._get("/status/sessions/terminate", {"sessionId": session_id, "reason": reason})


# ---------- plex.tv account: sign in with Plex and discover servers ----------

PLEX_TV = os.environ.get("PLEX_TV_URL", "https://plex.tv")


class PlexAccount:
    """PIN-based "Sign in with Plex" and server discovery through plex.tv."""

    def __init__(self, http: httpx.AsyncClient, client_id: str):
        self.http = http
        self.headers = {**PLEX_HEADERS, "X-Plex-Client-Identifier": client_id}
        self.client_id = client_id

    async def create_pin(self) -> dict:
        r = await self.http.post(f"{PLEX_TV}/api/v2/pins", params={"strong": "true"}, headers=self.headers, timeout=10.0)
        if r.status_code not in (200, 201):
            raise PlexError(f"plex.tv PIN request failed: HTTP {r.status_code}")
        pin = r.json()
        auth_url = (
            os.environ.get("PLEX_AUTH_URL", "https://app.plex.tv/auth") + "#?"
            f"clientID={self.client_id}&code={pin['code']}"
            "&context%5Bdevice%5D%5Bproduct%5D=Greystar%20Telemetry"
        )
        return {"pin_id": pin["id"], "code": pin["code"], "auth_url": auth_url}

    async def check_pin(self, pin_id: int) -> str | None:
        r = await self.http.get(f"{PLEX_TV}/api/v2/pins/{int(pin_id)}", headers=self.headers, timeout=10.0)
        if r.status_code != 200:
            raise PlexError(f"plex.tv PIN check failed: HTTP {r.status_code}")
        return r.json().get("authToken") or None

    async def user_info(self, token: str) -> dict:
        """The signed-in Plex account: id, username, email, title, thumb."""
        r = await self.http.get(f"{PLEX_TV}/api/v2/user", headers={**self.headers, "X-Plex-Token": token}, timeout=10.0)
        if r.status_code != 200:
            raise PlexError(f"plex.tv account lookup failed: HTTP {r.status_code}")
        u = r.json()
        return {"id": str(u.get("id")), "username": u.get("username") or u.get("title"), "title": u.get("title") or u.get("username"),
                "email": (u.get("email") or "").lower() or None, "thumb": u.get("thumb")}

    async def shared_users(self, owner_token: str) -> list[dict]:
        """Everyone the owner shares servers with (plex.tv/api/users, XML)."""
        r = await self.http.get(f"{PLEX_TV}/api/users", headers={**self.headers, "Accept": "application/xml", "X-Plex-Token": owner_token}, timeout=20.0)
        if r.status_code != 200:
            raise PlexError(f"plex.tv user list failed: HTTP {r.status_code}")
        out = []
        for u in ET.fromstring(r.text).iter("User"):
            a = u.attrib
            out.append({"id": str(a.get("id")), "username": a.get("username") or a.get("title"), "title": a.get("title") or a.get("username"),
                        "email": (a.get("email") or "").lower() or None, "thumb": a.get("thumb"),
                        "servers": [s.attrib.get("name") for s in u.iter("Server") if s.attrib.get("name")]})
        return out

    async def servers(self, account_token: str) -> list[dict]:
        r = await self.http.get(
            f"{PLEX_TV}/api/v2/resources",
            params={"includeHttps": 1, "includeRelay": 1},
            headers={**self.headers, "X-Plex-Token": account_token},
            timeout=15.0,
        )
        if r.status_code != 200:
            raise PlexError(f"plex.tv server list failed: HTTP {r.status_code}")
        out = []
        for res in r.json():
            if "server" not in str(res.get("provides", "")):
                continue
            conns = [
                {"uri": c.get("uri"), "local": bool(c.get("local")), "relay": bool(c.get("relay")), "address": c.get("address")}
                for c in res.get("connections") or []
            ]
            out.append({
                "name": res.get("name"),
                "machine_id": res.get("clientIdentifier"),
                "owned": bool(res.get("owned")),
                "version": res.get("productVersion"),
                "token": res.get("accessToken"),
                "connections": conns,
            })
        return out


# ---------- normalisation ----------

def _res_label(value: Any) -> str:
    v = str(value or "").lower()
    if not v:
        return ""
    if v in ("4k", "2160"):
        return "4K"
    if v == "sd":
        return "SD"
    return v if v.endswith("p") else f"{v}p"


def _height_label(h: Any) -> str:
    try:
        h = int(h)
    except (TypeError, ValueError):
        return ""
    if h >= 1600:
        return "4K"
    for cut in (1080, 720, 576, 480):
        if h >= cut - 40:
            return f"{cut}p"
    return "SD"


def _streams(media: dict) -> list[dict]:
    parts = media.get("Part") or [{}]
    return parts[0].get("Stream") or []


def _first(streams: list[dict], kind: int, selected_only: bool = False) -> dict:
    for s in streams:
        if s.get("streamType") == kind and (not selected_only or s.get("selected")):
            return s
    return {}


def _hdr(video: dict) -> str:
    if video.get("DOVIPresent"):
        return " Dolby Vision"
    if str(video.get("colorTrc", "")).lower() in ("smpte2084", "arib-std-b67"):
        return " HDR10" if video.get("colorTrc") == "smpte2084" else " HLG"
    return ""


def _audio_label(codec: Any, channels: Any) -> str:
    codec = str(codec or "").upper()
    layout = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(int(channels or 0), f"{channels}ch" if channels else "")
    return f"{codec} {layout}".strip() or "None"


def _infer_reason(vd: str, ad: str, sub_decision: str, src_res: str, out_res: str, src_container: str, out_container: str) -> str:
    """Plex does not report why it transcodes; this is a best guess from the decisions."""
    if sub_decision == "burn":
        return "Burned-in subtitles"
    if vd == "transcode":
        if src_res and out_res and src_res != out_res:
            return "Quality or bandwidth limit"
        return "Video codec not supported by the player"
    if ad == "transcode":
        return "Audio codec not supported by the player"
    if src_container and out_container and src_container.lower() != out_container.lower():
        return "Container not supported by the player"
    return "None"


def normalize_session(server: ServerCfg, item: dict, source: dict | None = None) -> dict:
    """Map one /status/sessions entry to the row shape the dashboard expects."""
    source = source or item
    player = item.get("Player") or {}
    session = item.get("Session") or {}
    ts = item.get("TranscodeSession") or {}
    out_media = (item.get("Media") or [{}])[0]
    src_media = (source.get("Media") or [{}])[0]
    src_streams = _streams(src_media)
    src_video = _first(src_streams, 1)
    src_audio = _first(src_streams, 2, selected_only=True) or _first(src_streams, 2)
    out_sub = _first(_streams(out_media), 3, selected_only=True)

    item_type = item.get("type", "")
    section = str(item.get("librarySectionTitle", "")).lower()
    if item_type == "episode":
        kind, title = "Episode", item.get("grandparentTitle") or item.get("title", "")
        subtitle = f"S{item.get('parentIndex', '?')} · E{item.get('index', '?')} · {item.get('title', '')}"
        art = item.get("grandparentThumb") or item.get("thumb")
    elif item_type == "track":
        kind = "Audiobook" if "audiobook" in section else "Music"
        title = item.get("grandparentTitle") or item.get("title", "")
        subtitle = item.get("title", "")
        art = item.get("parentThumb") or item.get("thumb")
    else:
        kind, title, subtitle, art = "Movie", item.get("title", ""), str(item.get("year", "") or ""), item.get("thumb")

    vd_raw = str(ts.get("videoDecision", "")).lower()
    ad_raw = str(ts.get("audioDecision", "")).lower()
    sub_raw = str(ts.get("subtitleDecision", "")).lower()
    has_video = bool(src_video or src_media.get("videoCodec") or src_media.get("videoResolution") or out_media.get("videoResolution"))
    is_audio = kind in ("Audiobook", "Music") or not has_video
    if not ts:
        vd_raw = ad_raw = "directplay"
    if is_audio:
        vd_raw = "none"

    src_res = "Audio" if is_audio else _res_label(src_media.get("videoResolution"))
    out_res = src_res if vd_raw != "transcode" else (_height_label(ts.get("height")) or _res_label(out_media.get("videoResolution")) or src_res)
    src_container = str(src_media.get("container", "") or "")
    out_container = str(ts.get("container", "") or src_container)

    if "transcode" in (vd_raw, ad_raw):
        method = "Transcode"
    elif "copy" in (vd_raw, ad_raw) or (ts and out_container.lower() != src_container.lower()):
        method = "Direct stream"
    else:
        method = "Direct play"

    src_vid_label = "None" if is_audio else f"{str(src_media.get('videoCodec', '')).upper()} {src_res}{_hdr(src_video)}".strip()
    out_vid_label = src_vid_label if vd_raw != "transcode" else f"{str(ts.get('videoCodec', '')).upper()} {out_res}".strip()
    src_aud_label = _audio_label(src_media.get("audioCodec") or src_audio.get("codec"), src_media.get("audioChannels") or src_audio.get("channels"))
    out_aud_label = src_aud_label if ad_raw != "transcode" else _audio_label(ts.get("audioCodec"), ts.get("audioChannels"))

    bandwidth_kbps = session.get("bandwidth") or out_media.get("bitrate") or 0
    bitrate_mbps = round(float(bandwidth_kbps) / 1000.0, 1)
    duration = float(item.get("duration") or 0)
    offset = float(item.get("viewOffset") or 0)
    location = str(session.get("location") or ("lan" if player.get("local") else "wan")).upper()
    hw = bool(ts.get("transcodeHwEncoding") or ts.get("transcodeHwDecoding"))
    subs = out_sub.get("displayTitle") or out_sub.get("language") or ""
    if subs and sub_raw == "burn":
        subs += ", burned in"

    row = {
        "id": f"{server.name}-{item.get('sessionKey', session.get('id', '?'))}",
        "session_id": session.get("id"),
        "rating_key": str(item.get("ratingKey", "")),
        "user": (item.get("User") or {}).get("title", "Unknown"),
        "user_id": str((item.get("User") or {}).get("id") or ""),
        "user_thumb": (item.get("User") or {}).get("thumb"),
        "title": title,
        "subtitle": subtitle,
        "kind": kind,
        "device": player.get("title") or player.get("device") or player.get("product", ""),
        "platform": player.get("platform", ""),
        "player": " ".join(p for p in (player.get("product"), player.get("version")) if p),
        "resolution": out_res or src_res,
        "method": method,
        "bitrate_mbps": bitrate_mbps,
        "server": server.name,
        "state": STATE.get(str(player.get("state", "")).lower(), "Playing"),
        "progress_pct": round(offset / duration * 100) if duration else 0,
        "elapsed_min": round(offset / 60000),
        "duration_min": round(duration / 60000),
        "location": "LAN" if location == "LAN" else "WAN",
        "source_video": src_vid_label,
        "stream_video": out_vid_label,
        "video_decision": DECISION.get(vd_raw, "None"),
        "source_audio": src_aud_label,
        "stream_audio": out_aud_label,
        "audio_decision": DECISION.get(ad_raw, "Direct play"),
        "container": src_container.upper() if out_container.lower() == src_container.lower() else f"{src_container.upper()} → {out_container.upper()}",
        "subtitles": subs or "None",
        "transcode_reason": _infer_reason(vd_raw, ad_raw, sub_raw, src_res, out_res, src_container, out_container) if method != "Direct play" else "None",
        "hw_accel": ("Hardware" if hw else "None (CPU)") if method == "Transcode" else "None",
        "transcode_speed": round(float(ts["speed"]), 1) if ts.get("speed") not in (None, "") else None,
        "throttled": bool(ts.get("throttled")),
        "quality": f"{round(bitrate_mbps)} Mbps {out_res}".strip() if method == "Transcode" else "Original",
        "poster": None,
        "_art": art,
    }
    return row
