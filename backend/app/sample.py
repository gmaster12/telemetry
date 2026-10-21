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
