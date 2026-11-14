"""Request log for administrators (Settings, Request log).

Every movie and series request made through this site is saved in `request_log` when it is made (who, what,
when). Its state now (waiting, approved, downloading, available, declined, failed, canceled, removed) comes
from Seerr's own list, read as Seerr's administrator and cached for a minute. Earlier requests can be
imported once from Seerr for the people linked to it (only those made after they were linked).

Comic, ebook and audiobook requests come from the media site (`Media.admin_log`, signed, cached a minute) and
are merged in by time. Movies and series that are not out yet (TMDB's dates through Seerr, `release_wait`)
carry `unreleased` so the page can mark them.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
from dataclasses import dataclass
from typing import Any

from .seerr import MEDIA_STATUS, REQUEST_STATUS, Requests, SeerrError, release_wait

# state -> (label, tone, group). Groups are the filter chips.
STATES = {
    "waiting": ("Waiting for approval", "warning", "waiting"),
    "approved": ("Approved", "info", "approved"),
    "downloading": ("Downloading", "info", "approved"),
    "partial": ("Partly available", "success", "available"),
    "available": ("Available", "success", "available"),
    "declined": ("Declined", "danger", "problem"),
    "failed": ("Failed", "danger", "problem"),
    "canceled": ("Canceled by them", "neutral", "ended"),
    "removed": ("Removed from their list", "neutral", "ended"),
    "gone": ("No longer in Seerr", "neutral", "ended"),
    "unknown": ("Unknown (Seerr not answering)", "neutral", "all"),
    # comics, ebooks and audiobooks (the media site): the label is the media site's own word
    "release": ("Waiting for release", "warning", "approved"),
    "following": ("Following", "success", "available"),
}
BOOK_KINDS = ("comic", "ebook", "audiobook")
KINDS = ("movie", "tv") + BOOK_KINDS
KIND_NAMES = {"movie": "Movie", "tv": "Series", "comic": "Comic", "ebook": "Ebook", "audiobook": "Audiobook"}
WORKING = ("Downloading", "Adding")  # media site labels that mean a copy is on its way
GROUPS = ["all", "waiting", "approved", "available", "problem", "ended"]


@dataclass
class Who:
    """Stands in for a signed-in person when importing (what log_request reads)."""
    kind: str
    subject: str
    name: str | None
    email: str | None


def iso_to_epoch(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def state_of(row: dict, q: dict | None, known: bool) -> str:
    if row.get("ended"):
        return row["ended"]
    if q is None:
        return "gone" if known and row.get("seerr_request") else "unknown"
    st = REQUEST_STATUS.get(q.get("status"))
    if st == "pending":
        return "waiting"
    if st in ("declined", "failed"):
        return st
    m = q.get("media") or {}
    four = bool(row.get("is4k"))
    media = MEDIA_STATUS.get(m.get("status4k" if four else "status"))
    if media == "available":
        return "available"
    if media == "partial":
        return "partial"
    if m.get("downloadStatus4k" if four else "downloadStatus"):
        return "downloading"
    return "approved"


def shape(row: dict, q: dict | None, known: bool, person: dict | None) -> dict:
    s = state_of(row, q, known)
    label, tone, group = STATES[s]
    decided = None
    if q and REQUEST_STATUS.get(q.get("status")) in ("approved", "declined", "completed") and (q.get("modifiedBy") or {}).get("displayName"):
        decided = {"by": q["modifiedBy"]["displayName"], "at": iso_to_epoch(q.get("updatedAt"))}
    person = person or {}
    return {
        "id": row["id"], "at": row["at"], "request": row.get("seerr_request"), "source": row.get("source") or "site",
        "who": {"kind": row["who_kind"], "id": row["who_id"],
                "name": person.get("title") or row.get("who_name") or person.get("username") or row["who_id"],
                "username": person.get("username"), "email": person.get("email") or row.get("who_email"),
                "admin_login": row["who_kind"] == "local"},
        "kind": row["media_type"], "tmdb": row["tmdb_id"], "title": row.get("title") or "Unknown title", "year": row.get("year") or "",
        "poster": row.get("poster"), "seasons": [int(x) for x in (row.get("seasons") or "").split(",") if x.strip().isdigit()],
        "is4k": bool(row.get("is4k")), "state": s, "label": label, "tone": tone, "group": group, "decided": decided,
        "ended_at": row.get("ended_at"), "hint": "Waiting for a download" if s == "approved" else None,
        "sub": None, "ref": None, "open": None, "unreleased": None,
    }


def book_state(item: dict) -> str:
    """The media site's shared words (work, release, follow, ready, problem, declined, cancelled) as log states."""
    st, label = item.get("state"), str(item.get("label") or "")
    if st == "work":
        return "waiting" if label == "Waiting for approval" else "downloading" if label.startswith(WORKING) else "approved"
    return {"release": "release", "follow": "following", "ready": "available", "problem": "failed",
            "declined": "declined", "cancelled": "canceled"}.get(st, "approved")


def shape_book(item: dict, directory, people: dict) -> dict | None:
    """One comic, ebook or audiobook request from the media site, in the same shape as a movie or series row."""
    kind = item.get("kind")
    at = iso_to_epoch(item.get("created"))
    try:
        num = int(item.get("id") or str(item.get("ref") or "").split(":")[-1])
    except ValueError:
        return None
    if kind not in BOOK_KINDS or at is None:
        return None
    who = item.get("who") or {}
    plex = str(who.get("plex") or "") or None
    person = None
    if plex:
        if plex not in people:
            people[plex] = directory.get_user(plex)
        person = people[plex] or {}
    s = book_state(item)
    label, tone, group = STATES[s]
    if s in ("downloading", "approved", "failed", "waiting", "release") and item.get("label"):
        label = str(item["label"])[:60]
    username = (person or {}).get("username") or who.get("username")
    return {
        "id": f"media:{kind}:{num}", "at": at, "request": None, "source": "media",
        "who": {"kind": "plex" if plex else "media", "id": plex or who.get("username") or "",
                "name": (person or {}).get("title") or username or plex or "Someone", "username": username,
                "email": (person or {}).get("email") or who.get("email"), "admin_login": False},
        "kind": kind, "tmdb": None, "title": item.get("title") or "Untitled", "year": "", "sub": item.get("sub"),
        "poster": f"/api/settings/requests/log/cover/{kind}/{num}", "seasons": [], "is4k": False,
        "state": s, "label": label, "tone": tone, "group": group, "decided": None, "ended_at": None, "hint": None,
        "ref": f"{kind}:{num}", "open": item.get("open"), "unreleased": None,
    }


UNRELEASED_CHECK = ("waiting", "approved", "partial", "failed")  # states a not-yet-released title can be in


async def mark_unreleased(svc: Requests, rows: list[dict], today: str | None = None) -> None:
    """Sets `unreleased` ({"date", "what"}) on movie and series rows that are not out yet (TMDB through Seerr)."""
    today = today or dt.date.today().isoformat()
    gate = asyncio.Semaphore(6)

    async def one(r: dict) -> None:
        async with gate:
            try:
                d = await svc.get(f"/{'movie' if r['kind'] == 'movie' else 'tv'}/{int(r['tmdb'])}", ttl=600)
            except (SeerrError, ValueError, TypeError):
                return
        w = release_wait(d or {}, r["kind"], r.get("seasons"), today)
        if w.get("reason") == "unreleased":
            r["unreleased"] = {"date": w.get("date"), "what": w.get("what")}

    await asyncio.gather(*[one(r) for r in rows if r["kind"] in ("movie", "tv") and r.get("tmdb") and r["state"] in UNRELEASED_CHECK])


async def build(svc: Requests, directory, media=None, errors: dict | None = None) -> tuple[list[dict], str | None]:
    """Every logged request, newest first, with its state now: movies and series from the log and Seerr, comics,
    ebooks and audiobooks from the media site when Books & comics is on. Returns (rows, error). With `errors`, the
    media site's problem goes in errors["media"] instead of being added to the error."""
    rows = svc.log_rows()
    by_id: dict[int, dict] = {}
    known, error = False, None
    if rows and svc.configured():
        try:
            by_id = await svc.all_requests()
            known = True
        except SeerrError as e:
            error = e.message
    people: dict[str, dict | None] = {}
    out = []
    for r in rows:
        person = None
        if r["who_kind"] == "plex":
            if r["who_id"] not in people:
                people[r["who_id"]] = directory.get_user(r["who_id"])
            person = people[r["who_id"]]
        out.append(shape(r, by_id.get(int(r["seerr_request"])) if r.get("seerr_request") else None, known, person))
    if known:
        await mark_unreleased(svc, out)
    media_error = None
    if media is not None and media.enabled():
        try:
            items = await media.admin_log()
            out += [b for b in (shape_book(x, directory, people) for x in items) if b]
        except Exception as e:  # the media site not answering: movies and series still show
            media_error = str(e) or "The media site did not answer."
        out.sort(key=lambda r: (r["at"], str(r["id"])), reverse=True)
    if media_error and errors is not None:
        errors["media"] = media_error
    elif media_error:
        error = (error + " " if error else "") + "Media site: " + media_error
    return out, error


def matches(row: dict, q: str, kind: str, group: str) -> bool:
    if kind == "books" and row["kind"] not in BOOK_KINDS:
        return False
    if kind in KINDS and row["kind"] != kind:
        return False
    if group == "unreleased":
        return bool(row.get("unreleased"))
    if group not in ("", "all") and row["group"] != group:
        return False
    if q:
        hay = " ".join(str(x or "") for x in (row["who"]["name"], row["who"]["username"], row["who"]["email"], row["title"], row["year"],
                                              row.get("sub"))).lower()
        return all(w in hay for w in q.lower().split())
    return True


def counts(rows: list[dict]) -> dict:
    out = {g: 0 for g in GROUPS}
    out["all"] = len(rows)
    out["unreleased"] = 0
    for r in rows:
        if r["group"] in out and r["group"] != "all":
            out[r["group"]] += 1
        if r.get("unreleased"):
            out["unreleased"] += 1
    return out


def totals(rows: list[dict], now: float) -> dict:
    """The figures at the top of the log: requests in the last day, week, all time, and how many people."""
    return {"total": len(rows), "day": sum(1 for r in rows if r["at"] >= now - 86400),
            "week": sum(1 for r in rows if r["at"] >= now - 7 * 86400),
            "people": len({(r["who"]["kind"], r["who"]["id"]) for r in rows})}


def to_csv(rows: list[dict], tz: dt.tzinfo) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Time", "Day", "Name", "Username", "Email", "Type", "Title", "Year", "Author or publisher", "Seasons", "4K", "Status",
                "Not released yet", "Decided by", "Seerr request", "Logged"])
    for r in rows:
        t = dt.datetime.fromtimestamp(r["at"], tz)
        un = r.get("unreleased")
        w.writerow([t.strftime("%Y-%m-%d"), t.strftime("%H:%M"), t.strftime("%A"), r["who"]["name"], r["who"]["username"] or "",
                    r["who"]["email"] or "", KIND_NAMES.get(r["kind"], r["kind"]), r["title"], r["year"], r.get("sub") or "",
                    " ".join(str(n) for n in r["seasons"]), "yes" if r["is4k"] else "", r["label"],
                    ((un.get("date") or "yes") if un else ""), (r["decided"] or {}).get("by") or "", r["request"] or "",
                    {"imported": "imported", "media": "media site"}.get(r["source"], "when made")])
    return buf.getvalue()


async def import_earlier(svc: Requests, directory, title_info) -> dict:
    """Bring in requests made by linked people since they were linked (Seerr has no mark for where a request
    came from, so these may include a few made in Seerr itself). Requests already in the log are skipped."""
    added = 0
    for link in svc.links():
        since = int(link.get("created") or 0)
        u = directory.get_user(link["plex_id"]) or {}
        who = Who("plex", str(link["plex_id"]), u.get("title") or u.get("username"), u.get("email"))
        for page in range(20):
            res = await svc.client().call("GET", "/request", params={"take": 100, "skip": page * 100, "filter": "all", "sort": "added",
                                                                      "requestedBy": int(link["seerr_id"])})
            got = (res or {}).get("results") or []
            for q in got:
                if (q.get("requestedBy") or {}).get("id") not in (None, int(link["seerr_id"])):
                    continue
                at = iso_to_epoch(q.get("createdAt"))
                if at is None or at < since - 300:
                    continue
                m = q.get("media") or {}
                kind = q.get("type") or m.get("mediaType")
                if kind not in ("movie", "tv") or not m.get("tmdbId"):
                    continue
                info = await title_info(kind, int(m["tmdbId"]))
                seasons = sorted(s.get("seasonNumber") for s in (q.get("seasons") or []) if s.get("seasonNumber") is not None)
                before = svc.log_counts()["total"]
                svc.log_request(who, int(link["seerr_id"]), q.get("id"), kind, int(m["tmdbId"]), info, seasons or None, bool(q.get("is4k")),
                                REQUEST_STATUS.get(q.get("status")), at=at, source="imported")
                added += svc.log_counts()["total"] - before
            pages = ((res or {}).get("pageInfo") or {}).get("pages") or 1
            if not got or page + 1 >= pages:
                break
    svc.clear_cache()
    return {"added": added}


def page_of(rows: list[dict], page: int, size: int) -> dict[str, Any]:
    page = max(1, page)
    start = (page - 1) * size
    return {"rows": rows[start:start + size], "page": page, "more": start + size < len(rows), "matched": len(rows)}
