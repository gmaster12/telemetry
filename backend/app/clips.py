"""Short silent looping clips for the stream cards, cut from each title's own file on its Plex server.

The first time a show or movie plays, ffmpeg (installed in this container) reads about 8 seconds starting
10% into the file, straight from the Plex server that has it, and saves a small 240p MP4 in the data
folder. Every viewer reuses that file, so each title is cut once. Only one clip is made at a time, so
neither this host nor the Plex servers get busy. A title that fails is not retried for 6 hours. Clips
nobody has watched for 14 days are deleted, and the folder is kept under a size cap, oldest first.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("telemetry.clips")

CLIP_SECONDS = 8
START_AT = 0.10            # 10% into the show or movie (Glenn's choice): no spoilers, one clip per title
RETRY_AFTER = 6 * 3600     # a title that failed is left alone this long
KEEP_DAYS = 14             # clips nobody has played for this long are deleted
MAX_BYTES = 500 * 1024 * 1024
TIMEOUT = 90               # seconds for one ffmpeg run
VIDEO_KINDS = ("Episode", "Movie")


class ClipError(Exception):
    pass


class Clips:
    def __init__(self, folder: str | Path, ffmpeg: str | None = None):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = ffmpeg if ffmpeg is not None else shutil.which("ffmpeg")
        self.queue: asyncio.Queue | None = None
        self.pending: set[str] = set()
        self.failed: dict[str, float] = {}
        self.last_error: str | None = None
        self._pruned_at = 0.0

    # ---------- names ----------
    @staticmethod
    def key(server_id: int, rating_key: str) -> str:
        return f"{int(server_id)}_{re.sub(r'[^A-Za-z0-9_-]', '', str(rating_key))[:40]}"

    def path(self, server_id: int, rating_key: str) -> Path:
        return self.dir / (self.key(server_id, rating_key) + ".mp4")

    def url(self, server_id: int, rating_key: str) -> str:
        return f"/api/clip/{int(server_id)}/{re.sub(r'[^A-Za-z0-9_-]', '', str(rating_key))}"

    def ready(self, server_id: int, rating_key: str) -> bool:
        return self.path(server_id, rating_key).exists()

    # ---------- asking for clips ----------
    def want(self, server_id: int, rating_key: str) -> bool:
        """Queue a clip for this title unless it exists, is queued, or failed recently. True when queued."""
        if not self.ffmpeg or not rating_key:
            return False
        k = self.key(server_id, rating_key)
        if k in self.pending or self.ready(server_id, rating_key):
            return False
        if time.time() - self.failed.get(k, 0) < RETRY_AFTER:
            return False
        if self.queue is None:
            self.queue = asyncio.Queue()
        self.pending.add(k)
        self.queue.put_nowait((int(server_id), str(rating_key)))
        return True

    def touch(self, p: Path) -> None:
        try:
            os.utime(p, None)  # "last played", for the 14-day clean-up
        except OSError:
            pass

    # ---------- making clips ----------
    async def worker(self, source: Callable[[int, str], Awaitable[tuple[str, str, float] | None]]) -> None:
        """Make queued clips one at a time. source(server_id, rating_key) gives (file url, Plex token, duration seconds)."""
        if self.queue is None:
            self.queue = asyncio.Queue()
        while True:
            server_id, rating_key = await self.queue.get()
            k = self.key(server_id, rating_key)
            try:
                src = await source(server_id, rating_key)
                if not src:
                    raise ClipError("no file found for this title")
                await self.make(*src, self.path(server_id, rating_key))
                self.failed.pop(k, None)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # one bad title must not stop the others
                self.failed[k] = time.time()
                self.last_error = str(e)[:300]
                log.warning("Clip for %s failed: %s", k, self.last_error)
            finally:
                self.pending.discard(k)
            if time.time() - self._pruned_at > 3600:
                self.prune()

    def command(self, file_url: str, token: str, duration_s: float, out: Path) -> list[str]:
        start = duration_s * START_AT if duration_s > CLIP_SECONDS * 3 else 0.0
        return [
            self.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
            # the token goes in a request header, never in the URL, so it cannot end up in an error message
            "-headers", f"X-Plex-Token: {token}\r\n",
            "-ss", f"{start:.2f}", "-i", file_url, "-t", str(CLIP_SECONDS),
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", "scale=-2:240:flags=bicubic,fps=24,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-profile:v", "main",
            "-movflags", "+faststart", "-f", "mp4", "-y", str(out),
        ]

    async def make(self, file_url: str, token: str, duration_s: float, out: Path) -> None:
        tmp = out.with_name(out.stem + ".part")
        proc = await asyncio.create_subprocess_exec(*self.command(file_url, token, duration_s, tmp),
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            tmp.unlink(missing_ok=True)
            raise ClipError(f"ffmpeg took longer than {TIMEOUT} s")
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 2000:
            tmp.unlink(missing_ok=True)
            msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
            raise ClipError(msg[-1][:200] if msg else f"ffmpeg exited with {proc.returncode}")
        tmp.replace(out)

    # ---------- clean-up ----------
    def prune(self, now: float | None = None) -> int:
        """Delete clips unplayed for KEEP_DAYS and leftovers, then the oldest until under MAX_BYTES. Returns files removed."""
        now = now or time.time()
        self._pruned_at = now
        removed = 0
        files = []
        for p in self.dir.iterdir():
            try:
                st = p.stat()
            except OSError:
                continue
            if p.suffix == ".part" and now - st.st_mtime > 600:
                p.unlink(missing_ok=True); removed += 1
            elif p.suffix == ".mp4":
                if now - st.st_mtime > KEEP_DAYS * 86400:
                    p.unlink(missing_ok=True); removed += 1
                else:
                    files.append((st.st_mtime, st.st_size, p))
        total = sum(f[1] for f in files)
        for _, size, p in sorted(files):
            if total <= MAX_BYTES:
                break
            p.unlink(missing_ok=True); removed += 1
            total -= size
        return removed

    def status(self) -> dict:
        n = sum(1 for p in self.dir.glob("*.mp4"))
        return {"available": bool(self.ffmpeg), "clips": n, "queued": len(self.pending), "error": self.last_error}
