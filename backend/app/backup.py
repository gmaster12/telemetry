"""Nightly copies of the database (Glenn's pick K2A, Nov 15 2026).

`data/telemetry.db` holds watch history, the request log, notifications, access settings and the encrypted tokens,
and nothing else copied it anywhere. Each night at `BACKUP_HOUR` (4 AM, after Unwatched cleanup's 3 AM run) in the
same zone as cleanup (`cleanup_tz`, America/Chicago) this makes a safe copy with SQLite's own backup, checks it, and
keeps it gzipped as `data/backups/telemetry-YYYY-MM-DD-HHMM.db.gz` (readable by this container's user only). The last
`KEEP` (14) are kept. A copy also runs at start when the newest is more than 26 hours old (the server was off at 4 AM).

To restore: stop the container, `gunzip -c data/backups/<file> > data/telemetry.db`, start it. The copy's tokens are
encrypted with SECRET_KEY, so keep `.env` safe on its own; without the same key they cannot be read.

The copies sit on the same disk as the database: they survive a bad change or a damaged file, not a lost server.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("telemetry.backup")

KEEP = 14
BACKUP_HOUR = 4
STALE_HOURS = 26     # a copy older than this at start, or when checked, is overdue
NAME = re.compile(r"^telemetry-(\d{4}-\d{2}-\d{2}-\d{4})\.db\.gz$")


class Backups:
    def __init__(self, store, folder: str | Path | None = None):
        self.store = store
        self.folder = Path(folder or os.environ.get("BACKUP_DIR") or Path(store.path).parent / "backups")
        self.running = False

    # ---------- what there is ----------
    def files(self) -> list[dict]:
        """The copies, newest first: {name, at, size}."""
        if not self.folder.is_dir():
            return []
        out = []
        for p in self.folder.iterdir():
            if NAME.match(p.name):
                st = p.stat()
                out.append({"name": p.name, "at": int(st.st_mtime), "size": st.st_size})
        return sorted(out, key=lambda f: (f["at"], f["name"]), reverse=True)

    def last(self) -> dict:
        """The last run, from settings: {at, name, size, error} (at: when it ran, ok or not)."""
        try:
            return json.loads(self.store.get_setting("backup_last") or "{}")
        except ValueError:
            return {}

    def status(self, now: float | None = None) -> dict:
        now = now or time.time()
        files, last = self.files(), self.last()
        newest = files[0]["at"] if files else None
        return {"folder": str(self.folder), "count": len(files), "keep": KEEP, "hour": BACKUP_HOUR,
                "newest": newest, "size": sum(f["size"] for f in files), "files": files[:KEEP],
                "error": last.get("error"), "error_at": last.get("at") if last.get("error") else None,
                "overdue": newest is None or now - newest > STALE_HOURS * 3600, "running": self.running}

    # ---------- making one ----------
    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.get_setting("cleanup_tz") or "America/Chicago")
        except Exception:  # noqa: BLE001  (an unknown zone name falls back to Glenn's)
            return ZoneInfo("America/Chicago")

    def run(self) -> dict:
        """Make one copy now (blocking: call it in a thread). Returns {name, size} or raises."""
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.folder, 0o700)
        except OSError:
            pass
        stamp = datetime.now(self._zone()).strftime("%Y-%m-%d-%H%M")
        part = self.folder / f".telemetry-{stamp}.db.part"
        final = self.folder / f"telemetry-{stamp}.db.gz"
        try:
            dst = sqlite3.connect(part)
            try:
                # The app's own writes wait for the copy (a second or two), so it is one consistent moment.
                with self.store.lock:
                    src = sqlite3.connect(self.store.path)
                    try:
                        src.backup(dst)
                    finally:
                        src.close()
                ok = dst.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                dst.close()
            if ok != "ok":
                raise RuntimeError(f"The copy did not pass SQLite's check: {ok}")
            with open(part, "rb") as fin, gzip.open(str(final) + ".part", "wb", compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout, 1024 * 1024)
            os.replace(str(final) + ".part", final)
            os.chmod(final, 0o600)
        finally:
            for p in (part, Path(str(final) + ".part")):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
        self.prune()
        size = final.stat().st_size
        self.store.set_setting("backup_last", json.dumps({"at": int(time.time()), "name": final.name, "size": size, "error": None}))
        log.info("Database copied to %s (%d bytes)", final.name, size)
        return {"name": final.name, "size": size}

    def prune(self) -> int:
        gone = 0
        for f in self.files()[KEEP:]:
            try:
                (self.folder / f["name"]).unlink()
                gone += 1
            except OSError:
                pass
        return gone

    async def run_now(self) -> dict:
        if self.running:
            raise RuntimeError("A copy is being made right now.")
        self.running = True
        try:
            return await asyncio.to_thread(self.run)
        except Exception as e:
            self.store.set_setting("backup_last", json.dumps({"at": int(time.time()), "error": str(e)}))
            log.warning("Database copy failed: %s", e)
            raise
        finally:
            self.running = False

    def due(self, now: float | None = None) -> bool:
        """At the nightly hour once a day, or whenever the newest copy is overdue."""
        now = now or time.time()
        st = self.status(now)
        if st["overdue"]:
            return True
        here = datetime.fromtimestamp(now, self._zone())
        newest = datetime.fromtimestamp(st["newest"], self._zone())
        return here.hour == BACKUP_HOUR and newest.date() != here.date()

    async def loop(self) -> None:
        await asyncio.sleep(30)   # let the app settle after a start
        while True:
            try:
                last = self.last()
                failed_lately = last.get("error") and time.time() - int(last.get("at") or 0) < 3600
                if self.due() and not failed_lately:
                    await self.run_now()
            except Exception:  # noqa: BLE001  (recorded in backup_last; tried again within the hour)
                pass
            await asyncio.sleep(600)
