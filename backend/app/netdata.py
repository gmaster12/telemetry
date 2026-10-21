"""Server telemetry from Netdata: Netdata Cloud (v3 data API) or each agent directly.

Returns per-server values keyed by the server name from servers.yaml.
"""
from __future__ import annotations

from typing import Any

import httpx

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
    labels = result.get("labels") or []
    rows = result.get("data") or []
    if not labels or not rows:
        return {}
    # Use the most recent row that has values.
    row = rows[-1]
    id_to_name: dict[str, str] = {}
    for n in resp.get("nodes") or []:
        if isinstance(n, dict):
            nid = str(n.get("nd") or n.get("id") or "")
            nm = str(n.get("nm") or n.get("name") or "")
            if nid:
                id_to_name[nid.lower()] = nm.lower()
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
        lab = str(label).lower()
        lab_name = id_to_name.get(lab, lab)
        for name, keys in wanted.items():
            if lab in keys or lab_name in keys:
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

    async def _cloud_query(self, context: str, dimensions: list[str] | None) -> dict:
        if not (self.nd.token and self.nd.space_id and self.nd.room_id):
            raise NetdataError("Netdata Cloud needs NETDATA_TOKEN, space_id and room_id")
        nodes = [s.netdata_node for s in self.cfg.servers if s.netdata_node and s.enabled] or ["*"]
        body = {
            "format": "json2",
            "options": ["jsonwrap", "minify", "unaligned"],
            "scope": {"contexts": [context], "nodes": nodes},
            "selectors": {"nodes": ["*"], "contexts": ["*"], "instances": ["*"], "dimensions": dimensions or ["*"], "labels": ["*"], "alerts": ["*"]},
            "aggregations": {"metrics": [{"group_by": ["node"], "aggregation": "sum"}], "time": {"time_group": "average"}},
            "window": {"after": -60, "before": 0, "points": 1},
            "timeout": 20000,
        }
        url = f"{self.nd.base_url.rstrip('/')}/api/v3/spaces/{self.nd.space_id}/rooms/{self.nd.room_id}/data"
        r = await self.http.post(url, json=body, headers={"Authorization": f"Bearer {self.nd.token}", "Accept": "application/json"}, timeout=25.0)
        if r.status_code != 200:
            raise NetdataError(f"Netdata Cloud HTTP {r.status_code} for {context}")
        return r.json()

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
        url = f"{self.nd.base_url.rstrip('/')}/api/v3/spaces/{self.nd.space_id}/rooms/{self.nd.room_id}/nodes"
        r = await self.http.post(url, json={}, headers={"Authorization": f"Bearer {self.nd.token}", "Accept": "application/json"}, timeout=20.0)
        if r.status_code != 200:
            raise NetdataError(f"Netdata Cloud HTTP {r.status_code} listing nodes")
        data = r.json()
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
            cpu = parse_json2_by_node(await self._cloud_query(self.nd.cpu_context, None), servers)
            net = parse_json2_by_node(await self._cloud_query(self.nd.net_context, [self.nd.net_out_dimension]), servers)
            for name in out:
                if name in cpu:
                    out[name]["cpu_pct"] = round(min(cpu[name], 100.0), 1)
                if name in net:
                    out[name]["net_out_mbps"] = round(abs(net[name]) / self.nd.net_units_per_mbps, 1)
        elif self.nd.mode == "agent":
            for s in servers:
                c = await self._agent_value(s, self.nd.cpu_context, None)
                n = await self._agent_value(s, self.nd.net_context, [self.nd.net_out_dimension])
                if c is not None:
                    out[s.name]["cpu_pct"] = round(min(c, 100.0), 1)
                if n is not None:
                    out[s.name]["net_out_mbps"] = round(n / self.nd.net_units_per_mbps, 1)
        return out
