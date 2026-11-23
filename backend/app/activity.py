"""Activity log (admins only; Glenn, Nov 22 2026): who signed in, when, and what they did while signed in.

Everything is read under /api/settings/activity, so the middleware's blanket admin gate covers it; viewers never
see anyone's activity, not even their own.

Rows in `activity_log`:
- sign-ins (Plex and local admin logins), sign-outs and denied sign-ins (`login`, `logout`, `denied`),
- pages a person opened (`page`; the page itself tells us through POST /api/activity/page),
- searches on the Requests page (`search`; typing as you go updates one row instead of adding one per letter),
- changes made through the API (`request`, `admin`): non-GET calls that succeeded, named by `ACTIONS`.
Requests themselves come from `request_log` (seerr.py) when the log is read, so they are never stored twice.

History from before this table existed: sign-ins still held in `auth_sessions` are copied in once at start
(`source = 'recovered'`); earlier ones were deleted when they expired and cannot be recovered. `request_log` goes
back to when it started (rows it imported from Seerr are marked `imported`).

A visit (`visits`) is one person's run of events: it starts at a sign-in, or at the first thing they do after
`VISIT_GAP` of quiet while still signed in (the cookie lasts 30 days, so most visits start that way).
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at INTEGER NOT NULL,
  who_kind TEXT NOT NULL,
  who_id TEXT NOT NULL,
  who_name TEXT,
  session TEXT,
  type TEXT NOT NULL,
  label TEXT NOT NULL,
  detail TEXT,
  ip TEXT,
  device TEXT,
  source TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS activity_at ON activity_log (at);
CREATE INDEX IF NOT EXISTS activity_who ON activity_log (who_id, at);
CREATE INDEX IF NOT EXISTS activity_session ON activity_log (session, type, at);
"""

VISIT_GAP = 30 * 60          # quiet this long and the next thing starts a new visit
SEARCH_MERGE = 12            # a search within this many seconds of the last one replaces it (search as you type)
PAGE_REPEAT = 10 * 60        # the same page again within this long is not a new row
LOOK_BACK = 6 * 3600         # how far past a page's cursor visits are read, so none is cut in half
FETCH = 4000                 # events read per page of visits
SIGN_IN_TYPES = ("login",)
NOT_ACTIONS = ("login", "logout", "denied")


def session_id(token: str | None) -> str | None:
    """The first 12 hex of the session token's SHA-256 (the same hash `auth_sessions` keeps). Never the token."""
    return hashlib.sha256(token.encode()).hexdigest()[:12] if token else None


def device_of(ua: str | None) -> str | None:
    """'Chrome · Windows' from a User-Agent. Coarse on purpose: enough to notice a new device."""
    ua = ua or ""
    if not ua:
        return None
    if "AFT" in ua or "Silk/" in ua:
        system = "Fire TV" if "AFT" in ua else "Fire tablet"
    elif "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Android" in ua:
        system = "Android"
    elif "Windows" in ua:
        system = "Windows"
    elif "CrOS" in ua:
        system = "ChromeOS"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        system = "Mac"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = "Other"
    if "Silk/" in ua:
        browser = "Silk"
    elif "Edg" in ua:
        browser = "Edge"
    elif "OPR/" in ua:
        browser = "Opera"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = ua.split("/")[0][:24] or "Browser"
    return f"{browser} · {system}"


# Settings sections by the first part of their path, for "Changed <name>".
SETTINGS_NAMES = {
    "servers": "Plex servers", "admins": "admin logins", "billing": "Billing", "access": "default access", "users": "Users",
    "cleanup": "Unwatched cleanup", "arr": "Radarr & Sonarr", "requests": "Requests", "books": "Books & comics",
    "netdata": "Netdata", "plex": "Plex accounts", "general": "general settings", "backups": "Backups", "history": "watch history",
}
# Personal display choices; saved as the admin tweaks them, so they would only be noise here.
SETTINGS_SKIP = ("globe", "cards")

ACTIONS: list[tuple[str, re.Pattern, str, str]] = [
    # method, path, type, label ({1} = first group)
    ("POST", re.compile(r"/api/streams/([^/]+)/stop"), "admin", "Stopped a stream"),
    ("POST", re.compile(r"/api/requests/request/(\d+)/retry"), "request", "Tried a failed request again"),
    ("POST", re.compile(r"/api/requests/request/(\d+)/notify(?:-import)?"), "request", "Asked an admin for help"),
    ("POST", re.compile(r"/api/requests/request/(\d+)/(approve|decline)"), "admin", "{2}d a request"),
    ("DELETE", re.compile(r"/api/requests/request/(\d+)"), "request", "Canceled a request"),
    ("POST", re.compile(r"/api/requests/watch/(\d+)"), "request", "Turned on Notify me too"),
    ("DELETE", re.compile(r"/api/requests/watch/(\d+)"), "request", "Turned off Notify me too"),
    ("POST", re.compile(r"/api/requests/watchlist"), "request", "Added to their watchlist"),
    ("DELETE", re.compile(r"/api/requests/watchlist/(\d+)"), "request", "Removed from their watchlist"),
    ("POST", re.compile(r"/api/requests/issue"), "request", "Reported an issue"),
    ("POST", re.compile(r"/api/requests/issue/(\d+)/comment"), "request", "Commented on an issue"),
    ("POST", re.compile(r"/api/requests/issue/(\d+)/(\w+)"), "request", "Marked an issue {2}"),
    ("POST", re.compile(r"/api/requests/books/(comic|ebook|audiobook)/(\d+)/(\w+)"), "request", "{3} · {1} request"),
    ("POST", re.compile(r"/api/users/([^/]+)/manual-paid"), "admin", "Marked paid by hand"),
    ("DELETE", re.compile(r"/api/users/([^/]+)/manual-paid"), "admin", "Took off paid by hand"),
    ("PUT", re.compile(r"/api/settings/users/([^/]+)"), "admin", "Changed a person's access"),
    ("POST", re.compile(r"/api/settings/cleanup/(\d+)/(keep|extend|delete|track)"), "admin", "Unwatched cleanup · {2}"),
]
CLEANUP_WORDS = {"keep": "kept", "extend": "gave more days", "delete": "deleted now", "track": "tracked again"}


class Activity:
    def __init__(self, store, directory, ip_of: Callable, is_admin_email: Callable[[str | None], bool]):
        self.store = store
        self.directory = directory
        self.ip_of = ip_of
        self.is_admin_email = is_admin_email
        self._last_page: dict[str, float] = {}
        with store.lock:
            store.db.executescript(SCHEMA)
            store.db.commit()
        self.recover()

    # ---------- writing ----------
    def _insert(self, at, who, session, type_, label, detail=None, ip=None, device=None, source="live") -> int:
        kind, wid, name = who
        with self.store.lock:
            cur = self.store.db.execute(
                "INSERT INTO activity_log (at, who_kind, who_id, who_name, session, type, label, detail, ip, device, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(at), kind, str(wid), name, session, type_, label[:160], (detail or None) and str(detail)[:240], ip, device, source))
            self.store.db.commit()
            return cur.lastrowid

    def _where(self, request, token: str | None = None) -> dict:
        from .auth import SESSION_COOKIE
        return {"session": session_id(token or request.cookies.get(SESSION_COOKIE)), "ip": self.ip_of(request),
                "device": device_of(request.headers.get("user-agent"))}

    def record(self, request, principal, type_: str, label: str, detail: str | None = None, *, token: str | None = None) -> None:
        w = self._where(request, token)
        self._insert(time.time(), (principal.kind, principal.subject, principal.name), w["session"], type_, label, detail, w["ip"], w["device"])

    def signed_in(self, request, kind: str, subject: str, name: str | None, token: str, admin: bool) -> None:
        w = self._where(request, token)
        label = {"plex": "Signed in with Plex", "emby": "Signed in with Emby"}.get(kind, "Signed in with an admin login")
        self._insert(time.time(), (kind, subject, name), w["session"], "login", label, "Admin" if admin else "Viewer", w["ip"], w["device"])

    def denied(self, request, kind: str, subject: str | None, name: str | None, reason: str) -> None:
        w = self._where(request, "")
        self._insert(time.time(), (kind, subject or "", name or "unknown"), None, "denied", "Sign-in denied", reason, w["ip"], w["device"])

    def page(self, request, principal, path: str, title: str | None) -> bool:
        """A page the person opened (sent by the page). The same page again within PAGE_REPEAT is skipped."""
        path = (path or "").strip()[:200]
        if not path.startswith(("#/", "/")):
            return False
        name = re.sub(r"\s*·\s*Greystar Telemetry\s*$", "", (title or "").strip())[:120] or path
        w = self._where(request)
        key = (w["session"] or principal.subject) + "|" + path
        now = time.time()
        if now - self._last_page.get(key, 0) < PAGE_REPEAT:
            return False
        if len(self._last_page) > 5000:
            self._last_page = {k: t for k, t in self._last_page.items() if now - t < PAGE_REPEAT}
        self._last_page[key] = now
        self._insert(now, (principal.kind, principal.subject, principal.name), w["session"], "page", "Opened " + name, path, w["ip"], w["device"])
        return True

    def search(self, request, principal, q: str) -> None:
        q = " ".join((q or "").split())[:100]
        if len(q) < 2:
            return
        w = self._where(request)
        now = int(time.time())
        with self.store.lock:
            last = self.store.db.execute(
                "SELECT id FROM activity_log WHERE session = ? AND type = 'search' AND at >= ? ORDER BY at DESC LIMIT 1",
                (w["session"], now - SEARCH_MERGE)).fetchone() if w["session"] else None
            if last:
                self.store.db.execute("UPDATE activity_log SET at = ?, detail = ? WHERE id = ?", (now, f"“{q}”", last["id"]))
                self.store.db.commit()
                return
        self._insert(now, (principal.kind, principal.subject, principal.name), w["session"], "search", "Searched movies & shows", f"“{q}”", w["ip"], w["device"])

    def after(self, request, principal, status: int) -> None:
        """Called by the middleware after every API call. Records the ones worth keeping."""
        if principal is None or status >= 400:
            return
        method, path = request.method, request.url.path
        try:
            if method == "GET":
                if path == "/api/requests/search":
                    self.search(request, principal, request.query_params.get("q", ""))
                elif path == "/api/settings/requests/log.csv":
                    self.record(request, principal, "admin", "Downloaded the request log")
                else:
                    m = re.fullmatch(r"/api/users/([^/]+)/history", path)
                    if m and request.query_params.get("before") is None:
                        self.record(request, principal, "admin", "Looked up a person", self._person(m.group(1)))
                return
            if method not in ("POST", "PUT", "PATCH", "DELETE"):
                return
            if path.startswith(("/api/auth/", "/api/activity/", "/api/notifications/")) or path == "/api/requests/request":
                return
            for meth, rx, type_, label in ACTIONS:
                m = rx.fullmatch(path)
                if meth == method and m:
                    g = m.groups()
                    text = label
                    for i, v in enumerate(g, 1):
                        text = text.replace("{%d}" % i, CLEANUP_WORDS.get(v, v) if "cleanup" in path else v)
                    text = text[:1].upper() + text[1:]
                    detail = None
                    if "/users/" in path:
                        detail = self._person(g[0])
                    elif "/cleanup/" in path:
                        detail = self._cleanup_title(g[0])
                    elif "/request/" in path or "/watch/" in path:
                        detail = "Request #" + g[0]
                    self.record(request, principal, type_, text, detail)
                    return
            if path.startswith("/api/settings/"):
                parts = path[len("/api/settings/"):].split("/")
                if parts[0] in SETTINGS_SKIP:
                    return
                name = SETTINGS_NAMES.get(parts[0], parts[0].replace("-", " "))
                tail = parts[-1]
                verb = {"test": "Tested", "sync": "Synced", "run": "Ran", "import": "Imported", "link": "Linked"}.get(tail)
                if verb is None:
                    verb = "Removed from" if method == "DELETE" else "Changed"
                self.record(request, principal, "admin", f"{verb} {name}", "/".join(parts[1:]) or None)
        except Exception:  # the log must never break the call it describes
            return

    def _person(self, plex_id: str) -> str:
        u = self.directory.get_user(plex_id)
        return (u.get("title") or u.get("username")) if u else plex_id

    def _cleanup_title(self, item_id: str) -> str | None:
        try:
            r = self.store.db.execute("SELECT title, year FROM cleanup_items WHERE id = ?", (int(item_id),)).fetchone()
        except Exception:
            return None
        return (r["title"] + (f" ({r['year']})" if r["year"] else "")) if r else None

    # ---------- history from before the log ----------
    def recover(self) -> int:
        """Copy sign-ins still held in auth_sessions into the log, once (earlier ones expired and are gone)."""
        if self.store.get_setting("activity_recovered"):
            return 0
        db = self.store.db
        n = 0
        with self.store.lock:
            rows = db.execute("SELECT token_hash, kind, subject, created FROM auth_sessions").fetchall()
            for r in rows:
                if r["kind"] == "local":
                    a = db.execute("SELECT username FROM local_admins WHERE id = ?", (r["subject"],)).fetchone()
                    name, label = (a["username"] if a else "admin " + r["subject"]), "Signed in with an admin login"
                else:
                    u = db.execute("SELECT title, username, email FROM users WHERE plex_id = ?", (r["subject"],)).fetchone()
                    name = (u["title"] or u["username"]) if u else r["subject"]
                    label = "Signed in with Plex"
                db.execute("INSERT INTO activity_log (at, who_kind, who_id, who_name, session, type, label, detail, source) "
                           "VALUES (?, ?, ?, ?, ?, 'login', ?, ?, 'recovered')",
                           (r["created"], r["kind"], r["subject"], name, r["token_hash"][:12], label, "From the sign-in record"))
                n += 1
            db.commit()
        self.store.set_setting("activity_recovered", str(int(time.time())))
        return n

    # ---------- reading ----------
    def _filter(self, q: str, who: str | None, col_kind: str, col_id: str, col_name: str) -> tuple[str, list]:
        if who and ":" in who:
            kind, wid = who.split(":", 1)
            return f" AND {col_kind} = ? AND {col_id} = ?", [kind, wid]
        q = (q or "").strip()
        if q:
            return f" AND ({col_name} LIKE ? OR {col_id} = ?)", [f"%{q}%", q]
        return "", []

    def _events(self, q: str, who: str | None, before: int | None, limit: int, since: int | None = None) -> list[dict]:
        db = self.store.db
        out: list[dict] = []
        f1, a1 = self._filter(q, who, "who_kind", "who_id", "who_name")
        f2, a2 = self._filter(q, who, "who_kind", "who_id", "who_name")
        top = before if before is not None else 2 ** 40
        low = since or 0
        with self.store.lock:
            rows = db.execute(f"SELECT * FROM activity_log WHERE at < ? AND at >= ?{f1} ORDER BY at DESC, id DESC LIMIT ?", [top, low, *a1, limit]).fetchall()
            try:
                reqs = db.execute(f"SELECT * FROM request_log WHERE at < ? AND at >= ?{f2} ORDER BY at DESC LIMIT ?", [top, low, *a2, limit]).fetchall()
            except Exception:  # no request_log yet (Seerr never connected)
                reqs = []
        for r in rows:
            out.append({"at": r["at"], "kind": r["who_kind"], "id": r["who_id"], "name": r["who_name"], "session": r["session"],
                        "type": r["type"], "label": r["label"], "detail": r["detail"], "ip": r["ip"], "device": r["device"], "source": r["source"]})
        for r in reqs:
            out.append({"at": r["at"], "kind": r["who_kind"], "id": r["who_id"], "name": r["who_name"], "session": None,
                        "type": "request", "label": "Requested", "detail": _request_detail(r), "ip": None, "device": None,
                        "source": "imported" if r["source"] != "site" else "live"})
        out.sort(key=lambda e: e["at"], reverse=True)
        return out[:limit]

    def visits(self, q: str = "", who: str | None = None, before: int | None = None, limit: int = 20, now: float | None = None) -> dict:
        now = now or time.time()
        limit = max(1, min(limit, 60))
        events = self._events(q, who, before + LOOK_BACK if before else None, FETCH)
        full = len(events) < FETCH
        chron = sorted(events, key=lambda e: e["at"])
        open_: dict[tuple, dict] = {}
        out: list[dict] = []
        for e in chron:
            key = (e["kind"], e["id"])
            cur = open_.get(key)
            starts = (cur is None or e["type"] in ("login", "denied") or cur["how"] == "denied" or e["at"] - cur["end"] > VISIT_GAP
                      or (e["session"] and cur["session"] and e["session"] != cur["session"]))
            if starts:
                cur = {"who": {"kind": e["kind"], "id": e["id"], "name": e["name"]}, "start": e["at"], "end": e["at"],
                       "how": "login" if e["type"] == "login" else "denied" if e["type"] == "denied" else "return",
                       "session": e["session"], "ip": e["ip"], "device": e["device"], "events": []}
                open_[key] = cur
                out.append(cur)
            cur["end"] = e["at"]
            cur["session"] = cur["session"] or e["session"]
            cur["ip"] = cur["ip"] or e["ip"]
            cur["device"] = cur["device"] or e["device"]
            if e["name"]:
                cur["who"]["name"] = e["name"]
            cur["events"].append({"at": e["at"], "type": e["type"], "label": e["label"], "detail": e["detail"], "source": e["source"]})
        oldest = chron[0]["at"] if chron else None
        keep = [v for v in out if (before is None or v["start"] < before) and (full or v["start"] > oldest)]
        keep.sort(key=lambda v: v["start"], reverse=True)
        page = keep[:limit]
        more = len(keep) > limit or not full
        admins: dict[tuple, bool] = {}
        for v in page:
            v["newDevice"] = self._new_device(v)  # takes the lock itself, so before the loop below
        for v in page:
            k = (v["who"]["kind"], v["who"]["id"])
            if k not in admins:
                admins[k] = self._is_admin(*k)
            v["who"]["admin"] = admins[k]
            last = v["events"][-1]
            v["ended"] = ("denied" if v["how"] == "denied" else "logout" if last["type"] == "logout"
                          else "active" if now - v["end"] < VISIT_GAP else "idle")
            v["minutes"] = max(1, round((v["end"] - v["start"]) / 60)) if v["how"] != "denied" else 0
            v["recovered"] = all(x["source"] == "recovered" for x in v["events"])
            v["id"] = f"{v['who']['kind']}:{v['who']['id']}:{v['start']}"
            v.pop("session", None)
        return {"visits": page, "next": page[-1]["start"] if page and more else None}

    def _is_admin(self, kind: str, wid: str) -> bool:
        if kind == "local":
            return True
        u = self.directory.get_user(wid)
        return bool(u and self.is_admin_email(u.get("email")))

    def _new_device(self, v: dict) -> bool:
        """True when this person had signed in or done something before, but never from this device."""
        if not v["device"] or v["how"] == "denied":
            return False
        db = self.store.db
        with self.store.lock:
            before = db.execute("SELECT 1 FROM activity_log WHERE who_kind = ? AND who_id = ? AND at < ? AND device IS NOT NULL LIMIT 1",
                                (v["who"]["kind"], v["who"]["id"], v["start"])).fetchone()
            if not before:
                return False
            same = db.execute("SELECT 1 FROM activity_log WHERE who_kind = ? AND who_id = ? AND at < ? AND device = ? LIMIT 1",
                              (v["who"]["kind"], v["who"]["id"], v["start"], v["device"])).fetchone()
        return same is None

    def summary(self, q: str = "", who: str | None = None, tz_offset_min: int = 0, now: float | None = None) -> dict:
        """Daily counts since the site went live, totals, coverage and things worth a look (for the matched people)."""
        now = int(now or time.time())
        db = self.store.db
        f1, a1 = self._filter(q, who, "who_kind", "who_id", "who_name")
        off = int(tz_offset_min) * 60
        days: dict[str, dict] = {}
        day_sql = "date(at + ?, 'unixepoch')"  # tz: -getTimezoneOffset(), like /api/viewing
        one = lambda sql, args=(): db.execute(sql, list(args)).fetchone()[0]

        def bump(rows, field):
            for d, n in rows:
                days.setdefault(d, {"day": d, "events": 0, "signins": 0})[field] += n

        with self.store.lock:
            bump(db.execute(f"SELECT {day_sql}, COUNT(*) FROM activity_log WHERE 1=1{f1} GROUP BY 1", [off, *a1]).fetchall(), "events")
            bump(db.execute(f"SELECT {day_sql}, COUNT(*) FROM activity_log WHERE type = 'login'{f1} GROUP BY 1", [off, *a1]).fetchall(), "signins")
            try:
                bump(db.execute(f"SELECT {day_sql}, COUNT(*) FROM request_log WHERE 1=1{f1} GROUP BY 1", [off, *a1]).fetchall(), "events")
                req_first = one("SELECT MIN(at) FROM request_log")
                req_count = one(f"SELECT COUNT(*) FROM request_log WHERE 1=1{f1}", a1)
            except Exception:  # no request_log yet
                req_first, req_count = None, 0
            # The site went live with its first recorded play (Oct 21, 2026); never later than the first log row.
            firsts = [x for x in (one("SELECT MIN(started_at) FROM plays WHERE source = 'live'"), one("SELECT MIN(at) FROM activity_log"), req_first) if x]
            coverage = {"signins": one("SELECT MIN(at) FROM activity_log WHERE type = 'login'"),
                        "pages": one("SELECT MIN(at) FROM activity_log WHERE source = 'live'"), "requests": req_first}
            totals = {
                "signins": one(f"SELECT COUNT(*) FROM activity_log WHERE type = 'login'{f1}", a1),
                "actions": one(f"SELECT COUNT(*) FROM activity_log WHERE type NOT IN ('login','logout','denied'){f1}", a1) + req_count,
                "people": one(f"SELECT COUNT(DISTINCT who_kind || ':' || who_id) FROM activity_log WHERE type <> 'denied'{f1}", a1),
            }
            recent = db.execute(f"SELECT at, who_kind, who_id, who_name, device FROM activity_log WHERE type = 'login' AND device IS NOT NULL "
                                f"AND at >= ?{f1} ORDER BY at DESC LIMIT 200", [now - 14 * 86400, *a1]).fetchall()
            denied = one(f"SELECT COUNT(*) FROM activity_log WHERE type = 'denied' AND at >= ?{f1}", [now - 30 * 86400, *a1])
        launch = min(firsts) if firsts else now
        coverage["launch"] = launch
        # Worth a look: sign-ins from a new device (14 days) and denied sign-ins (30 days).
        flags = []
        for r in recent:
            v = {"who": {"kind": r["who_kind"], "id": r["who_id"]}, "start": r["at"], "device": r["device"], "how": "login"}
            if self._new_device(v):
                flags.append({"kind": "device", "at": r["at"], "who": {"kind": r["who_kind"], "id": r["who_id"], "name": r["who_name"]}, "device": r["device"]})
                if len(flags) >= 4:
                    break
        series = []
        t = launch + off
        today = time.strftime("%Y-%m-%d", time.gmtime(now + off))
        while True:
            d = time.strftime("%Y-%m-%d", time.gmtime(t))
            if d > today:
                break
            series.append(days.get(d, {"day": d, "events": 0, "signins": 0}))
            t += 86400
        return {"days": series, "totals": totals, "coverage": coverage, "flags": flags, "denied30": denied}

    def people(self, q: str, limit: int = 8) -> list[dict]:
        q = (q or "").strip()
        if not q:
            return []
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT who_kind, who_id, MAX(who_name) AS name, MAX(at) AS last, SUM(type = 'login') AS signins FROM activity_log "
                "WHERE type <> 'denied' AND (who_name LIKE ? OR who_id = ?) GROUP BY who_kind, who_id ORDER BY last DESC LIMIT ?",
                (f"%{q}%", q, limit)).fetchall()
        return [{"kind": r["who_kind"], "id": r["who_id"], "name": r["name"], "last": r["last"], "signins": r["signins"]} for r in rows]


def _request_detail(r) -> str:
    title = r["title"] or f"TMDB {r['tmdb_id']}"
    bits = [title + (f" ({r['year']})" if r["year"] and r["media_type"] == "movie" else "")]
    if r["seasons"]:
        bits.append("Season " + r["seasons"].replace(",", ", ") if "," not in r["seasons"] else "Seasons " + r["seasons"].replace(",", ", "))
    if r["is4k"]:
        bits.append("4K")
    if r["source"] != "site":
        bits.append("imported from Seerr")
    return " · ".join(bits)
