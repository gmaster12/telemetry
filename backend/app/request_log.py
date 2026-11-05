"""Request log for administrators (Settings, Request log).

Every request made through this site is saved in `request_log` when it is made (who, what, when).
Its state now (waiting, approved, downloading, available, declined, failed, canceled, removed) comes
from Seerr's own list, read as Seerr's administrator and cached for a minute. Earlier requests can be
imported once from Seerr for the people linked to it (only those made after they were linked).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass
from typing import Any

from .seerr import MEDIA_STATUS, REQUEST_STATUS, Requests, SeerrError

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
}
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
    }


async def build(svc: Requests, directory) -> tuple[list[dict], str | None]:
    """Every logged request, newest first, with its state now. Returns (rows, error)."""
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
    return out, error


def matches(row: dict, q: str, kind: str, group: str) -> bool:
    if kind in ("movie", "tv") and row["kind"] != kind:
        return False
    if group not in ("", "all") and row["group"] != group:
        return False
    if q:
        hay = " ".join(str(x or "") for x in (row["who"]["name"], row["who"]["username"], row["who"]["email"], row["title"], row["year"])).lower()
        return all(w in hay for w in q.lower().split())
    return True


def counts(rows: list[dict]) -> dict:
    out = {g: 0 for g in GROUPS}
    out["all"] = len(rows)
    for r in rows:
        if r["group"] in out and r["group"] != "all":
            out[r["group"]] += 1
    return out


def to_csv(rows: list[dict], tz: dt.tzinfo) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Time", "Day", "Name", "Username", "Email", "Type", "Title", "Year", "Seasons", "4K", "Status", "Decided by", "Seerr request", "Logged"])
    for r in rows:
        t = dt.datetime.fromtimestamp(r["at"], tz)
        w.writerow([t.strftime("%Y-%m-%d"), t.strftime("%H:%M"), t.strftime("%A"), r["who"]["name"], r["who"]["username"] or "",
                    r["who"]["email"] or "", "Movie" if r["kind"] == "movie" else "Series", r["title"], r["year"],
                    " ".join(str(n) for n in r["seasons"]), "yes" if r["is4k"] else "", r["label"],
                    (r["decided"] or {}).get("by") or "", r["request"] or "", "imported" if r["source"] == "imported" else "when made"])
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
