"""Stream card clips: made once per title from 10% in, queued sensibly, cleaned up, token kept out of the URL."""
import asyncio
import http.server
import os
import shutil
import subprocess
import threading
import time

import pytest

from app.clips import CLIP_SECONDS, MAX_BYTES, Clips

FFMPEG = shutil.which("ffmpeg")


def test_command_starts_ten_percent_in_and_keeps_the_token_out_of_the_url(tmp_path):
    c = Clips(tmp_path, ffmpeg="ffmpeg")
    cmd = c.command("http://plex:32400/library/parts/9/1/file.mkv", "secret-token", 2400.0, tmp_path / "x.part")
    assert cmd[cmd.index("-ss") + 1] == "240.00"            # 10% of 40 minutes
    assert cmd[cmd.index("-t") + 1] == str(CLIP_SECONDS)
    assert "-an" in cmd                                      # silent
    assert all("secret-token" not in a for a in cmd if a.startswith("http"))
    assert any(a.startswith("X-Plex-Token: secret-token") for a in cmd)
    short = c.command("http://x/library/parts/1/1/a.mkv", "t", 12.0, tmp_path / "y.part")
    assert short[short.index("-ss") + 1] == "0.00"           # too short to skip ahead


def test_want_queues_each_title_once_and_waits_after_a_failure(tmp_path):
    async def go():
        c = Clips(tmp_path, ffmpeg="ffmpeg")
        assert c.want(1, "123") is True
        assert c.want(1, "123") is False                     # already queued
        c.pending.clear(); c.failed[c.key(1, "123")] = time.time()
        assert c.want(1, "123") is False                     # failed recently: leave it for a while
        c.path(2, "77").write_bytes(b"x" * 3000)
        assert c.want(2, "77") is False                      # already made
        assert Clips(tmp_path / "n", ffmpeg="").want(1, "5") is False   # no ffmpeg: nothing queued
    asyncio.run(go())


def test_keys_are_safe_file_names(tmp_path):
    c = Clips(tmp_path, ffmpeg="ffmpeg")
    assert c.path(3, "../../etc/passwd").name == "3_etcpasswd.mp4"
    assert c.url(3, "12a/../b") == "/api/clip/3/12ab"


def test_prune_drops_old_clips_and_keeps_under_the_cap(tmp_path, monkeypatch):
    import app.clips as C
    c = Clips(tmp_path, ffmpeg="ffmpeg")
    now = time.time()
    old = c.path(1, "1"); old.write_bytes(b"x" * 10); os.utime(old, (now - 20 * 86400,) * 2)
    leftover = tmp_path / "1_2.part"; leftover.write_bytes(b"x"); os.utime(leftover, (now - 3600,) * 2)
    monkeypatch.setattr(C, "MAX_BYTES", 250)
    for i, age in enumerate([300, 200, 100]):
        p = c.path(2, str(i)); p.write_bytes(b"x" * 100); os.utime(p, (now - age,) * 2)
    removed = c.prune(now)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert removed == 3 and names == ["2_1.mp4", "2_2.mp4"]   # 20-day-old, leftover, then the oldest over the cap
    assert MAX_BYTES > 100 * 1024 * 1024


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_makes_a_small_silent_clip_from_a_file_served_over_http(tmp_path):
    media = tmp_path / "media"; media.mkdir()
    src = media / "show.mp4"
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=40",
                    "-f", "lavfi", "-i", "sine=duration=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-y", str(src)], check=True)
    seen = []

    data = src.read_bytes()

    class H(http.server.BaseHTTPRequestHandler):
        # Plex serves files with byte ranges, which ffmpeg needs to jump 10% in; so does this stand-in.
        def do_GET(self):
            seen.append(self.headers.get("X-Plex-Token"))
            rng = self.headers.get("Range")
            start, end = 0, len(data) - 1
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a or 0); end = int(b) if b else end
            body = data[start:end + 1]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", "video/mp4"); self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Clips(tmp_path / "clips")
        out = c.path(1, "123")
        asyncio.run(c.make(f"http://127.0.0.1:{srv.server_address[1]}/show.mp4", "tok", 40.0, out))
    finally:
        srv.shutdown()
    assert out.exists() and 2000 < out.stat().st_size < 1_000_000
    assert "tok" in seen                                     # sent as a header
    probe = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert probe == ["video,240"]                            # one video stream, 240 px tall, no audio
    assert not list((tmp_path / "clips").glob("*.part"))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_a_bad_source_is_recorded_and_the_worker_carries_on(tmp_path):
    async def go():
        c = Clips(tmp_path)
        calls = []

        async def source(sid, rk):
            calls.append(rk)
            return None if rk == "bad" else ("http://127.0.0.1:9/nothing.mkv", "t", 100.0)

        c.want(1, "bad"); c.want(1, "gone")
        task = asyncio.create_task(c.worker(source))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if not c.pending:
                break
        task.cancel()
        return c, calls
    c, calls = asyncio.run(go())
    assert calls == ["bad", "gone"] and set(c.failed) == {"1_bad", "1_gone"} and c.last_error
