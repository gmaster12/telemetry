"""Sample data, shown until the first Plex server is added (SAMPLE_MODE=auto)."""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

HERE = Path(__file__).parent / "sample"


def _load(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def streams() -> list[dict]:
    return _load("streams.json")


def servers() -> list[dict]:
    rows = _load("servers.json")
    for r in rows:
        r.setdefault("cpu_known", True)
    return rows


def viewing() -> list[dict]:
    return _load("viewing.json")


def bandwidth_daily(tz_offset_min: int = 0) -> list[dict]:
    """Seven plausible daily peaks ending today; today's is the sample last hour's peak."""
    off = tz_offset_min * 60
    today = (int(time.time()) + off) // 86400
    rnd = random.Random(today)
    now_peak = max(r["mbps"] for r in bandwidth())
    out = []
    for i, d in enumerate(range(today - 6, today + 1)):
        is_today = d == today
        v = now_peak if is_today else now_peak * rnd.uniform(1.05, 1.75)
        minute = rnd.randint(19 * 60, 22 * 60 + 45)  # evenings, viewer's time
        ts = min(d * 86400 + minute * 60 - off, int(time.time()) - 60) if not is_today else int(time.time()) - 600
        out.append({"day": time.strftime("%Y-%m-%d", time.gmtime(d * 86400)), "today": is_today,
                    "peak_mbps": round(v, 1), "peak_at": time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(ts))})
    return out


def bandwidth() -> list[dict]:
    """A plausible last hour ending now, finishing at the sample streams' current total."""
    final = round(sum(r["bitrate_mbps"] for r in streams() if r["state"] != "Paused"), 1)
    rnd = random.Random(int(time.time()) // 3600)
    now = int(time.time()) // 60 * 60
    out = []
    for i in range(60):
        v = final * (0.55 + 0.45 * i / 59) + 9 * math.sin(i / 5) + rnd.uniform(-5, 5)
        out.append({"minute": time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(now - (59 - i) * 60)), "mbps": round(max(8, v if i < 59 else final), 1)})
    return out
