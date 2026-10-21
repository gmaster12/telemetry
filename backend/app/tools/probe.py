"""Debug probe: print what Plex and Netdata actually return, so field mappings
can be checked against real servers. Tokens are never printed.

    docker compose exec telemetry python -m app.tools.probe plex            # all servers
    docker compose exec telemetry python -m app.tools.probe plex Atlas      # one server
    docker compose exec telemetry python -m app.tools.probe netdata
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

from ..config import DB_PATH
from ..netdata import NetdataClient, parse_json2_by_node
from ..plex import PlexClient, normalize_session
from ..secrets import SecretBox
from ..store import Store


def show(title: str, obj) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(obj, indent=2, default=str)[:20000])


async def probe_plex(name: str | None) -> None:
    cfg = Store(DB_PATH, SecretBox()).app_config()
    async with httpx.AsyncClient(follow_redirects=True) as http:
        for s in cfg.servers:
            if name and s.name.lower() != name.lower():
                continue
            c = PlexClient(s, http)
            try:
                items = await c.sessions()
            except Exception as e:
                show(f"{s.name}: ERROR", str(e))
                continue
            show(f"{s.name}: raw /status/sessions ({len(items)} sessions)", items[:3])
            for it in items:
                src = it
                if it.get("TranscodeSession") and it.get("ratingKey"):
                    src = await c.metadata(str(it["ratingKey"]))
                row = normalize_session(s, it, src)
                show(f"{s.name}: normalized {row['id']}", row)


async def probe_netdata() -> None:
    cfg = Store(DB_PATH, SecretBox()).app_config()
    async with httpx.AsyncClient() as http:
        nd = NetdataClient(cfg, http)
        if cfg.netdata.mode == "cloud":
            show("nodes", await nd.list_nodes())
            raw = await nd._cloud_query(cfg.netdata.cpu_context, None)
            show(f"raw {cfg.netdata.cpu_context}", raw)
            show("parsed cpu by server", parse_json2_by_node(raw, cfg.servers))
            raw = await nd._cloud_query(cfg.netdata.net_context, [cfg.netdata.net_out_dimension])
            show(f"raw {cfg.netdata.net_context}", raw)
        show("snapshot", await nd.snapshot())


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("plex", "netdata"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "plex":
        asyncio.run(probe_plex(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        asyncio.run(probe_netdata())


if __name__ == "__main__":
    main()
