"""Server telemetry from Netdata: Netdata Cloud (v3 data API) or each agent directly.

Returns per-server values keyed by the server name from servers.yaml.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

from .config import AppCfg, ServerCfg


class NetdataError(Exception):
    pass


def _point_value(cell: Any) -> float | None:
    """json2 cells are either a number or [value, anomaly_rate, point_annotations]."""
    if isinstance(cell, list):
        cell = cell[0] if cell else None
    try:
        return None if cell is None else float(cell)
    except (TypeError, ValueError):
        return None


def parse_json2_by_node(resp: dict, servers: list[ServerCfg]) -> dict[str, float]:
    """Turn a json2 response grouped by node into {server name: value}.

    Labels may be node ids or node names; both are matched against each
    server's `netdata_node` (and its name as a fallback), case-insensitively.
    """
    result = resp.get("result") or resp
    labels = list(result.get("labels") or [])
    rows = result.get("data") or []
    if not labels or not rows:
        return {}
    # Use the most recent row that has values.
    row = next((r for r in reversed(rows) if any(_point_value(c) is not None for c in r[1:])), rows[-1])
    # Node ids (nd), machine GUIDs (mg) -> hostnames (nm), from summary.nodes (v3) or nodes (older).
    id_to_name: dict[str, str] = {}
    for n in ((resp.get("summary") or {}).get("nodes") or []) + list(resp.get("nodes") or []):
        if isinstance(n, dict):
            nm = str(n.get("nm") or n.get("name") or "").lower()
            for k in ("nd", "mg", "id"):
                if n.get(k) and nm:
                    id_to_name[str(n[k]).lower()] = nm
    # Column ids can be node ids while labels are names (or the other way round): match either.
    ids = list(((resp.get("view") or {}).get("dimensions") or {}).get("ids") or [])
    if len(ids) == len(labels) - 1:
        ids = ["time"] + ids
    else:
        ids = labels
    wanted = {}
    for s in servers:
        keys = {s.name.lower()}
        if s.netdata_node:
            keys.add(s.netdata_node.lower())
            if s.netdata_node.lower() in id_to_name:
                keys.add(id_to_name[s.netdata_node.lower()])
        wanted[s.name] = keys
    out: dict[str, float] = {}
    for idx, label in enumerate(labels):
        if idx == 0 or idx >= len(row):
            continue  # first column is time
        cands = {str(label).lower(), str(ids[idx]).lower()}
        cands |= {id_to_name[c] for c in list(cands) if c in id_to_name}
        for name, keys in wanted.items():
            if cands & keys:
                v = _point_value(row[idx])
                if v is not None:
                    out[name] = out.get(name, 0.0) + v
    return out


class NetdataClient:
    def __init__(self, cfg: AppCfg, http: httpx.AsyncClient):
        self.cfg = cfg
        self.nd = cfg.netdata
        self.http = http

    @property
    def enabled(self) -> bool:
        return self.nd.mode in ("cloud", "agent")

    def _cloud_url(self, what: str) -> str:
        if not (self.nd.token and self.nd.space_id and self.nd.room_id):
            raise NetdataError("Netdata Cloud needs an API token, a space ID and a room ID")
        return f"{self.nd.base_url.rstrip('/')}/api/v3/spaces/{self.nd.space_id.strip()}/rooms/{self.nd.room_id.strip()}/{what}"

    async def _cloud_post(self, what: str, body: dict, label: str) -> dict:
        r = await self.http.post(self._cloud_url(what), json=body, timeout=30.0,
                                 headers={"Authorization": f"Bearer {self.nd.token.strip()}", "Accept": "application/json"})
        if r.status_code != 200:
            detail = ""
            try:
                j = r.json()
                detail = j.get("errorMsg") or j.get("message") or j.get("error") or j.get("detail") or ""
            except ValueError:
                detail = r.text[:200]
            hint = {401: " (the API token is wrong or expired)", 403: " (the token has no access to this space or room)",
                    404: " (check the space ID and room ID)"}.get(r.status_code, "")
            raise NetdataError(f"Netdata Cloud HTTP {r.status_code} for {label}{hint}" + (f": {str(detail)[:200]}" if detail else ""))
        return r.json()

    @staticmethod
    def build_body(context: str, dimensions: list[str] | None, node_ids: list[str]) -> dict:
        """v3 data query: one column per node, last minute averaged into one point.

        scope.nodes only accepts Netdata node UUIDs (no hostnames, no wildcards), so it is set only
        when every linked node is a UUID; otherwise all nodes in the room are queried and matched by name.
        """
        scope: dict = {"contexts": [context]}
        if dimensions:
            scope["dimensions"] = dimensions
        if node_ids and all(UUID.match(n) for n in node_ids):
            scope["nodes"] = node_ids
        return {
            "scope": scope,
            "selectors": {"nodes": ["*"], "contexts": ["*"], "instances": ["*"],
                          # CPU: everything except idle (idle is a hidden dimension on some agents)
                          "dimensions": ["*"] if dimensions else ["!idle", "*"], "labels": ["*"], "alerts": ["*"]},
            "window": {"after": -60, "before": 0, "points": 1},
            "aggregations": {"metrics": [{"group_by": ["node"], "aggregation": "sum"}], "time": {"time_group": "average"}},
            "format": "json2",
            "options": ["jsonwrap", "minify", "unaligned", "abs"],
            "timeout": 20000,
        }

    async def _cloud_query(self, context: str, dimensions: list[str] | None) -> dict:
        nodes = [s.netdata_node.strip() for s in self.cfg.servers if s.netdata_node and s.enabled]
        return await self._cloud_post("data", self.build_body(context, dimensions, nodes), context)

    async def list_contexts(self, pattern: str) -> list[str]:
        """Metric names in the room matching a pattern (e.g. *cpu*), to help pick the right ones."""
        data = await self._cloud_post("contexts", {"scope": {"contexts": [pattern]}, "selectors": {"nodes": ["*"], "contexts": ["*"]}}, pattern)
        ctx = data.get("contexts") if isinstance(data, dict) else None
        if isinstance(ctx, dict):
            return sorted(ctx.keys())
        if isinstance(ctx, list):
            return sorted(str(c.get("id") if isinstance(c, dict) else c) for c in ctx)
        return []

    async def _agent_value(self, server: ServerCfg, context: str, dimensions: list[str] | None) -> float | None:
        if not server.netdata_agent_url:
            return None
        params = {"chart": context, "after": -60, "points": 1, "group": "average", "format": "json", "options": "abs"}
        if dimensions:
            params["dimensions"] = "|".join(dimensions)
        r = await self.http.get(server.netdata_agent_url.rstrip("/") + "/api/v1/data", params=params, timeout=8.0)
        if r.status_code != 200:
            raise NetdataError(f"{server.name}: Netdata agent HTTP {r.status_code}")
        data = r.json().get("data") or []
        if not data:
            return None
        return float(sum(abs(v) for v in data[0][1:] if isinstance(v, (int, float))))

    async def list_nodes(self) -> list[dict]:
        """Nodes visible in the configured Netdata Cloud room, for the Settings picker."""
        if self.nd.mode != "cloud":
            return []
        if not (self.nd.token and self.nd.space_id and self.nd.room_id):
            raise NetdataError("Netdata Cloud needs a token, space ID and room ID")
        data = await self._cloud_post("nodes", {}, "the node list")
        items = data.get("nodes", data) if isinstance(data, dict) else data
        out = []
        for n in items or []:
            if not isinstance(n, dict):
                continue
            nid = n.get("nd") or n.get("id") or n.get("nodeID")
            name = n.get("nm") or n.get("name") or n.get("hostname")
            if nid:
                out.append({"id": str(nid), "name": str(name or nid), "state": n.get("state") or n.get("status")})
        return sorted(out, key=lambda x: x["name"].lower())

    async def snapshot(self) -> dict[str, dict[str, float]]:
        """{server name: {"cpu_pct": .., "net_out_mbps": ..}} for servers Netdata knows about."""
        servers = [s for s in self.cfg.servers if s.enabled]
        out: dict[str, dict[str, float]] = {s.name: {} for s in servers}
        if self.nd.mode == "cloud":
            cpu_raw, net_raw = await asyncio.gather(self._cloud_query(self.nd.cpu_context, None),
                                                    self._cloud_query(self.nd.net_context, [self.nd.net_out_dimension]))
            cpu, net = parse_json2_by_node(cpu_raw, servers), parse_json2_by_node(net_raw, servers)
            for name in out:
                if name in cpu:
                    out[name]["cpu_pct"] = round(min(cpu[name], 100.0), 1)
                if name in net:
                    out[name]["net_out_mbps"] = round(abs(net[name]) / self.nd.net_units_per_mbps, 1)
        elif self.nd.mode == "agent":
            async def both(s):
                return await asyncio.gather(self._agent_value(s, self.nd.cpu_context, None),
                                            self._agent_value(s, self.nd.net_context, [self.nd.net_out_dimension]))
            for s, (c, n) in zip(servers, await asyncio.gather(*(both(s) for s in servers))):
                if c is not None:
                    out[s.name]["cpu_pct"] = round(min(c, 100.0), 1)
                if n is not None:
                    out[s.name]["net_out_mbps"] = round(n / self.nd.net_units_per_mbps, 1)
        return out
