"""Where a request stands: worked out once, in one set of words (Glenn's pick O1A, Nov 15 2026).

Before this, six parts of the site each worked out where a movie or show request stood, each in its own words: the
Request log, My requests, Recently requested, the notifications, Unwatched cleanup and the download progress. They
drifted: the title page said "On Plex" (it asks Plex itself, app/onplex.py) while the "is ready" notice and the
cleanup clock waited for Seerr's own scan, sometimes until 3 AM. Now:

- `stage_of` turns a Seerr request, plus what Radarr/Sonarr and Plex say, into ONE stage (`STAGES`).
- `WORDS` holds every view's words for each stage: the admin Request log (label, tone, filter group), My requests
  (the shared words the media site uses too: work, release, follow, ready, problem), Recently requested (four plain
  words, each with a shape), notifications (waiting, started, ready, failed) and the page's own badges and filters.
- `RequestStatus` asks Radarr/Sonarr and Plex the way the title pages do. The notifier and Unwatched cleanup use it,
  so a title is ready for them the moment it is on Plex, not when Seerr catches up (Glenn's pick 4A).
- `vocabulary()` sends the page the labels it shows (filters, badges, the hub, episode stages), so no page script
  keeps its own copy.

Comics, ebooks and audiobooks come from the media site already in the shared words; `from_shared` maps them onto the
same stages.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .arr import ArrError
from .seerr import MEDIA_STATUS, REQUEST_STATUS

log = logging.getLogger("telemetry.request_state")

# Every stage a request can be in, in the order it moves (the last four only appear in the admin Request log).
STAGES = ("waiting", "approved", "release", "downloading", "importing", "partial", "ready", "following",
          "declined", "failed", "canceled", "removed", "gone", "unknown")

# Radarr/Sonarr queue stages (app/arr.py `progress_word`) that mean a copy is on its way, and that it is being added.
DOWNLOADING_STAGES = ("downloading", "queued", "paused", "retry")
IMPORTING_STAGES = ("importing", "imported")
STARTED_STAGES = DOWNLOADING_STAGES + IMPORTING_STAGES

# Each view's words for each stage.
#   log:    (label, tone, filter group) in the admin Request log
#   member: (shared word, label) in My requests; the shared words are the media site's too
#   notice: what a notification announces (only forward moves are told)
WORDS: dict[str, dict[str, Any]] = {
    "waiting":     {"log": ("Waiting for approval", "warning", "waiting"), "member": ("work", "Waiting for approval"), "notice": "waiting"},
    "approved":    {"log": ("Approved", "info", "approved"), "member": ("work", "Searching"), "notice": "waiting"},
    "release":     {"log": ("Waiting for release", "warning", "approved"), "member": ("release", "Waiting for release"), "notice": "waiting"},
    "downloading": {"log": ("Downloading", "info", "approved"), "member": ("work", "Downloading"), "notice": "started"},
    "importing":   {"log": ("Adding to Plex", "info", "approved"), "member": ("work", "Adding to Plex"), "notice": "started"},
    "partial":     {"log": ("Partly available", "success", "available"), "member": ("ready", "Partly available"), "notice": "waiting"},
    "ready":       {"log": ("Available", "success", "available"), "member": ("ready", "Available"), "notice": "ready"},
    "following":   {"log": ("Following", "success", "available"), "member": ("follow", "Following"), "notice": "waiting"},
    "declined":    {"log": ("Declined", "danger", "problem"), "member": ("hidden", "Declined"), "notice": "declined"},
    "failed":      {"log": ("Failed", "danger", "problem"), "member": ("problem", "Failed"), "notice": "failed"},
    "canceled":    {"log": ("Canceled by them", "neutral", "ended"), "member": ("hidden", "Canceled"), "notice": "declined"},
    "removed":     {"log": ("Removed from their list", "neutral", "ended"), "member": ("hidden", "Removed"), "notice": "declined"},
    "gone":        {"log": ("No longer in Seerr", "neutral", "ended"), "member": ("hidden", "No longer in Seerr"), "notice": "declined"},
    "unknown":     {"log": ("Unknown (Seerr not answering)", "neutral", "all"), "member": ("work", "Unknown"), "notice": "waiting"},
}
MEMBER_NOTES = {"failed": "Something went wrong getting it.", "partial": "Some of it is on Plex; the rest is on its way."}

# The page's own words, sent once with /api/requests/me (`vocabulary`).
MEMBER_FILTERS = [("all", "Any status"), ("work", "In progress"), ("release", "Waiting for release"), ("follow", "Following"),
                  ("ready", "Ready"), ("problem", "Needs attention")]
MEMBER_TONES = {"ready": "success", "work": "info", "release": "neutral", "follow": "neutral", "problem": "danger"}
LOG_GROUPS = [("all", "All"), ("waiting", "Waiting for approval"), ("approved", "Approved"), ("available", "Available"),
              ("problem", "Declined or failed"), ("ended", "Canceled or removed"), ("unreleased", "Not out yet")]
# Seerr's own request and media statuses, for the request rows (approvals, title pages, the Seerr-only list).
REQUEST_BADGES = {"pending": ("warning", "Pending approval"), "approved": ("info", "Approved"), "declined": ("danger", "Declined"),
                  "failed": ("danger", "Failed"), "completed": ("success", "Available")}
MEDIA_BADGES = {"available": ("success", "Available"), "partial": ("info", "Partly available"), "processing": ("info", "Processing"),
                "pending": ("warning", "Requested"), "blocklisted": ("danger", "Blocked"), "deleted": ("neutral", "Removed")}
SEERR_FILTERS = [("all", "All"), ("pending", "Pending"), ("approved", "Approved"), ("processing", "Processing"),
                 ("available", "Available"), ("failed", "Failed")]
# The viewer home's Requests hub: which stages go in which group.
HUB = [{"id": "ready", "label": "Ready to watch", "shape": "circle", "filter": "available", "stages": ["ready"]},
       {"id": "way", "label": "On its way", "shape": "triangle", "filter": "processing",
        "stages": ["approved", "release", "downloading", "importing", "partial"]},
       {"id": "wait", "label": "Waiting for approval", "shape": "square", "filter": "pending", "stages": ["waiting"]},
       {"id": "failed", "label": "Failed", "shape": "square", "filter": "failed", "stages": ["failed"]}]
# A show's episodes on the title page (app/arr.py episode stages): label and tint.
EPISODE_STAGES = [("onplex", "On Plex", "lime"), ("importing", "Importing", "sky"), ("downloading", "Downloading", "volt"),
                  ("queued", "Queued", "grey"), ("problem", "Needs a look", "danger"), ("searching", "Looking for a copy", "ring"),
                  ("release", "Not out yet", "ring")]


def vocabulary() -> dict:
    """Every request word the page shows, so page scripts type none of their own."""
    stages = {st: [MEMBER_TONES.get(w["member"][0], "neutral"), w["member"][1]] for st, w in WORDS.items()}
    return {"stages": stages, "filters": MEMBER_FILTERS, "tones": MEMBER_TONES, "logGroups": LOG_GROUPS,
            "request": {k: list(v) for k, v in REQUEST_BADGES.items()}, "media": {k: list(v) for k, v in MEDIA_BADGES.items()},
            "seerrFilters": SEERR_FILTERS, "hub": HUB, "episodes": EPISODE_STAGES}


# ---------- the one decision ----------
def asked_seasons(q: dict) -> set[int]:
    return {int(s["seasonNumber"]) for s in q.get("seasons") or [] if s.get("seasonNumber")}


def seasons_on_plex(q: dict, is4k: bool) -> bool:
    """A show request whose every asked-for season Seerr already has as available."""
    asked = asked_seasons(q)
    have = {s.get("seasonNumber") for s in (q.get("media") or {}).get("seasons") or []
            if MEDIA_STATUS.get(s.get("status4k" if is4k else "status")) == "available"}
    return bool(asked) and asked <= have


def stage_of(q: dict | None, *, is4k: bool | None = None, downloading: bool | None = None, wait: dict | None = None,
             arr_stage: str | None = None, on_plex: bool = False) -> str:
    """One Seerr request's stage. `downloading`: Seerr's own download flag (default: read from the request).
    `wait`: why an approved request has not downloaded (seerr.release_wait). `arr_stage`: what Radarr/Sonarr say.
    `on_plex`: Plex itself has it (app/onplex.py), which beats Seerr when Seerr is behind."""
    if not q:
        return "unknown"
    rs = REQUEST_STATUS.get(q.get("status"))
    if rs == "declined":
        return "declined"
    if rs == "failed":
        return "failed"
    four = bool(q.get("is4k")) if is4k is None else bool(is4k)
    m = q.get("media") or {}
    ms = MEDIA_STATUS.get(m.get("status4k" if four else "status"))
    kind = q.get("type") or m.get("mediaType") or "movie"
    if on_plex or ms == "available" or rs == "completed":
        return "ready"
    if kind == "tv" and ms == "partial" and seasons_on_plex(q, four):
        return "ready"   # the show is partly on Plex, but every season this request asked for is
    if rs == "pending":
        return "waiting"
    if arr_stage in IMPORTING_STAGES:
        return "importing"
    if downloading is None:
        downloading = bool(m.get("downloadStatus4k" if four else "downloadStatus"))
    if downloading or arr_stage in DOWNLOADING_STAGES:
        return "downloading"
    if ms == "partial":
        return "partial"
    if (wait or {}).get("reason") in ("unreleased", "airing"):
        return "release"
    return "approved"


def from_shared(state: str | None, label: str | None) -> str:
    """The media site's shared words (comics, ebooks, audiobooks) as a stage."""
    label = str(label or "")
    if state == "work":
        if label == "Waiting for approval":
            return "waiting"
        if label.startswith("Adding"):
            return "importing"
        return "downloading" if label.startswith("Downloading") else "approved"
    return {"release": "release", "follow": "following", "ready": "ready", "problem": "failed",
            "declined": "declined", "cancelled": "canceled"}.get(state or "", "approved")


def log_words(stage: str) -> tuple[str, str, str]:
    return WORDS.get(stage, WORDS["unknown"])["log"]


def member_words(stage: str, wait: dict | None = None) -> dict:
    """My requests: {state, label, note}. A series that is airing says Airing instead of Waiting for release."""
    state, label = WORDS.get(stage, WORDS["unknown"])["member"]
    if stage == "release" and (wait or {}).get("reason") == "airing":
        label = "Airing"
    return {"state": state, "label": label, "note": MEMBER_NOTES.get(stage)}


def notice_word(stage: str) -> str:
    return WORDS.get(stage, WORDS["unknown"])["notice"]


def public_words(state: str, label: str | None) -> tuple[str, str]:
    """Recently requested shows a few plain words, each with its shape (never colour alone): Available or Following
    (circle), Downloading (triangle), Not out yet (ring), Searching (square). A failed or waiting request reads
    Searching there: what went wrong is the requester's business (My requests)."""
    label = label or ""
    if state == "ready":
        return ("Partly available" if label == "Partly available" else "Available"), "circle"
    if state == "follow":
        return "Following", "circle"
    if state == "release":
        return "Not out yet", "ring"
    if state == "work" and (label.startswith("Downloading") or label.startswith("Adding")):
        return "Downloading", "triangle"
    return "Searching", "square"


# ---------- asking Radarr/Sonarr and Plex ----------
class RequestStatus:
    """What Radarr/Sonarr and Plex say about a request, the same way for the title pages, the notifier and Unwatched
    cleanup. Plex is only asked once Radarr or Sonarr has finished with every file (it costs nothing before)."""

    def __init__(self, seerr=None, arr=None, onplex=None):
        self.seerr, self.arr, self.onplex = seerr, arr, onplex

    async def movie_on_plex(self, tmdb: int, is4k: bool, prog: dict | None) -> float | None:
        """When Plex has the movie Radarr finished with (the time it first saw it), else None. The file's size and
        name come from Radarr and stay on this side: they identify the copy for Plex, and members never see them."""
        if not (self.onplex and prog and prog.get("stage") == "imported"):
            return None
        size = name = title = None
        if self.arr:
            try:
                size, name, title = await self.arr.imported_file(int(tmdb), is4k)
            except ArrError:
                pass
        return await self.onplex.movie(int(tmdb), is4k=is4k, size=size, file_name=name, title=title)

    async def show_on_plex(self, tvdb: int, tmdb: int | None, show: dict | None, is4k: bool) -> float | None:
        """When Plex has every asked-for episode Sonarr has finished with, else None."""
        if not (self.onplex and show):
            return None
        want = {(int(se["number"]), int(e["n"])) for se in show.get("seasons") or [] for e in se.get("episodes") or []
                if e.get("stage") == "onplex" and e.get("n") and se.get("number")}
        if not want or len(want) != int(show.get("total") or 0):
            return None
        title = None
        if self.arr:
            try:
                title = ((await self.arr.series(int(tvdb), is4k)) or {}).get("title")
            except ArrError:
                pass
        return await self.onplex.episodes(want, tvdb_id=int(tvdb), tmdb_id=tmdb, title=title, is4k=is4k)

    async def check(self, q: dict, gate: asyncio.Semaphore | None = None) -> dict:
        """{stage, plex_at}: one request's stage with Radarr/Sonarr and Plex asked. plex_at is when Plex itself got it,
        when that is how it became ready. Seerr's word alone when Radarr/Sonarr are not connected."""
        m = q.get("media") or {}
        is4k = bool(q.get("is4k"))
        base = stage_of(q)
        if base not in ("approved", "downloading", "partial") or not (self.arr and self.arr.configured()):
            return {"stage": base, "plex_at": None}
        kind = q.get("type") or m.get("mediaType") or "movie"
        tmdb = m.get("tmdbId")
        try:
            async with (gate or asyncio.Semaphore(1)):
                if kind == "movie":
                    if not tmdb:
                        return {"stage": base, "plex_at": None}
                    prog = await self.arr.movie_progress(int(tmdb), is4k)
                    at = await self.movie_on_plex(int(tmdb), is4k, prog)
                    return {"stage": stage_of(q, arr_stage=(prog or {}).get("stage"), on_plex=bool(at)), "plex_at": at}
                tvdb = m.get("tvdbId")
                if not tvdb:
                    return {"stage": base, "plex_at": None}
                show = await self.arr.show_progress(int(tvdb), sorted(asked_seasons(q)), is4k)
                at = await self.show_on_plex(int(tvdb), tmdb, show, is4k)
                eps = {e.get("stage") for se in (show or {}).get("seasons") or [] for e in se.get("episodes") or []}
                started = next((s for s in DOWNLOADING_STAGES + ("importing",) if s in eps), None)
                return {"stage": stage_of(q, arr_stage=started, on_plex=bool(at)), "plex_at": at}
        except ArrError:
            return {"stage": base, "plex_at": None}
