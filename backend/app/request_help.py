"""Glenn's request rules for movies and shows (the media site has the same ones for books, server/request_help.ts).

- A failed request can be tried again once by the person who made it; after that they get Notify admin, which puts it
  on the admins' list (Settings, Request log, "Needs you", and the Settings home's "needs a look") until handled.
  Request managers are not limited.
- Notify me too: someone can follow a request another person made. It then shows in their My requests as
  "Requested by another member"; the other person's name is never sent to them.
- Searches that found nothing are counted by search words only (never who searched) for the admins.
"""
from __future__ import annotations

import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS request_flags (
  request_id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  kind TEXT,
  tmdb_id INTEGER,
  title TEXT,
  retried_at INTEGER,
  notified_at INTEGER,
  handled_at INTEGER
);
CREATE TABLE IF NOT EXISTS request_watch (
  request_id INTEGER NOT NULL,
  subject TEXT NOT NULL,
  kind TEXT,
  tmdb_id INTEGER,
  title TEXT,
  created INTEGER NOT NULL,
  PRIMARY KEY (request_id, subject)
);
CREATE TABLE IF NOT EXISTS search_misses (
  day TEXT NOT NULL,
  term TEXT NOT NULL,
  kind TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  last_at INTEGER NOT NULL,
  PRIMARY KEY (day, term, kind)
);
"""


class RequestHelp:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.db.executescript(SCHEMA)
            store.db.commit()

    def _run(self, sql: str, *args) -> None:
        with self.store.lock:
            self.store.db.execute(sql, args)
            self.store.db.commit()

    # ---------- one retry, then Notify admin ----------
    def flags(self, ids) -> dict[int, dict]:
        ids = [int(i) for i in ids if i is not None]
        if not ids:
            return {}
        rows = self.store.db.execute(f"SELECT * FROM request_flags WHERE request_id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return {int(r["request_id"]): dict(r) for r in rows}

    def flag(self, request_id: int) -> dict | None:
        return self.flags([request_id]).get(int(request_id))

    def _upsert(self, request_id: int, subject: str, info: dict, field: str) -> None:
        now = int(time.time())
        clear = ", handled_at = NULL" if field == "notified_at" else ""
        self._run(f"INSERT INTO request_flags (request_id, subject, kind, tmdb_id, title, {field}) VALUES (?, ?, ?, ?, ?, ?) "
                  f"ON CONFLICT(request_id) DO UPDATE SET {field} = excluded.{field}{clear}, "
                  "title = COALESCE(excluded.title, request_flags.title)",
                  int(request_id), str(subject), info.get("kind"), info.get("tmdbId"), info.get("title"), now)

    def mark_retried(self, request_id: int, subject: str, info: dict) -> None:
        self._upsert(request_id, subject, info, "retried_at")

    def mark_notified(self, request_id: int, subject: str, info: dict) -> None:
        self._upsert(request_id, subject, info, "notified_at")

    def mark_handled(self, request_id: int) -> None:
        self._run("UPDATE request_flags SET handled_at = ? WHERE request_id = ?", int(time.time()), int(request_id))

    def needs_you(self) -> list[dict]:
        return [dict(r) for r in self.store.db.execute(
            "SELECT * FROM request_flags WHERE notified_at IS NOT NULL AND handled_at IS NULL ORDER BY notified_at DESC")]

    def needs_count(self) -> int:
        return self.store.db.execute("SELECT COUNT(*) FROM request_flags WHERE notified_at IS NOT NULL AND handled_at IS NULL").fetchone()[0]

    # ---------- Notify me too ----------
    def watch(self, request_id: int, subject: str, info: dict) -> None:
        self._run("INSERT OR IGNORE INTO request_watch (request_id, subject, kind, tmdb_id, title, created) VALUES (?, ?, ?, ?, ?, ?)",
                  int(request_id), str(subject), info.get("kind"), info.get("tmdbId"), info.get("title"), int(time.time()))

    def unwatch(self, request_id: int, subject: str) -> None:
        self._run("DELETE FROM request_watch WHERE request_id = ? AND subject = ?", int(request_id), str(subject))

    def watched(self, subject: str) -> list[dict]:
        return [dict(r) for r in self.store.db.execute("SELECT * FROM request_watch WHERE subject = ? ORDER BY created DESC", (str(subject),))]

    # ---------- searches that found nothing ----------
    def miss(self, term: str, kind: str = "video", now: float | None = None) -> None:
        term = " ".join(term.lower().split())[:120]
        if len(term) < 2:
            return
        now = int(now or time.time())
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        self._run("INSERT INTO search_misses (day, term, kind, count, last_at) VALUES (?, ?, ?, 1, ?) "
                  "ON CONFLICT(day, term, kind) DO UPDATE SET count = count + 1, last_at = excluded.last_at", day, term, kind, now)

    def misses(self, days: int = 7, now: float | None = None) -> list[dict]:
        since = time.strftime("%Y-%m-%d", time.gmtime((now or time.time()) - days * 86400))
        return [dict(r) for r in self.store.db.execute(
            "SELECT term, kind, SUM(count) AS count, MAX(last_at) AS last_at FROM search_misses WHERE day >= ? "
            "GROUP BY term, kind ORDER BY count DESC, last_at DESC LIMIT 50", (since,))]
