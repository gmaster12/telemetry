"""Notifications and the Welcome back sheet (Glenn's pick A "Welcome sheet", Nov 14 2026).

Every signed-in person has a list of notifications kept here, so nothing is lost when they are not on the site.
When they come back, the page opens the Welcome back sheet with what they have not seen yet (`web/notify.js`).

What makes one (`Notifier.tick`, every 2 minutes):
- Their own movie and show requests (Seerr's full list as its admin; the requester is found through `seerr_links`):
  "started downloading" (Radarr or Sonarr has it in the queue), "is on Plex" (Seerr says available; for a show, every
  requested season), "could not be downloaded" (Seerr says failed).
- Their own comic, ebook and audiobook requests (the media site's signed admin feed, `Media.admin_log`): the same three.
- Billing (Stripe, read-only, from the copy the poller keeps): a subscription that is past due, unpaid or incomplete.
  Urgent: it stays pinned until Stripe says it is paid, then it says so.
- Admins (shared by every admin, each with their own seen marks): payments that failed (names), requests on the
  Needs you list, a server not answering (and how long it was down once it is back), titles due for Unwatched cleanup.
  "N new requests since your last visit" is counted when the list is read (`request_log`), not stored.

Transitions only: `notify_state` remembers each request's last state, so a state is announced once. The first run
only records where everything is (`notify_seeded`), so turning this on does not flood anyone with old news.

Never someone else's request in a member's list, never release names, never other members' names for members.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from . import request_state as rs
from .requests_api import IMAGES
from .seerr import SeerrError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

log = logging.getLogger("telemetry.notify")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  who TEXT NOT NULL,
  key TEXT NOT NULL,
  kind TEXT NOT NULL,
  urgent INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL,
  body TEXT,
  action TEXT,
  href TEXT,
  media TEXT,
  poster TEXT,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  resolved_at INTEGER,
  UNIQUE (who, key)
);
CREATE INDEX IF NOT EXISTS notifications_who ON notifications (who, updated);
CREATE TABLE IF NOT EXISTS notify_seen (
  id INTEGER NOT NULL,
  reader TEXT NOT NULL,
  seen_at INTEGER NOT NULL,
  PRIMARY KEY (id, reader)
);
CREATE TABLE IF NOT EXISTS notify_state (
  key TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notify_readers (
  reader TEXT PRIMARY KEY,
  last_seen INTEGER,
  shown_at INTEGER
);
"""

ADMINS = "admins"
TICK_SECONDS = 120
KEEP_DAYS = 30            # what the list shows (urgent ones that are still open always show)
PRUNE_DAYS = 60
REQUEST_DAYS = 45         # requests older than this are not followed any more
LIST_MAX = 60
WELCOME_GAP = 6 * 3600   # a visit after this long counts as coming back
PAY_PROBLEM = ("past_due", "unpaid", "incomplete")
# a request's states in order; a state is announced only when it moves forward (or to failed)
RANK = {"waiting": 0, "started": 1, "ready": 2, "failed": 3}
ISSUE_TYPES = {1: "Video", 2: "Audio", 3: "Subtitle", 4: "Other"}
MEDIA_WORD = {"movie": "Movie", "tv": "Show", "comic": "Comic", "ebook": "Ebook", "audiobook": "Audiobook"}


def reader_of(p) -> str:
    return f"{p.kind}:{p.subject}"


class Notifier:
    def __init__(self, store, seerr=None, arr=None, media=None, billing=None, directory=None, poller=None, cleanup=None, status=None,
                 backups=None):
        self.store = store
        self.db = store.db
        self.seerr, self.arr, self.media, self.billing = seerr, arr, media, billing
        self.directory, self.poller, self.cleanup = directory, poller, cleanup
        # Where a request stands, decided the way every page decides it, Plex's own answer included (Glenn's picks
        # O1A and 4A, Nov 15 2026): "is ready" goes out when the title page says On Plex, not hours later.
        self.status = status or rs.RequestStatus(seerr, arr)
        self.backups = backups   # the nightly database copies (app/backup.py): a failed one is the admins' to sort out
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.last_error: str | None = None
        self.ticked_at: float = 0

    # ---------- the list ----------
    def post(self, who: str, key: str, kind: str, title: str, body: str | None = None, action: str | None = None,
             href: str | None = None, urgent: bool = False, media: str | None = None, poster: str | None = None,
             now: float | None = None) -> int:
        """Adds a notification, or updates the one with the same key. A changed title or text, or one that had been
        resolved, counts as new again (everyone's seen marks on it are dropped)."""
        now = int(now or time.time())
        with self.store.lock:
            row = self.db.execute("SELECT id, title, body, resolved_at FROM notifications WHERE who = ? AND key = ?", (who, key)).fetchone()
            if row is None:
                cur = self.db.execute(
                    "INSERT INTO notifications (who, key, kind, urgent, title, body, action, href, media, poster, created, updated) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (who, key, kind, int(urgent), title, body, action, href, media, poster, now, now))
                self.db.commit()
                return int(cur.lastrowid)
            nid = int(row["id"])
            if row["title"] != title or (row["body"] or "") != (body or "") or row["resolved_at"]:
                self.db.execute("UPDATE notifications SET kind = ?, urgent = ?, title = ?, body = ?, action = ?, href = ?, media = ?, poster = ?, "
                                "updated = ?, resolved_at = NULL WHERE id = ?",
                                (kind, int(urgent), title, body, action, href, media, poster, now, nid))
                self.db.execute("DELETE FROM notify_seen WHERE id = ?", (nid,))
                self.db.commit()
            return nid

    def resolve(self, who: str, key: str, title: str | None = None, body: str | None = None, now: float | None = None) -> bool:
        """Closes an urgent one (paid, back up, handled). With a title it also says what happened, as news."""
        now = int(now or time.time())
        with self.store.lock:
            row = self.db.execute("SELECT id, resolved_at FROM notifications WHERE who = ? AND key = ?", (who, key)).fetchone()
            if row is None or row["resolved_at"]:
                return False
            if title:
                self.db.execute("UPDATE notifications SET title = ?, body = ?, action = NULL, resolved_at = ?, updated = ? WHERE id = ?",
                                (title, body, now, now, row["id"]))
                self.db.execute("DELETE FROM notify_seen WHERE id = ?", (row["id"],))
            else:
                self.db.execute("UPDATE notifications SET resolved_at = ? WHERE id = ?", (now, row["id"]))
            self.db.commit()
            return True

    def withdraw(self, who: str, key: str) -> bool:
        """Takes a notice back without an outcome: what it was about is gone (a server deleted while it was down,
        the Stripe key removed while someone was past due). Nobody can say whether it was sorted out, so it goes."""
        with self.store.lock:
            row = self.db.execute("SELECT id FROM notifications WHERE who = ? AND key = ?", (who, key)).fetchone()
            if row is None:
                return False
            self.db.execute("DELETE FROM notify_seen WHERE id = ?", (row["id"],))
            self.db.execute("DELETE FROM notifications WHERE id = ?", (row["id"],))
            self.db.commit()
            return True

    def open_keys(self, who: str, prefix: str) -> list[str]:
        return [r["key"] for r in self.db.execute(
            "SELECT key FROM notifications WHERE who = ? AND key LIKE ? AND resolved_at IS NULL", (who, prefix + "%"))]

    def audiences(self, p) -> list[str]:
        out = []
        if p.member:  # Plex and Emby members share the member-id audience
            out.append(f"plex:{p.subject}")
        if p.is_admin:
            out.append(ADMINS)
        return out

    def _reader(self, reader: str) -> dict:
        r = self.db.execute("SELECT last_seen, shown_at FROM notify_readers WHERE reader = ?", (reader,)).fetchone()
        return dict(r) if r else {"last_seen": None, "shown_at": None}

    def last_seen(self, reader: str) -> int | None:
        return self._reader(reader)["last_seen"]

    def _touch(self, reader: str, field: str, now: int) -> None:
        self.db.execute(f"INSERT INTO notify_readers (reader, {field}) VALUES (?, ?) "
                        f"ON CONFLICT(reader) DO UPDATE SET {field} = excluded.{field}", (reader, now))

    def shown(self, p, now: float | None = None) -> None:
        """The page opened the Welcome back sheet by itself: not again until something new comes, or a new visit."""
        with self.store.lock:
            self._touch(reader_of(p), "shown_at", int(now or time.time()))
            self.db.commit()

    def items_for(self, p, now: float | None = None) -> dict:
        now = int(now or time.time())
        who = self.audiences(p)
        reader = reader_of(p)
        if not who:
            return {"items": [], "unseen": 0, "pinned": 0, "since": None, "welcome": False}
        q = ",".join("?" * len(who))
        rows = self.db.execute(
            f"SELECT n.*, s.seen_at FROM notifications n LEFT JOIN notify_seen s ON s.id = n.id AND s.reader = ? "
            f"WHERE n.who IN ({q}) AND (n.updated > ? OR (n.urgent = 1 AND n.resolved_at IS NULL)) "
            f"ORDER BY (n.urgent = 1 AND n.resolved_at IS NULL) DESC, n.updated DESC LIMIT ?",
            [reader, *who, now - KEEP_DAYS * 86400, LIST_MAX]).fetchall()
        items = [self._public(r) for r in rows]
        rd = self._reader(reader)
        since = rd["last_seen"]
        if p.is_admin:
            extra = self._new_requests(since, now)
            if extra:
                items.insert(sum(1 for x in items if x["pinned"]), extra)
        unseen = sum(1 for x in items if not x["seen"])
        pinned = sum(1 for x in items if x["pinned"])
        # Open the sheet by itself when there is something to see and either something new came since it last opened,
        # or this is a new visit (it last opened over WELCOME_GAP ago). "Remind me next time" waits for that.
        shown = rd["shown_at"]
        fresh = any(not x["seen"] and (shown is None or x["at"] > shown) for x in items if x["id"] != "new-requests")
        welcome = bool(unseen or pinned) and (shown is None or fresh or now - shown > WELCOME_GAP)
        return {"items": items, "unseen": unseen, "pinned": pinned, "since": since, "welcome": welcome, "now": now}

    @staticmethod
    def _public(r) -> dict:
        open_urgent = bool(r["urgent"]) and not r["resolved_at"]
        return {"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"], "action": r["action"], "href": r["href"],
                "media": r["media"], "poster": poster_url(r["poster"]), "at": r["updated"], "urgent": bool(r["urgent"]),
                "pinned": open_urgent, "resolved": bool(r["resolved_at"]), "seen": r["seen_at"] is not None}

    def _new_requests(self, since: int | None, now: int) -> dict | None:
        """Admins: how many requests came in since this admin last cleared the list (movies and shows made here)."""
        start = since or now - 86400
        try:
            rows = self.db.execute("SELECT media_type, COUNT(*) AS n FROM request_log WHERE at > ? GROUP BY media_type", (start,)).fetchall()
        except Exception:  # noqa: BLE001  (no request log table yet)
            return None
        counts = {r["media_type"]: int(r["n"]) for r in rows}
        total = sum(counts.values())
        if not total:
            return None
        parts = []
        for k, one, many in (("tv", "show", "shows"), ("movie", "movie", "movies")):
            if counts.get(k):
                parts.append(f"{counts[k]} {one if counts[k] == 1 else many}")
        return {"id": "new-requests", "kind": "requests", "title": f"{total} new request{'' if total == 1 else 's'}",
                "body": ", ".join(parts) + ("." if parts else ""), "action": "Request log", "href": "settings:reqlog",
                "media": None, "poster": None, "at": now, "urgent": False, "pinned": False, "resolved": False, "seen": False}

    def mark_seen(self, p, ids: list[int] | None = None, now: float | None = None) -> int:
        """Marks the given ones seen, or all of them (and moves this person's "last visit" to now)."""
        now = int(now or time.time())
        reader = reader_of(p)
        who = self.audiences(p)
        if not who:
            return 0
        q = ",".join("?" * len(who))
        with self.store.lock:
            if ids is None:
                rows = self.db.execute(f"SELECT id FROM notifications WHERE who IN ({q})", who).fetchall()
                target = [int(r["id"]) for r in rows]
                self._touch(reader, "last_seen", now)
            else:
                wanted = [int(i) for i in ids][:200]
                if not wanted:
                    return 0
                rows = self.db.execute(f"SELECT id FROM notifications WHERE who IN ({q}) AND id IN ({','.join('?' * len(wanted))})",
                                       [*who, *wanted]).fetchall()
                target = [int(r["id"]) for r in rows]
            self.db.executemany("INSERT OR IGNORE INTO notify_seen (id, reader, seen_at) VALUES (?, ?, ?)", [(i, reader, now) for i in target])
            self.db.commit()
        return len(target)

    def prune(self, now: float | None = None) -> None:
        cut = int(now or time.time()) - PRUNE_DAYS * 86400
        with self.store.lock:
            self.db.execute("DELETE FROM notify_seen WHERE id IN (SELECT id FROM notifications WHERE updated < ? AND (urgent = 0 OR resolved_at IS NOT NULL))", (cut,))
            self.db.execute("DELETE FROM notifications WHERE updated < ? AND (urgent = 0 OR resolved_at IS NOT NULL)", (cut,))
            self.db.execute("DELETE FROM notify_state WHERE at < ?", (cut,))
            self.db.commit()

    # ---------- remembered states ----------
    def _state(self, key: str) -> str | None:
        r = self.db.execute("SELECT state FROM notify_state WHERE key = ?", (key,)).fetchone()
        return r["state"] if r else None

    def _set_state(self, key: str, state: str, now: int) -> None:
        with self.store.lock:
            self.db.execute("INSERT INTO notify_state (key, state, at) VALUES (?, ?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET state = excluded.state, at = excluded.at", (key, state, now))
            self.db.commit()

    def _moved(self, key: str, state: str, seeding: bool, now: int) -> bool:
        """True when this request's state moved forward since last time (and this is not the first, silent run)."""
        old = self._state(key)
        if old == state:
            return False
        if old is not None and state != "failed" and RANK.get(state, 0) <= RANK.get(old, 0):
            self._set_state(key, state, now)   # going back (a failed one tried again): remember, do not announce
            return False
        self._set_state(key, state, now)
        return not seeding and state != "waiting"

    # ---------- the loop ----------
    async def loop(self) -> None:
        await asyncio.sleep(20)   # let the first polls run
        while True:
            try:
                await self.tick()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  (never let a bad tick stop notifications)
                self.last_error = str(e)
                log.exception("Notifications: tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self, now: float | None = None) -> None:
        now = int(now or time.time())
        seeding = not self.store.get_setting("notify_seeded")
        await self.requests_tick(seeding, now)
        await self.books_tick(seeding, now)
        self.billing_tick(now)
        self.admin_tick(now)
        await self.issues_tick(now)
        if seeding:
            self.store.set_setting("notify_seeded", str(now))
        self.prune(now)
        self.ticked_at = now

    def _tz(self):
        name = self.store.get_setting("cleanup_tz") or "America/Chicago"
        try:
            return ZoneInfo(name) if ZoneInfo else None
        except Exception:  # noqa: BLE001
            return None

    def _clock(self, ts: float) -> str:
        """A time of day in Glenn's time zone, for text saved with a notification: "3:42 AM"."""
        d = dt.datetime.fromtimestamp(ts, self._tz() or dt.timezone.utc)
        return d.strftime("%I:%M %p").lstrip("0")

    def _day(self, ts: float) -> str:
        d = dt.datetime.fromtimestamp(ts, self._tz() or dt.timezone.utc)
        return d.strftime("%b ") + str(d.day)

    # ---------- movies and shows ----------
    def _seerr_people(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for r in self.db.execute("SELECT plex_id, seerr_id FROM seerr_links"):
            out.setdefault(int(r["seerr_id"]), []).append(str(r["plex_id"]))
        return out

    async def _seerr_state(self, q: dict, gate: asyncio.Semaphore) -> str:
        """waiting, started, ready, failed or declined (app/request_state.py decides; this only names it)."""
        return rs.notice_word((await self.status.check(q, gate))["stage"])

    async def requests_tick(self, seeding: bool, now: int) -> None:
        if not (self.seerr and self.seerr.configured()):
            return
        try:
            every = await self.seerr.all_requests()
        except SeerrError as e:
            log.info("Notifications: Seerr not answering: %s", e.message)
            return
        people = self._seerr_people()
        cutoff = now - REQUEST_DAYS * 86400
        rows = []
        for q in every.values():
            uid = (q.get("requestedBy") or {}).get("id")
            if uid is None or int(uid) not in people:
                continue
            created = _stamp(q.get("createdAt"))
            if created and created < cutoff:
                continue
            rows.append(q)
        gate = asyncio.Semaphore(4)
        states = await asyncio.gather(*[self._seerr_state(q, gate) for q in rows], return_exceptions=True)
        for q, st in zip(rows, states):
            if isinstance(st, BaseException) or st == "declined":
                continue
            key = f"seerr:{q['id']}"
            if not self._moved(key, st, seeding, now):
                continue
            m = q.get("media") or {}
            kind = q.get("type") or m.get("mediaType") or "movie"
            tmdb = m.get("tmdbId")
            info = await self.seerr.title_of(kind, int(tmdb)) if tmdb else {}
            title = info.get("title") or "Your request"
            seasons = sorted(s.get("seasonNumber") for s in q.get("seasons") or [] if s.get("seasonNumber"))
            what = title + (f", season {seasons[0]}" if len(seasons) == 1 else f", seasons {seasons[0]} to {seasons[-1]}" if seasons else "")
            what += " (4K)" if q.get("is4k") else ""
            href = f"#/requests/{'series' if kind == 'tv' else 'movie'}/{tmdb}"
            for pid in people[int(q["requestedBy"]["id"])]:
                self._post_request(f"plex:{pid}", key, st, what, kind, href, info.get("poster"), now)

    def _post_request(self, who: str, key: str, state: str, what: str, media: str, href: str, poster: str | None, now: int) -> None:
        days = None
        if self.cleanup and media in ("movie", "tv") and self.cleanup.enabled():
            try:
                days = self.cleanup.days()
            except Exception:  # noqa: BLE001
                days = None
        if state == "ready":
            book = media in ("comic", "ebook", "audiobook")
            body = ("Ready in Books & comics." if book else
                    f"On Plex now. Start watching within {days} days to keep it." if days else "On Plex now.")
            self.post(who, f"{key}:ready", "ready", f"{what} is ready", body, "Open", href, media=media, poster=poster, now=now)
        elif state == "started":
            self.post(who, f"{key}:started", "started", f"{what} started downloading",
                      "It is on its way. The green bar at the top shows how far along it is.", "Track it", href,
                      media=media, poster=poster, now=now)
        elif state == "failed":
            self.post(who, f"{key}:failed", "problem", f"{what} could not be downloaded",
                      "Try again from My requests, or let the admin know.", "My requests", "#/requests/mine",
                      media=media, poster=poster, now=now)

    # ---------- comics, ebooks and audiobooks ----------
    async def books_tick(self, seeding: bool, now: int) -> None:
        if not (self.media and self.media.enabled()):
            return
        try:
            items = await self.media.admin_log(days=REQUEST_DAYS)
        except Exception as e:  # noqa: BLE001  (MediaError, or anything odd from the other site)
            log.info("Notifications: media site not answering: %s", e)
            return
        for x in items:
            pid = str((x.get("who") or {}).get("plex") or "")
            kind = x.get("kind")
            if not pid or kind not in ("comic", "ebook", "audiobook"):
                continue
            st = rs.notice_word(rs.from_shared(x.get("state"), x.get("label")))
            key = f"media:{x.get('ref') or (str(kind) + ':' + str(x.get('id')))}"
            if not self._moved(key, st, seeding, now):
                continue
            op = x.get("open")
            href = ("#/books" + op) if isinstance(op, str) and op.startswith("/") else "#/requests/mine"
            what = str(x.get("title") or "Your request") + ("" if kind == "comic" else f" ({kind})")
            self._post_request(f"plex:{pid}", key, st, what, kind, href, None, now)

    # ---------- billing ----------
    def billing_tick(self, now: int) -> None:
        if not (self.billing and self.store.get_setting("stripe_key")):
            # Stripe was disconnected: nobody can tell any more whether a failed payment was sorted out, so the
            # pinned "did not go through" notices are taken back rather than left pinned forever.
            for r in self.db.execute("SELECT who, key FROM notifications WHERE key IN ('billing:payment', 'billing:failed') "
                                     "AND resolved_at IS NULL").fetchall():
                self.withdraw(r["who"], r["key"])
            return
        status = self.billing.status_map()
        # Marked as paid by hand (a free month, or money taken outside Stripe): not behind on payment, so no
        # reminder to them and not on the admin's failed list until that period ends.
        manual = self.billing.manual_paid_map(now)
        bad = {pid: st for pid, st in status.items() if st in PAY_PROBLEM and pid not in manual}
        for pid, st in bad.items():
            last = (self.billing.summary(pid) or {}).get("last_payment") or {}
            when = f" on {self._day(last['created'])}" if last.get("status") == "failed" and last.get("created") else ""
            body = (f"Your last payment{when} was declined. Update your card with Greystar to keep watching."
                    if st != "incomplete" else "Your first payment has not gone through yet. Finish it with Greystar to start watching.")
            self.post(f"plex:{pid}", "billing:payment", "billing", "Your payment did not go through", body,
                      "See subscription", "#/subscription", urgent=True, now=now)
        for who in [r["who"] for r in self.db.execute(
                "SELECT who FROM notifications WHERE key = 'billing:payment' AND resolved_at IS NULL")]:
            if who.split(":", 1)[1] not in bad:
                self.resolve(who, "billing:payment", "Your payment went through", "Thanks. Your subscription is active again.", now)
        if bad:
            names = []
            for pid in sorted(bad):
                u = self.directory.get_user(pid) if self.directory else None
                names.append((u or {}).get("username") or (u or {}).get("title") or pid)
            shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
            n = len(bad)
            self.post(ADMINS, "billing:failed", "billing", f"{n} payment{'s' if n != 1 else ''} failed",
                      f"{shown}: past due or unpaid in Stripe.", "Open Billing", "settings:billing", urgent=True, now=now)
        else:
            self.resolve(ADMINS, "billing:failed", "Every payment is up to date", "Nobody is past due in Stripe any more.", now)

    # ---------- admins ----------
    def admin_tick(self, now: int) -> None:
        # requests on the Needs you list
        if self.seerr:
            flags = self.seerr.help.needs_you()
            if flags:
                titles = [f.get("title") or "A request" for f in flags[:2]]
                more = f" and {len(flags) - 2} more" if len(flags) > 2 else ""
                n = len(flags)
                self.post(ADMINS, "needs-you", "needs", f"{n} request{'s' if n != 1 else ''} need{'' if n != 1 else 's'} you",
                          ", ".join(titles) + more + ".", "Open Needs you", "settings:reqlog", urgent=True, now=now)
            else:
                self.resolve(ADMINS, "needs-you")
        # the nightly database copy (Glenn's pick K2A): pinned while the last one failed, closed by the next good one
        if self.backups:
            st = self.backups.status()
            if st.get("error"):
                self.post(ADMINS, "backup:failed", "backup", "The nightly database copy failed",
                          str(st["error"])[:200], "Open Backups", "settings:backups", urgent=True, now=now)
            else:
                for old in self.open_keys(ADMINS, "backup:"):
                    self.resolve(ADMINS, old, "The database copy is working again", "The last nightly copy was made.", now)

        # servers that stopped answering
        if self.poller:
            rows = {str(s["id"]): s for s in self.store.list_servers()}
            names = {k: s["name"] for k, s in rows.items() if s["enabled"]}
            # A server deleted or turned off while it was down is no longer checked, so its notice would never clear.
            for old in self.open_keys(ADMINS, "server:"):
                sid = old.split(":")[1]
                if sid in names:
                    continue
                if sid in rows:
                    self.resolve(ADMINS, old, f"{rows[sid]['name']} is turned off",
                                 "It was turned off in Settings while it was down, so it is no longer checked.", now)
                else:
                    self.withdraw(ADMINS, old)
            for sid, rec in dict(self.poller.since).items():
                name = names.get(str(sid))
                if not name:
                    continue
                since = int(rec.get("since") or now)
                key = f"server:{sid}:{since}" if not rec.get("online") else None
                if key:
                    self.post(ADMINS, key, "server", f"{name} is down", f"Not answering since {self._clock(since)}.", "See servers",
                              "#/servers", urgent=True, now=now)
                for old in self.open_keys(ADMINS, f"server:{sid}:"):
                    if old == key:
                        continue
                    went = int(old.rsplit(":", 1)[1])
                    back = since if rec.get("online") else now
                    mins = max(1, round((back - went) / 60))
                    took = f"{mins} minute{'s' if mins != 1 else ''}" if mins < 120 else f"{round(mins / 60)} hours"
                    self.resolve(ADMINS, old, f"{name} was down for {took}", f"Back up at {self._clock(back)}.", now)
        # Unwatched cleanup: titles due in the next 3 days
        if self.cleanup and self.cleanup.enabled():
            n = self.cleanup.due_count()
            key = "cleanup:" + dt.datetime.fromtimestamp(now, self._tz() or dt.timezone.utc).strftime("%Y-%m-%d")
            if n:
                tail = ("Review only is on, so nothing is removed yet." if self.cleanup.mode() == "review"
                        else "They are removed at the nightly run unless you keep them.")
                self.post(ADMINS, key, "cleanup", f"{n} title{'s are' if n != 1 else ' is'} due for removal",
                          f"Nobody has started {'them' if n != 1 else 'it'}. {tail}", "Open Cleanup", "#/cleanup", now=now)
            for old in self.open_keys(ADMINS, "cleanup:"):
                if old != key or not n:
                    self.resolve(ADMINS, old)
        else:
            for old in self.open_keys(ADMINS, "cleanup:"):   # cleanup turned off: nothing is due any more
                self.resolve(ADMINS, old)

    async def issues_tick(self, now: int) -> None:
        """Reported issues (Glenn, Nov 16 2026: "those are important and should be in the list"). Every open Seerr
        issue is pinned in the admins' list until it is resolved, then it says so. Unlike requests there is no silent
        first run: an open issue is still work to do, so the ones already open when this started are shown too."""
        if not (self.seerr and self.seerr.configured()):
            return
        try:
            res = await self.seerr.client().call("GET", "/issue", params={"take": 100, "skip": 0, "sort": "added", "filter": "open"})
        except SeerrError as e:
            log.info("Notifications: Seerr issues not answering: %s", e.message)
            return   # never close notices because Seerr did not answer
        rows = [i for i in (res or {}).get("results") or [] if i.get("status") in (None, 1)]
        live = set()
        for i in rows:
            key = f"issue:{i['id']}"
            live.add(key)
            m = i.get("media") or {}
            kind = m.get("mediaType") or "movie"
            tmdb = m.get("tmdbId")
            info = await self.seerr.title_of(kind, int(tmdb)) if tmdb else {}
            what = info.get("title") or "A title"
            s, e = i.get("problemSeason") or 0, i.get("problemEpisode") or 0
            what += f", season {s} episode {e}" if s and e else f", season {s}" if s else ""
            sort = ISSUE_TYPES.get(i.get("issueType"), "other").lower()
            sort = "" if sort == "other" else sort + " "
            by = (i.get("createdBy") or {}).get("displayName") or "Someone"
            said = next((c.get("message") for c in i.get("comments") or [] if c.get("message")), "") or ""
            said = said.strip()
            self.post(ADMINS, key, "issue", f"{by} reported a{'n' if sort[:1] in ('a', 'e', 'i', 'o', 'u') else ''} {sort}problem with {what}",
                      (f"\u201c{said[:200]}\u201d" if said else "No details given."), "Open issues", "#/requests/issues",
                      urgent=True, media=kind, poster=info.get("poster"), now=now)
        for old in self.open_keys(ADMINS, "issue:"):
            if old in live:
                continue
            row = self.db.execute("SELECT title FROM notifications WHERE who = ? AND key = ?", (ADMINS, old)).fetchone()
            what = (row["title"].split(" problem with ", 1)[-1] if row else "a title")
            self.resolve(ADMINS, old, f"Issue resolved: {what}", "It was marked resolved.", now)


def poster_url(path: str | None) -> str | None:
    """A TMDB poster path as Seerr gives it ("/abc.jpg"), as a small poster address (the page's CSP allows TMDB)."""
    if not path or not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return None
    return IMAGES.rstrip("/") + "/w185" + path


def _stamp(v: Any) -> float:
    try:
        t = dt.datetime.fromisoformat(str(v or "").replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return 0.0
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


# ---------- sample mode (no Plex server added yet): made-up notifications, kept in memory only ----------
_sample_seen: dict[str, set] = {}
_sample_shown: dict[str, int] = {}


def sample_items(p, now: float | None = None) -> dict:
    now = int(now or time.time())
    reader = reader_of(p)
    seen = _sample_seen.setdefault(reader, set())
    rows = [
        (1, "billing", True, "Your payment did not go through", "Your last payment on Nov 12 was declined. Update your card with Greystar to keep watching.",
         "See subscription", "#/subscription", None, None, now - 3 * 3600),
        (2, "ready", False, "Airwolf, season 3 is ready", "On Plex now. Start watching within 14 days to keep it.", "Open", "#/requests/series/1736",
         "tv", None, now - 2 * 3600),
        (3, "ready", False, "Other Mommy (4K) is ready", "On Plex now. Start watching within 14 days to keep it.", "Open", "#/requests/movie/1",
         "movie", None, now - 20 * 3600),
        (4, "started", False, "Dead-End Job, season 1 started downloading", "It is on its way. The green bar at the top shows how far along it is.",
         "Track it", "#/requests/series/2", "tv", None, now - 30 * 3600),
        (5, "ready", False, "The Martian (audiobook) is ready", "Ready in Books & comics.", "Open", "#/books", "audiobook", None, now - 40 * 3600),
    ]
    if p.is_admin:
        rows = [
            (11, "billing", True, "3 payments failed", "dkramer, tonyb and maria.l: past due or unpaid in Stripe.", "Open Billing", "settings:billing",
             None, None, now - 5 * 3600),
            (12, "needs", True, "2 requests need you", "Severance, The Long Walk.", "Open Needs you", "settings:reqlog", None, None, now - 6 * 3600),
            (13, "server", False, "Atlas was down for 14 minutes", "Back up at 3:42 AM.", None, "#/servers", None, None, now - 7 * 3600),
            (14, "cleanup", False, "4 titles are due for removal", "Nobody has started them. Review only is on, so nothing is removed yet.",
             "Open Cleanup", "#/cleanup", None, None, now - 8 * 3600),
        ] + rows[1:3]
    items = [{"id": i, "kind": k, "title": t, "body": b, "action": a, "href": h, "media": m, "poster": po, "at": at, "urgent": u,
              "pinned": u, "resolved": False, "seen": i in seen} for i, k, u, t, b, a, h, m, po, at in rows]
    unseen = sum(1 for x in items if not x["seen"])
    pinned = sum(1 for x in items if x["pinned"])
    shown = _sample_shown.get(reader)
    welcome = bool(unseen or pinned) and (shown is None or now - shown > WELCOME_GAP)
    return {"items": items, "unseen": unseen, "pinned": pinned, "since": now - 26 * 3600, "welcome": welcome, "now": now, "sample": True}


class SeenIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=200)


def make_router(notifier: Notifier, me: Callable[[Request], Any], is_sample: Callable[[], bool] = lambda: False) -> APIRouter:
    r = APIRouter(prefix="/api/notifications")

    @r.get("")
    def list_notifications(request: Request):
        """This person's notifications, newest first, open urgent ones first. `welcome`: open the Welcome back sheet."""
        p = me(request)
        return sample_items(p) if is_sample() else notifier.items_for(p)

    @r.post("/seen")
    def seen(body: SeenIn, request: Request):
        """Marks some seen, or (no ids) all of them and this visit as the last one."""
        p = me(request)
        if is_sample():
            ids = body.ids if body.ids is not None else [x["id"] for x in sample_items(p)["items"]]
            _sample_seen.setdefault(reader_of(p), set()).update(int(i) for i in ids)
            return {"ok": True, "seen": len(ids)}
        n = notifier.mark_seen(p, body.ids)
        return {"ok": True, "seen": n}

    @r.post("/shown")
    def shown(request: Request):
        """The page opened the Welcome back sheet on its own."""
        p = me(request)
        if is_sample():
            _sample_shown[reader_of(p)] = int(time.time())
        else:
            notifier.shown(p)
        return {"ok": True}

    return r
