"""Runtime configuration.

Plex servers and the Netdata connection are managed from the dashboard's
Settings menu and stored in SQLite (tokens encrypted). Only process-level
settings come from the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ServerCfg:
    id: int
    name: str
    plex_url: str
    token: str | None = None
    max_streams: int = 8
    capacity_mbps: float = 1000.0
    netdata_node: str | None = None  # Netdata Cloud node id (or hostname)
    netdata_agent_url: str | None = None  # only used when Netdata mode is "agent"
    enabled: bool = True


@dataclass
class NetdataCfg:
    mode: str = "off"  # "cloud", "agent" or "off"
    base_url: str = "https://app.netdata.cloud"
    token: str | None = None
    space_id: str | None = None
    room_id: str | None = None
    cpu_context: str = "system.cpu"
    net_context: str = "system.net"
    net_out_dimension: str = "sent"
    net_units_per_mbps: float = 1000.0  # system.net reports kilobits/s


@dataclass
class AppCfg:
    servers: list[ServerCfg] = field(default_factory=list)
    netdata: NetdataCfg = field(default_factory=NetdataCfg)
    poll_seconds: int = 10
    telemetry_seconds: int = 30
    history_days: int = 8
    hot_cpu_pct: float = 85.0
    sample_mode: str = "auto"  # "auto": sample data until a Plex server is added; "always"; "never"

    @property
    def use_sample(self) -> bool:
        if self.sample_mode == "always":
            return True
        if self.sample_mode == "never":
            return False
        return not [s for s in self.servers if s.enabled]


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


DB_PATH = env("DB_PATH", "/data/telemetry.db")
WEB_DIR = env("WEB_DIR", "/app/web")
