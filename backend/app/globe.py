"""Live streams globe: where new streams start, by city, for the admin dashboard's globe card.

The poller calls `Globe.started(row)` when a stream starts that was not playing before. The viewer's IP is looked up
here, on the server, in a city-level GeoIP database and then dropped: the browser only ever gets a random id, the
time, a city label ("Dallas, TX", "Toronto, CA") and the city's own centre rounded to one decimal (about 11 km), so
the card can place its ping. Never a username, a Plex id, an IP or the viewer's own coordinates
(`tests/test_globe.py` checks this).

GeoIP: DB-IP's free "IP to City Lite" database (CC BY 4.0, attribution "IP Geolocation by DB-IP" on the card),
downloaded into the data folder and refreshed once a month. No key or account needed. Streams on the local network,
and IPs the database does not know, count toward the last-hour number but get no ping.
"""
from __future__ import annotations

import asyncio
import gzip
import ipaddress
import logging
import os
import random
import secrets
import shutil
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

log = logging.getLogger("telemetry.globe")

DBIP_URL = "https://download.db-ip.com/free/dbip-city-lite-{ym}.mmdb.gz"
REFRESH_DAYS = 32
KEEP_EVENTS = 300

US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR",
}


def public_ip(ip: str | None) -> str | None:
    """The address if it is a public one; None for LAN, loopback, CGNAT and anything that is not an IP."""
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return None
    if a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified:
        return None
    if a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"):  # carrier-grade NAT
        return None
    return str(a)


def _name(rec: dict | None) -> str:
    return str(((rec or {}).get("names") or {}).get("en") or "")


def place_from(rec: dict | None) -> dict | None:
    """A GeoIP record to {label, lat, lon}: the city (or region, or country) and its centre, rounded."""
    if not rec:
        return None
    loc = rec.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return None
    country = rec.get("country") or {}
    cc = str(country.get("iso_code") or "").upper()
    city = _name(rec.get("city"))
    subs = rec.get("subdivisions") or []
    region = _name(subs[0]) if subs else ""
    region_code = str((subs[0] or {}).get("iso_code") or "") if subs else ""
    if cc == "US":
        tail = region_code or US_STATES.get(region, "") or region or "US"
    else:
        tail = cc or _name(country)
    head = city or region or _name(country)
    if not head:
        return None
    label = f"{head}, {tail}" if tail and tail != head else head
    return {"label": label[:60], "lat": round(float(lat), 1), "lon": round(float(lon), 1)}


class GeoDB:
    """City-level IP lookups from a local .mmdb file, refreshed monthly."""

    def __init__(self, folder: Path, http: httpx.AsyncClient | None = None):
        self.folder = Path(folder)
        self.path = self.folder / "dbip-city-lite.mmdb"
        self.http = http
        self.reader = None
        self.error: str | None = None
        self.updated: float | None = None
        self._cache: dict[str, dict | None] = {}
        self._open()

    def _open(self) -> None:
        if not self.path.exists():
            return
        try:
            import maxminddb  # noqa: PLC0415
            old, self.reader = self.reader, maxminddb.open_database(str(self.path))
            if old:
                old.close()
            self.updated = self.path.stat().st_mtime
            self._cache.clear()
            self.error = None
        except Exception as e:  # a damaged file: download again next time
            self.error = f"Could not open the location database: {e}"
            log.warning(self.error)

    def status(self) -> dict:
        return {"ok": self.reader is not None, "updated": self.updated, "error": self.error,
                "source": "DB-IP City Lite", "attribution_url": "https://db-ip.com"}

    def lookup(self, ip: str | None) -> dict | None:
        ip = public_ip(ip)
        if not ip or self.reader is None:
            return None
        if ip in self._cache:
            return self._cache[ip]
        try:
            place = place_from(self.reader.get(ip))
        except Exception:
            place = None
        if len(self._cache) > 4000:
            self._cache.clear()
        self._cache[ip] = place
        return place

    def stale(self) -> bool:
        return not self.path.exists() or time.time() - self.path.stat().st_mtime > REFRESH_DAYS * 86400

    async def refresh(self) -> bool:
        """Download this month's database (or last month's, early in a month) and swap it in."""
        if self.http is None:
            return False
        self.folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        months = [now.strftime("%Y-%m")]
        prev = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        months.append(prev)
        last_err = None
        for ym in months:
            tmp = self.folder / f".dbip-{ym}.part"
            try:
                async with self.http.stream("GET", DBIP_URL.format(ym=ym), timeout=120) as r:
                    if r.status_code != 200:
                        last_err = f"HTTP {r.status_code}"
                        continue
                    with open(tmp, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)
                out = self.folder / ".dbip-city-lite.mmdb.new"
                with gzip.open(tmp, "rb") as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(out, self.path)
                self._open()
                log.info("Location database updated (%s)", ym)
                return True
            except Exception as e:
                last_err = str(e)
            finally:
                tmp.unlink(missing_ok=True)
        self.error = f"Could not download the location database: {last_err}"
        log.warning(self.error)
        return False


class Globe:
    """Stream-start events for the globe card, newest last, each with a sequence number for `since`."""

    def __init__(self, geo: GeoDB, started_count=None):
        self.geo = geo
        self.events: deque[dict] = deque(maxlen=KEEP_EVENTS)
        self.seq = 0
        self._count = started_count  # streams started since a time (from the plays table); survives restarts

    def started(self, row: dict, now: float | None = None) -> dict | None:
        """A stream started. Returns the event the browser will get, or None when it has no location."""
        place = self.geo.lookup(row.get("_ip"))
        if not place:
            return None
        self.seq += 1
        ev = {"seq": self.seq, "id": "s_" + secrets.token_urlsafe(6), "at": int(now or time.time()), **place}
        self.events.append(ev)
        return ev

    def since(self, after: int | None) -> dict:
        """Events after the cursor. With no cursor (the page just loaded): none, so streams that were already
        playing do not ping."""
        out = [] if after is None else [{k: e[k] for k in ("id", "at", "label", "lat", "lon")} for e in self.events if e["seq"] > after]
        return {"events": out[-40:], "cursor": self.seq}

    def last_hour(self, now: float | None = None) -> int:
        if self._count is None:
            return 0
        return int(self._count(int((now or time.time()) - 3600)))

    async def loop(self) -> None:
        """Keep the location database fresh: check at start, then every 6 hours."""
        await asyncio.sleep(5)
        while True:
            try:
                if self.geo.stale():
                    await self.geo.refresh()
            except Exception:
                log.exception("location database refresh failed")
            await asyncio.sleep(6 * 3600)


# ---------- sample mode: pretend stream starts so the card has something to show ----------
SAMPLE_CITIES = [
    ("Dallas, TX", 32.8, -96.8, 9), ("Fort Worth, TX", 32.8, -97.3, 3), ("Plano, TX", 33.0, -96.7, 3),
    ("Houston, TX", 29.8, -95.4, 5), ("Austin, TX", 30.3, -97.7, 4), ("Chicago, IL", 41.9, -87.6, 4),
    ("New York, NY", 40.7, -74.0, 5), ("Los Angeles, CA", 34.1, -118.2, 4), ("Seattle, WA", 47.6, -122.3, 2),
    ("Denver, CO", 39.7, -105.0, 2), ("Atlanta, GA", 33.7, -84.4, 3), ("Miami, FL", 25.8, -80.2, 2),
    ("Toronto, CA", 43.7, -79.4, 4), ("Vancouver, CA", 49.3, -123.1, 2), ("Mexico City, MX", 19.4, -99.1, 2),
    ("London, GB", 51.5, -0.1, 3), ("Dublin, IE", 53.3, -6.3, 1), ("Berlin, DE", 52.5, 13.4, 1),
    ("Sao Paulo, BR", -23.6, -46.6, 1), ("Sydney, AU", -33.9, 151.2, 1), ("Manila, PH", 14.6, 121.0, 2),
    ("Tokyo, JP", 35.7, 139.7, 1),
]


class SampleGlobe(Globe):
    """Made-up starts every few seconds, only while the dashboard shows sample data."""

    def __init__(self):
        super().__init__(GeoDB(Path("/nonexistent")))
        self._next = 0.0
        self._hour: deque[float] = deque(time.time() - random.random() * 3600 for _ in range(38))

    def _tick(self) -> None:
        now = time.time()
        while now >= self._next:
            n = 4 if random.random() < 0.12 else 1
            weights = [c[3] for c in SAMPLE_CITIES]
            for c in random.choices(SAMPLE_CITIES, weights=weights, k=n):
                self.seq += 1
                self.events.append({"seq": self.seq, "id": "s_" + secrets.token_urlsafe(6), "at": int(now),
                                    "label": c[0], "lat": c[1], "lon": c[2]})
                self._hour.append(now)
            self._next = (self._next or now) + 3 + random.random() * 5
            if self._next < now - 30:
                self._next = now + 2

    def since(self, after: int | None) -> dict:
        self._tick()
        return super().since(after)

    def last_hour(self, now: float | None = None) -> int:
        cut = (now or time.time()) - 3600
        while self._hour and self._hour[0] < cut:
            self._hour.popleft()
        return len(self._hour)
