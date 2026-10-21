"""SQLite: Plex servers and settings (managed from the dashboard), plus one
history row per server per minute for the bandwidth chart, viewing heatmap
and how long a server has been hot."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import AppCfg, NetdataCfg, ServerCfg, env
from .secrets import SecretBox

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  ts INTEGER NOT NULL,
  server TEXT NOT NULL,
  online INTEGER NOT NULL,
  streams INTEGER NOT NULL,
  bandwidth_mbps REAL NOT NULL,
  cpu_pct REAL,
  net_out_mbps REAL,
  PRIMARY KEY (ts, server)
);
CREATE INDEX IF NOT EXISTS samples_ts ON samples (ts);
CREATE TABLE IF NOT EXISTS servers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  plex_url TEXT NOT NULL,
  token_enc TEXT,
  max_streams INTEGER NOT NULL DEFAULT 8,
  capacity_mbps REAL NOT NULL DEFAULT 1000,
  netdata_node TEXT,
  netdata_agent_url TEXT,
  machine_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

SECRET_SETTINGS = {"netdata_token", "plex_account_token"}
SERVER_FIELDS = ("name", "plex_url", "max_streams", "capacity_mbps", "netdata_node", "netdata_agent_url", "machine_id", "enabled")


class Store:
    def __init__(self, path: str, box: SecretBox):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.box = box
        self.lock = threading.Lock()

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if not row or row["value"] in (None, ""):
            return default
        return self.box.open(row["value"]) if key in SECRET_SETTINGS else row["value"]

    def set_setting(self, key: str, value: str | None) -> None:
        stored = self.box.seal(value) if key in SECRET_SETTINGS else value
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, stored))
            self.db.commit()

    # ---------- servers ----------
    def list_servers(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM servers ORDER BY id")]

    def get_server(self, server_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        return dict(r) if r else None

    def add_server(self, data: dict, token: str | None) -> int:
        cols = [f for f in SERVER_FIELDS if f in data]
        with self.lock:
            cur = self.db.execute(
                f"INSERT INTO servers ({', '.join(cols)}, token_enc, created) VALUES ({', '.join('?' * len(cols))}, ?, ?)",
                [data[c] for c in cols] + [self.box.seal(token), int(time.time())],
            )
            self.db.commit()
            return int(cur.lastrowid)

    def update_server(self, server_id: int, data: dict, token: str | None = None) -> None:
        cols = [f for f in SERVER_FIELDS if f in data]
        sets = [f"{c} = ?" for c in cols]
        vals = [data[c] for c in cols]
        if token:
            sets.append("token_enc = ?")
            vals.append(self.box.seal(token))
        if not sets:
            return
        with self.lock:
            self.db.execute(f"UPDATE servers SET {', '.join(sets)} WHERE id = ?", vals + [server_id])
            self.db.commit()

    def delete_server(self, server_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            self.db.commit()

    # ---------- runtime config ----------
    def app_config(self) -> AppCfg:
        servers = [
            ServerCfg(
                id=r["id"], name=r["name"], plex_url=r["plex_url"], token=self.box.open(r["token_enc"]),
                max_streams=r["max_streams"], capacity_mbps=r["capacity_mbps"], netdata_node=r["netdata_node"],
                netdata_agent_url=r["netdata_agent_url"], enabled=bool(r["enabled"]),
            )
            for r in self.list_servers()
        ]
        nd = NetdataCfg(
            mode=self.get_setting("netdata_mode", env("NETDATA_MODE", "off")),
            token=self.get_setting("netdata_token", env("NETDATA_TOKEN")),
            space_id=self.get_setting("netdata_space_id", env("NETDATA_SPACE_ID")),
            room_id=self.get_setting("netdata_room_id", env("NETDATA_ROOM_ID")),
            cpu_context=self.get_setting("netdata_cpu_context", "system.cpu"),
            net_context=self.get_setting("netdata_net_context", "system.net"),
            net_out_dimension=self.get_setting("netdata_net_out_dimension", "sent"),
        )
        return AppCfg(
            servers=servers, netdata=nd,
            hot_cpu_pct=float(self.get_setting("hot_cpu_pct", "85")),
            sample_mode=env("SAMPLE_MODE", "auto"),
        )

    # ---------- history ----------
    def write_minute(self, rows: list[dict], ts: int | None = None) -> None:
        ts = ts or int(time.time()) // 60 * 60
        with self.lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?)",
                [(ts, r["server"], int(r["online"]), r["streams"], r["bandwidth_mbps"], r.get("cpu_pct"), r.get("net_out_mbps")) for r in rows],
            )
            self.db.commit()

    def prune(self, days: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE ts < ?", (int(time.time()) - days * 86400,))
            self.db.commit()

    def bandwidth_last_hour(self, servers: list[str] | None = None) -> list[dict]:
        since = int(time.time()) - 3600
        q = "SELECT ts, SUM(bandwidth_mbps) AS v FROM samples WHERE ts >= ?"
        args: list = [since]
        if servers:
            q += f" AND server IN ({','.join('?' * len(servers))})"
            args += servers
        cur = self.db.execute(q + " GROUP BY ts ORDER BY ts", args)
        return [{"minute": time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(r["ts"])), "mbps": round(r["v"] or 0, 1)} for r in cur]

    def viewing_week(self, tz_offset_min: int = 0) -> list[dict]:
        """Peak concurrent streams per weekday and hour over the last 7 days (in the viewer's time zone)."""
        since = int(time.time()) - 7 * 86400
        cur = self.db.execute("SELECT ts, SUM(streams) AS n FROM samples WHERE ts >= ? GROUP BY ts", (since,))
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        grid = {(d, h): 0 for d in days for h in range(24)}
        for r in cur:
            t = time.gmtime(r["ts"] + tz_offset_min * 60)
            key = (days[t.tm_wday], t.tm_hour)
            grid[key] = max(grid[key], int(r["n"] or 0))
        return [{"day": d, "hour": h, "streams": grid[(d, h)]} for d in days for h in range(24)]

    def hot_minutes(self, server: str, threshold: float) -> int:
        """Consecutive most-recent minutes at or above the CPU threshold."""
        n = 0
        for r in self.db.execute("SELECT cpu_pct FROM samples WHERE server = ? ORDER BY ts DESC LIMIT 240", (server,)):
            if r["cpu_pct"] is None or r["cpu_pct"] < threshold:
                break
            n += 1
        return n

    def history_minutes(self) -> int:
        r = self.db.execute("SELECT COUNT(DISTINCT ts) AS n FROM samples WHERE ts >= ?", (int(time.time()) - 7 * 86400,)).fetchone()
        return int(r["n"] or 0)
