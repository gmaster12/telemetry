"""SQLite: Plex servers and settings (managed from the dashboard), plus one
history row per server per minute for the bandwidth chart, viewing heatmap
and how long a server has been hot."""
from __future__ import annotations

import json
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
  created INTEGER NOT NULL,
  owner_plex_id TEXT
);
CREATE TABLE IF NOT EXISTS plex_accounts (
  plex_id TEXT PRIMARY KEY,
  username TEXT,
  title TEXT,
  email TEXT,
  token_enc TEXT NOT NULL,
  created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS users (
  plex_id TEXT PRIMARY KEY,
  username TEXT,
  title TEXT,
  email TEXT,
  thumb TEXT,
  shared INTEGER NOT NULL DEFAULT 0,
  is_owner INTEGER NOT NULL DEFAULT 0,
  sections TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  first_seen INTEGER,
  last_seen INTEGER,
  last_login INTEGER,
  servers TEXT
);
CREATE INDEX IF NOT EXISTS users_username ON users (username COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS plays (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server TEXT NOT NULL,
  session_key TEXT,
  user_id TEXT,
  username TEXT,
  rating_key TEXT,
  art TEXT,
  title TEXT,
  subtitle TEXT,
  kind TEXT,
  device TEXT,
  player TEXT,
  platform TEXT,
  location TEXT,
  method TEXT,
  resolution TEXT,
  started_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  stopped_at INTEGER,
  watched_sec INTEGER NOT NULL DEFAULT 0,
  progress_pct INTEGER,
  duration_min INTEGER,
  source TEXT NOT NULL DEFAULT 'live',
  UNIQUE (server, session_key, rating_key, started_at)
);
CREATE INDEX IF NOT EXISTS plays_user ON plays (user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS plays_open ON plays (stopped_at);
CREATE TABLE IF NOT EXISTS local_admins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  created INTEGER NOT NULL,
  last_login INTEGER
);
CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  created INTEGER NOT NULL,
  expires INTEGER NOT NULL
);
"""

SECRET_SETTINGS = {"netdata_token", "plex_account_token"}
SERVER_FIELDS = ("name", "plex_url", "max_streams", "capacity_mbps", "netdata_node", "netdata_agent_url", "machine_id", "enabled", "owner_plex_id")


class Store:
    def __init__(self, path: str, box: SecretBox):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(users)")}
        if "servers" not in cols:  # added after the first users release
            self.db.execute("ALTER TABLE users ADD COLUMN servers TEXT")
            self.db.commit()
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(servers)")}
        if "owner_plex_id" not in cols:  # one Plex account per server
            self.db.execute("ALTER TABLE servers ADD COLUMN owner_plex_id TEXT")
            self.db.commit()
        self.box = box
        self.lock = threading.Lock()

    # ---------- linked Plex accounts (each owns one or more servers) ----------
    def list_accounts(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT plex_id, username, title, email, created FROM plex_accounts ORDER BY created")]

    def account_tokens(self) -> list[tuple[dict, str]]:
        out = []
        for r in self.db.execute("SELECT * FROM plex_accounts ORDER BY created"):
            tok = self.box.open(r["token_enc"])
            if tok:
                out.append(({k: r[k] for k in ("plex_id", "username", "title", "email")}, tok))
        return out

    def save_account(self, info: dict, token: str) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO plex_accounts (plex_id, username, title, email, token_enc, created) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(plex_id) DO UPDATE SET username = excluded.username, title = excluded.title, email = excluded.email, token_enc = excluded.token_enc",
                (str(info["id"]), info.get("username"), info.get("title"), info.get("email"), self.box.seal(token), int(time.time())),
            )
            self.db.commit()

    def update_account_info(self, info: dict) -> None:
        with self.lock:
            self.db.execute("UPDATE plex_accounts SET username = ?, title = ?, email = ? WHERE plex_id = ?",
                            (info.get("username"), info.get("title"), info.get("email"), str(info["id"])))
            self.db.commit()

    def delete_account(self, plex_id: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM plex_accounts WHERE plex_id = ?", (plex_id,))
            self.db.commit()

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


class Directory:
    """Plex users, playback history, local admin logins and login sessions."""

    def __init__(self, store: Store):
        self.s = store
        self.db = store.db
        self.lock = store.lock

    # ---------- users ----------
    def upsert_user(self, u: dict, *, shared: bool | None = None, is_owner: bool | None = None, seen: bool = False) -> None:
        if not u.get("id"):
            return
        now = int(time.time())
        with self.lock:
            row = self.db.execute("SELECT plex_id FROM users WHERE plex_id = ?", (u["id"],)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO users (plex_id, username, title, email, thumb, shared, is_owner, first_seen, last_seen, servers) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (u["id"], u.get("username"), u.get("title"), u.get("email"), u.get("thumb"), int(bool(shared)), int(bool(is_owner)), now, now if seen else None,
                     json.dumps(sorted(set(u["servers"]))) if u.get("servers") is not None else None),
                )
            else:
                sets, vals = [], []
                for k in ("username", "title", "email", "thumb"):
                    if u.get(k):
                        sets.append(f"{k} = ?")
                        vals.append(u[k])
                if shared is not None:
                    sets.append("shared = ?"); vals.append(int(shared))
                if is_owner is not None:
                    sets.append("is_owner = ?"); vals.append(int(is_owner))
                if seen:
                    sets.append("last_seen = ?"); vals.append(now)
                if u.get("servers") is not None:
                    sets.append("servers = ?"); vals.append(json.dumps(sorted(set(u["servers"]))))
                if sets:
                    self.db.execute(f"UPDATE users SET {', '.join(sets)} WHERE plex_id = ?", vals + [u["id"]])
            self.db.commit()

    def saw_user(self, plex_id: str, name: str | None, thumb: str | None) -> None:
        """Seen streaming: add the user if new, otherwise only fill blanks and update last_seen."""
        now = int(time.time())
        with self.lock:
            self.db.execute(
                "INSERT INTO users (plex_id, username, title, thumb, shared, is_owner, first_seen, last_seen) VALUES (?,?,?,?,0,0,?,?) "
                "ON CONFLICT(plex_id) DO UPDATE SET last_seen = excluded.last_seen, "
                "title = COALESCE(NULLIF(users.title, ''), excluded.title), username = COALESCE(NULLIF(users.username, ''), excluded.username), "
                "thumb = COALESCE(NULLIF(users.thumb, ''), excluded.thumb)",
                (plex_id, name, name, thumb, now, now),
            )
            self.db.commit()

    def add_user_server(self, plex_id: str, server: str) -> None:
        """Remember a server this person uses (keeps the ones already known)."""
        with self.lock:
            r = self.db.execute("SELECT servers FROM users WHERE plex_id = ?", (plex_id,)).fetchone()
            if r is None:
                return
            have = set(json.loads(r["servers"] or "[]"))
            if server in have:
                return
            have.add(server)
            self.db.execute("UPDATE users SET servers = ? WHERE plex_id = ?", (json.dumps(sorted(have)), plex_id))
            self.db.commit()

    def get_user(self, plex_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM users WHERE plex_id = ?", (plex_id,)).fetchone()
        return dict(r) if r else None

    def owner(self) -> dict | None:
        """The server owner, only when there is exactly one (several servers can have different owners)."""
        rs = self.db.execute("SELECT * FROM users WHERE is_owner = 1 LIMIT 2").fetchall()
        return dict(rs[0]) if len(rs) == 1 else None

    def find_user_by_email(self, email: str) -> dict | None:
        r = self.db.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email,)).fetchone()
        return dict(r) if r else None

    def set_user_access(self, plex_id: str, *, sections: list[str] | None = None, use_defaults: bool = False, disabled: bool | None = None) -> None:
        """sections: custom list for this user; use_defaults: clear the custom list."""
        import json
        with self.lock:
            if use_defaults:
                self.db.execute("UPDATE users SET sections = NULL WHERE plex_id = ?", (plex_id,))
            elif sections is not None:
                self.db.execute("UPDATE users SET sections = ? WHERE plex_id = ?", (json.dumps(sections), plex_id))
            if disabled is not None:
                self.db.execute("UPDATE users SET disabled = ? WHERE plex_id = ?", (int(disabled), plex_id))
            self.db.commit()

    def search_users(self, q: str, limit: int = 50) -> list[dict]:
        like = f"%{q.strip()}%"
        since = int(time.time()) - 30 * 86400
        cur = self.db.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM plays p WHERE p.user_id = u.plex_id) AS plays,
                      (SELECT COUNT(*) FROM plays p WHERE p.user_id = u.plex_id AND p.started_at >= ?) AS plays_30d,
                      (SELECT MAX(p.started_at) FROM plays p WHERE p.user_id = u.plex_id) AS last_play
               FROM users u
               WHERE (? = '%%' OR u.username LIKE ? OR u.title LIKE ? OR u.email LIKE ?)
               ORDER BY COALESCE(last_play, u.last_seen, 0) DESC, u.username COLLATE NOCASE
               LIMIT ?""",
            (since, like, like, like, like, limit),
        )
        return [dict(r) for r in cur]

    def user_stats(self, plex_id: str) -> dict:
        since = int(time.time()) - 30 * 86400
        r = self.db.execute(
            "SELECT COUNT(*) AS plays, COALESCE(SUM(watched_sec), 0) AS sec, MAX(started_at) AS last_play FROM plays WHERE user_id = ? AND started_at >= ?",
            (plex_id, since),
        ).fetchone()
        total = self.db.execute("SELECT COUNT(*) AS n FROM plays WHERE user_id = ?", (plex_id,)).fetchone()["n"]
        top = [dict(x) for x in self.db.execute(
            "SELECT title, COUNT(*) AS plays FROM plays WHERE user_id = ? AND started_at >= ? GROUP BY title ORDER BY plays DESC, MAX(started_at) DESC LIMIT 3",
            (plex_id, since),
        )]
        servers = [x["server"] for x in self.db.execute("SELECT server, COUNT(*) AS n FROM plays WHERE user_id = ? GROUP BY server ORDER BY n DESC", (plex_id,))]
        return {"plays_30d": r["plays"], "hours_30d": round((r["sec"] or 0) / 3600, 1), "plays_total": total,
                "last_play": r["last_play"], "top_titles": top, "servers": servers}

    def history(self, plex_id: str, before: int | None = None, limit: int = 50) -> list[dict]:
        before = before or 2**62
        cur = self.db.execute(
            "SELECT * FROM plays WHERE user_id = ? AND started_at < ? ORDER BY started_at DESC LIMIT ?",
            (plex_id, before, max(1, min(limit, 200))),
        )
        return [dict(r) for r in cur]

    # ---------- playback tracking ----------
    def open_play(self, server: str, session_key: str, rating_key: str) -> dict | None:
        r = self.db.execute(
            "SELECT * FROM plays WHERE server = ? AND session_key = ? AND rating_key = ? AND stopped_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (server, session_key, rating_key),
        ).fetchone()
        return dict(r) if r else None

    def start_play(self, row: dict, art: str | None, now: int) -> int:
        with self.lock:
            cur = self.db.execute(
                """INSERT INTO plays (server, session_key, user_id, username, rating_key, art, title, subtitle, kind, device, player,
                   platform, location, method, resolution, started_at, last_seen, progress_pct, duration_min)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["server"], row["session_key"], row.get("user_id"), row.get("user"), row.get("rating_key"), art, row.get("title"),
                 row.get("subtitle"), row.get("kind"), row.get("device"), row.get("player"), row.get("platform"), row.get("location"),
                 row.get("method"), row.get("resolution"), now, now, row.get("progress_pct"), row.get("duration_min")),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def touch_play(self, play_id: int, row: dict, now: int, add_sec: int) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE plays SET last_seen = ?, watched_sec = watched_sec + ?, progress_pct = ?, method = ?, resolution = ? WHERE id = ?",
                (now, max(0, add_sec), row.get("progress_pct"), row.get("method"), row.get("resolution"), play_id),
            )
            self.db.commit()

    def stop_play(self, play_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE plays SET stopped_at = last_seen WHERE id = ? AND stopped_at IS NULL", (play_id,))
            self.db.commit()

    def close_stale_plays(self, older_than_sec: int = 300) -> None:
        with self.lock:
            self.db.execute("UPDATE plays SET stopped_at = last_seen WHERE stopped_at IS NULL AND last_seen < ?", (int(time.time()) - older_than_sec,))
            self.db.commit()

    def import_play(self, rec: dict) -> bool:
        """Insert a play from Plex's own history; returns False if it was already imported."""
        with self.lock:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO plays (server, session_key, user_id, username, rating_key, art, title, subtitle, kind,
                   started_at, last_seen, stopped_at, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'plex')""",
                (rec["server"], f"hist-{rec['rating_key']}-{rec['account']}", rec.get("user_id"), rec.get("username"), rec["rating_key"],
                 rec.get("art"), rec.get("title"), rec.get("subtitle"), rec.get("kind"), rec["viewed_at"], rec["viewed_at"], rec["viewed_at"]),
            )
            self.db.commit()
            return cur.rowcount > 0

    def art_for(self, server: str, rating_key: str) -> str | None:
        r = self.db.execute("SELECT art FROM plays WHERE server = ? AND rating_key = ? AND art IS NOT NULL ORDER BY started_at DESC LIMIT 1", (server, rating_key)).fetchone()
        return r["art"] if r else None

    # ---------- local admins ----------
    def list_admins(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT id, username, created, last_login FROM local_admins ORDER BY username")]

    def get_admin(self, username: str) -> dict | None:
        r = self.db.execute("SELECT * FROM local_admins WHERE username = ?", (username,)).fetchone()
        return dict(r) if r else None

    def get_admin_by_id(self, admin_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM local_admins WHERE id = ?", (admin_id,)).fetchone()
        return dict(r) if r else None

    def add_admin(self, username: str, pw_hash: str) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO local_admins (username, pw_hash, created) VALUES (?,?,?)", (username, pw_hash, int(time.time())))
            self.db.commit()
            return int(cur.lastrowid)

    def set_admin_password(self, admin_id: int, pw_hash: str) -> None:
        with self.lock:
            self.db.execute("UPDATE local_admins SET pw_hash = ? WHERE id = ?", (pw_hash, admin_id))
            self.db.execute("DELETE FROM auth_sessions WHERE kind = 'local' AND subject = ?", (str(admin_id),))
            self.db.commit()

    def delete_admin(self, admin_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM local_admins WHERE id = ?", (admin_id,))
            self.db.execute("DELETE FROM auth_sessions WHERE kind = 'local' AND subject = ?", (str(admin_id),))
            self.db.commit()

    def mark_login(self, kind: str, subject: str) -> None:
        with self.lock:
            if kind == "local":
                self.db.execute("UPDATE local_admins SET last_login = ? WHERE id = ?", (int(time.time()), subject))
            else:
                self.db.execute("UPDATE users SET last_login = ? WHERE plex_id = ?", (int(time.time()), subject))
            self.db.commit()

    # ---------- login sessions ----------
    def add_session(self, token_hash: str, kind: str, subject: str, ttl: int) -> None:
        now = int(time.time())
        with self.lock:
            self.db.execute("DELETE FROM auth_sessions WHERE expires < ?", (now,))
            self.db.execute("INSERT INTO auth_sessions VALUES (?,?,?,?,?)", (token_hash, kind, subject, now, now + ttl))
            self.db.commit()

    def get_session(self, token_hash: str) -> dict | None:
        r = self.db.execute("SELECT * FROM auth_sessions WHERE token_hash = ? AND expires > ?", (token_hash, int(time.time()))).fetchone()
        return dict(r) if r else None

    def delete_session(self, token_hash: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            self.db.commit()
