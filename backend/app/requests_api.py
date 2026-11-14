"""Requests pages: what the browser asks for, answered from Seerr as the signed-in person.

Every route needs the `requests` section. Seerr itself decides what each person may do (request,
4K, advanced options, approve), because each call is made as their own Seerr user.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .arr import ArrError
from .seerr import MEDIA_STATUS, REQUEST_STATUS, Requests, SeerrError, has, release_wait

log = logging.getLogger("telemetry.requests")

# Seerr's built-in studio and network rows (src/components/Discover/StudioSlider, NetworkSlider).
STUDIOS = [("Disney", 2, "/wdrCwmRnLFJhEoH8GSfymY85KHT.png"), ("20th Century Studios", 127928, "/h0rjX5vjW5r8yEnUBStFarjcLT4.png"),
           ("Sony Pictures", 34, "/GagSvqWlyPdkFHMfQ3pNq6ix9P.png"), ("Warner Bros. Pictures", 174, "/ky0xOc5OrhzkZ1N6KyUxacfQsCk.png"),
           ("Universal", 33, "/8lvHyhjr8oUKOOy2dKXoALWKdp0.png"), ("Paramount", 4, "/fycMZt242LVjagMByZOLUGbCvv3.png"),
           ("Pixar", 3, "/1TjvGVDMYsj6JBxOAkUHpPEwLf7.png"), ("Dreamworks", 521, "/kP7t6RwGz2AvvTkvnI1uteEwHet.png"),
           ("Marvel Studios", 420, "/hUzeosd33nzE5MCNsZxCGEKTXaQ.png"), ("DC", 9993, "/2Tc1P3Ac8M479naPp1kYT3izLS5.png"),
           ("A24", 41077, "/1ZXsGaFPgrgS6ZZGS37AqD5uU12.png")]
NETWORKS = [("Netflix", 213, "/wwemzKWzjKYJFfCeiB57q3r4Bcm.png"), ("Disney+", 2739, "/gJ8VX6JSu3ciXHuC2dDGAo2lvwM.png"),
            ("Prime Video", 1024, "/ifhbNuuVnlwYy5oXA5VIb2YR8AZ.png"), ("Apple TV+", 2552, "/4KAy34EHvRM25Ih8wb82AuGU7zJ.png"),
            ("Hulu", 453, "/pqUTCleNUiTLAVlelGxUgWn1ELh.png"), ("HBO", 49, "/tuomPhY2UtuPTqqFnKMVHvSb724.png"),
            ("Discovery+", 4353, "/1D1bS3Dyw4ScYnFWTlBOvJXC3nb.png"), ("ABC", 2, "/ndAvF4JLsliGreX87jAc9GdjmJY.png"),
            ("FOX", 19, "/1DSpHrWyOORkL9N2QHX7Adt31mQ.png"), ("Cinemax", 359, "/6mSHSquNpfLgDdv6VnOOvC5Uz2h.png"),
            ("AMC", 174, "/pmvRmATOCaDykE6JrVoeYxlFHw3.png"), ("Showtime", 67, "/Allse9kbjiP6ExaQrnSpIhkurEi.png"),
            ("Starz", 318, "/8GJjw3HHsAJYwIWKIPBPfqMxlEa.png"), ("The CW", 71, "/ge9hzeaU7nMtQ4PjkFlc68dGAJ9.png"),
            ("NBC", 6, "/o3OedEP0f9mfZr33jz2BfXOUK5.png"), ("CBS", 16, "/nm8d7P7MJNiBLdgIzUK0gkuEA4r.png"),
            ("Paramount+", 4330, "/fi83B1oztoS47xxcemFdPMhIzK.png"), ("BBC One", 4, "/mVn7xESaTNmjBUyUtGNvDQd3CT1.png"),
            ("Cartoon Network", 56, "/c5OC6oVCg6QP4eqzW6XIq17CQjI.png"), ("Adult Swim", 80, "/9AKyspxVzywuaMuZ1Bvilu8sXly.png"),
            ("Nickelodeon", 13, "/ikZXxg6GnwpzqiZbRPhJGaZapqB.png"), ("Peacock", 3353, "/gIAcGTjKKr0KOHL5s4O36roJ8p7.png")]

# Seerr's default Discover rows, used if its slider settings cannot be read.
DEFAULT_SLIDERS = [(1, "Recently Added"), (2, "Recent Requests"), (3, "Plex Watchlist"), (4, "Trending"), (5, "Popular Movies"),
                   (6, "Movie Genres"), (7, "Upcoming Movies"), (8, "Studios"), (9, "Popular Series"), (10, "Series Genres"),
                   (11, "Upcoming Series"), (12, "Networks")]

FAST, SLOW, LONG = 60, 600, 6 * 3600  # cache seconds: statuses, lists, titles
IMAGES = os.environ.get("SEERR_IMAGE_BASE", "https://image.tmdb.org/t/p/")  # TMDB posters, as Seerr shows them


class RequestIn(BaseModel):
    mediaType: str = Field(pattern="^(movie|tv)$")
    mediaId: int
    tvdbId: int | None = None
    seasons: list[int] | str | None = None
    is4k: bool = False
    serverId: int | None = None
    profileId: int | None = None
    rootFolder: str | None = None
    languageProfileId: int | None = None
    agreeRemoval: bool = False  # the "removed if not watched within 14 days" box (Unwatched cleanup)


class IssueIn(BaseModel):
    issueType: int = Field(ge=1, le=4)
    message: str = Field(min_length=1, max_length=2000)
    mediaId: int  # Seerr's media id (not the TMDB id)
    problemSeason: int | None = Field(default=None, ge=0)
    problemEpisode: int | None = Field(default=None, ge=0)


class CommentIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class WatchIn(BaseModel):
    tmdbId: int
    mediaType: str = Field(pattern="^(movie|tv)$")
    title: str = Field(max_length=300)


# ---------- shaping ----------
def year(r: dict) -> str:
    return str(r.get("releaseDate") or r.get("firstAirDate") or "")[:4]


def media_state(mi: dict | None) -> dict:
    mi = mi or {}
    return {"status": MEDIA_STATUS.get(mi.get("status"), "unknown"), "status4k": MEDIA_STATUS.get(mi.get("status4k"), "unknown"),
            "mediaId": mi.get("id")}  # no Plex links: Glenn removed Play on Plex


def card(r: dict) -> dict | None:
    """One poster tile."""
    t = r.get("mediaType") or ("tv" if "firstAirDate" in r or ("name" in r and "title" not in r) else "movie")
    if t == "person":
        return {"kind": "person", "id": r.get("id"), "title": r.get("name"), "photo": r.get("profilePath"),
                "knownFor": [x.get("title") or x.get("name") for x in (r.get("knownFor") or [])][:3]}
    if t not in ("movie", "tv") or not r.get("id"):
        return None
    return {"kind": t, "id": r["id"], "title": r.get("title") or r.get("name"), "year": year(r), "poster": r.get("posterPath"),
            "backdrop": r.get("backdropPath"), "overview": r.get("overview") or "", "vote": r.get("voteAverage"),
            **media_state(r.get("mediaInfo"))}


def page_of(res: dict | None, items: list) -> dict:
    res = res or {}
    page = res.get("page") or (res.get("pageInfo") or {}).get("page") or 1
    pages = res.get("totalPages") or (res.get("pageInfo") or {}).get("pages") or 1
    return {"items": [i for i in items if i], "page": page, "totalPages": pages, "more": page < pages}


EVERYTHING_PAGE = 30
BOOK_KINDS = ("comic", "ebook", "audiobook")
BOOK_ACTIONS = ("retry", "notify", "cancel", "stop", "watch", "unwatch")


def unify_seerr(x: dict, wait: dict | None) -> dict:
    """One Seerr request (a req_row) in My requests' shared words (the media site's feed uses the same ones):
    work (In progress), release (Waiting for release), follow, ready, problem (Needs attention), or hidden."""
    tv = x.get("kind") == "tv"
    seasons = x.get("seasons") or []
    sub = ", ".join(p for p in [x.get("year") or "", "4K" if x.get("is4k") else "",
                                ("Season " if len(seasons) == 1 else "Seasons ") + ", ".join(str(n) for n in seasons) if seasons else ""] if p) or None
    out = {"key": f"seerr:{x['id']}", "source": "seerr", "kind": "show" if tv else "movie", "id": x["id"], "tmdbId": x.get("tmdbId"),
           "title": x.get("title") or "Unknown title", "sub": sub, "poster": x.get("poster"), "created": x.get("created"),
           "sortAt": x.get("created"), "progress": None, "wait": None, "note": None, "seasons": seasons,
           "open": f"#/requests/{'series' if tv else 'movie'}/{x.get('tmdbId')}",
           "canRetry": bool(x.get("canRetry")), "canRemove": bool(x.get("canRemove")), "is4k": bool(x.get("is4k"))}
    status, media = x.get("status"), (x.get("media") or {}).get("status")
    if status == "declined":
        return {**out, "state": "hidden", "label": "Declined"}
    if status == "failed":
        return {**out, "state": "problem", "label": "Failed", "note": "Something went wrong getting it."}
    if media == "available" or status == "completed":
        return {**out, "state": "ready", "label": "Available"}
    if media == "partial":
        return {**out, "state": "ready", "label": "Partly available", "note": "Some of it is on Plex; the rest is on its way."}
    if status == "pending":
        return {**out, "state": "work", "label": "Waiting for approval"}
    if x.get("downloading"):
        return {**out, "state": "work", "label": "Downloading"}
    w = wait or {"reason": "searching"}
    if w.get("reason") in ("unreleased", "airing"):
        return {**out, "state": "release", "label": "Airing" if w.get("reason") == "airing" else "Waiting for release", "wait": w}
    return {**out, "state": "work", "label": "Searching", "wait": w}


RECENT_DAYS, RECENT_SHOWN = 7, 80
READY_SECONDS = 30 * 60      # the top bar says a finished download "is ready" for this long
DOWNLOADS_SECONDS = 15        # one person's download list is reused this long
DOWNLOADS_MAX = 6
ACTIVE_STAGES = ("downloading", "queued", "paused", "importing", "retry", "problem")


def public_status(state: str, label: str | None) -> tuple[str, str]:
    """The viewer home's Recently requested shows a few plain words, each with its shape (never colour alone):
    Available or Following (circle), Downloading (triangle), Not out yet (ring), Searching (square). A failed or
    waiting request reads Searching there: what went wrong is the requester's business (My requests)."""
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


def make_router(svc: Requests, directory, need: Callable[[Request, str], Any], media=None, cleanup=None, arr=None,
                onplex=None) -> APIRouter:
    r = APIRouter(prefix="/api/requests")

    STALE = ("processing", "pending", "partial")   # Seerr has not caught up with Plex (yet, or at all)

    def iso(when: float) -> str:
        return dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async def plex_movie_at(tmdb: int, is4k: bool, prog: dict | None) -> float | None:
        """Plex's own answer for a movie Radarr has finished with: is that file in the library?

        Only asked once the download is done (`imported`), so a title still downloading costs nothing.
        The file's size and name come from Radarr and stay on this side: they identify the copy for
        Plex, and they are release names, which members never see."""
        if not (onplex and prog and prog.get("stage") == "imported"):
            return None
        size = name = title = None
        if arr:
            try:
                size, name, title = await arr.imported_file(int(tmdb), is4k)
            except ArrError:
                pass
        return await onplex.movie(int(tmdb), is4k=is4k, size=size, file_name=name, title=title)

    async def plex_media_state(mi: dict, kind: str, tmdb_id: int, title: str | None) -> dict:
        """`media_state`, plus Plex's own answer while Seerr still says a request is being worked on.

        Shows are left to Seerr here (a series is available only when every asked-for season is, which
        the progress route works out from Sonarr); their episodes still get Plex's answer there."""
        st = media_state(mi)
        if not (onplex and mi and tmdb_id and kind == "movie"):
            return st
        async def one(is4k: bool) -> None:
            field = "status4k" if is4k else "status"
            if st[field] not in STALE:
                return
            if await onplex.movie(int(tmdb_id), is4k=is4k, title=title):
                st[field] = "available"
                st["plexDirect"] = True
        await asyncio.gather(one(False), one(True))
        return st

    async def plex_has_episodes(tvdb: int, tmdb: int | None, show: dict | None, is4k: bool) -> float | None:
        """Plex's own answer for a show: are all the asked-for episodes Sonarr has finished with there?

        Only asked when Sonarr has a file for every episode of the asked-for seasons, so a season still
        coming in is left alone and costs nothing."""
        if not (onplex and show):
            return None
        want = {(int(se["number"]), int(e["n"])) for se in show.get("seasons") or [] for e in se.get("episodes") or []
                if e.get("stage") == "onplex" and e.get("n") and se.get("number")}
        if not want or len(want) != int(show.get("total") or 0):
            return None
        title = None
        if arr:
            try:
                title = ((await arr.series(int(tvdb), is4k)) or {}).get("title")
            except ArrError:
                pass
        return await onplex.episodes(want, tvdb_id=int(tvdb), tmdb_id=tmdb, title=title, is4k=is4k)

    async def who(request: Request) -> tuple[Any, int | None]:
        p = need(request, "requests")
        try:
            return p, await svc.seerr_user(p, directory)
        except SeerrError as e:
            raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)

    async def run(coro):
        try:
            return await coro
        except SeerrError as e:
            svc.last_error = e.message if e.status >= 500 else svc.last_error
            raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)

    async def title_info(kind: str, tmdb: int) -> dict:
        """Title, year and poster for a request or recently added item (Seerr's own cards do this too)."""
        try:
            d = await svc.get(f"/{'movie' if kind == 'movie' else 'tv'}/{int(tmdb)}", ttl=LONG)
        except SeerrError:
            return {"kind": kind, "id": tmdb, "title": "Unknown title", "year": "", "poster": None, "backdrop": None}
        return {"kind": kind, "id": tmdb, "title": d.get("title") or d.get("name"), "year": year(d), "poster": d.get("posterPath"),
                "backdrop": d.get("backdropPath")}

    async def me_info(uid: int | None) -> dict:
        me = await svc.get("/auth/me", user=uid, ttl=FAST)
        return me or {}

    def req_row(q: dict, info: dict, me_id: int | None, manage: bool) -> dict:
        m = q.get("media") or {}
        by = q.get("requestedBy") or {}
        mine = by.get("id") == me_id
        status = REQUEST_STATUS.get(q.get("status"), "unknown")
        return {**info, "tmdbId": info.get("id"), "id": q.get("id"), "status": status, "is4k": bool(q.get("is4k")), "kind": q.get("type") or m.get("mediaType"),
                "created": q.get("createdAt"), "updated": q.get("updatedAt"),
                "seasons": sorted(s.get("seasonNumber") for s in (q.get("seasons") or []) if s.get("seasonNumber") is not None),
                "by": {"id": by.get("id"), "name": by.get("displayName") or by.get("plexUsername") or by.get("username"), "avatar": by.get("avatar")},
                "modifiedBy": (q.get("modifiedBy") or {}).get("displayName"), "mine": mine,
                "canCancel": (mine and status == "pending") or manage, "canDecide": manage and status == "pending",
                "canRetry": status == "failed" and (mine or manage), "canRemove": status == "failed" and (mine or manage),
                "downloading": bool(m.get("downloadStatus4k" if q.get("is4k") else "downloadStatus")),
                "media": {"status": MEDIA_STATUS.get(m.get("status4k" if q.get("is4k") else "status"), "unknown"), "tmdbId": m.get("tmdbId")}}

    def with_help(items: list[dict], manage: bool) -> list[dict]:
        """Glenn's rule on failed requests: one Try again, then Notify admin, then "Admin notified". Managers: no limit."""
        if manage:
            return items
        flags = svc.help.flags([x["id"] for x in items if x.get("status") == "failed" or x.get("state") == "problem"])
        for x in items:
            if x.get("status") != "failed" and not (x.get("source") == "seerr" and x.get("state") == "problem"):
                continue
            f = flags.get(x["id"]) or {}
            told = bool(f.get("notified_at")) and not f.get("handled_at")
            x.update(canRetry=not f.get("retried_at"), canNotify=bool(f.get("retried_at")) and not told, notified=told)
        return items

    async def waiting_reason(row: dict) -> dict:
        """Why an approved request has not downloaded yet: not out yet (with the date it comes out, from TMDB through
        Seerr), or out but no copy found yet (Radarr or Sonarr keep looking on their own)."""
        try:
            d = await svc.get(f"/{'movie' if row['kind'] == 'movie' else 'tv'}/{int(row['tmdbId'])}", ttl=SLOW)
        except SeerrError:
            return {"reason": "searching"}
        return release_wait(d, row["kind"], row.get("seasons"), dt.date.today().isoformat())

    async def requests_page(res: dict, me_id: int | None, manage: bool) -> dict:
        rows = (res or {}).get("results") or []
        infos = await asyncio.gather(*[title_info(q.get("type") or (q.get("media") or {}).get("mediaType") or "movie",
                                                  (q.get("media") or {}).get("tmdbId") or 0) for q in rows])
        pi = (res or {}).get("pageInfo") or {}
        return {"items": [req_row(q, i, me_id, manage) for q, i in zip(rows, infos)], "page": pi.get("page", 1),
                "totalPages": pi.get("pages", 1), "more": pi.get("page", 1) < pi.get("pages", 1)}

    # ---------- who am I, what can I do ----------
    @r.get("/me")
    async def me(request: Request):
        p = need(request, "requests")
        if not svc.configured():
            return {"connected": False, "admin": p.is_admin, "images": IMAGES}
        _, uid = await who(request)
        u = await run(me_info(uid))
        perms = int(u.get("permissions") or 0)
        pub = await run(svc.get("/settings/public", ttl=SLOW))
        quota = None
        try:
            quota = await svc.get(f"/user/{u.get('id')}/quota", user=uid, ttl=FAST)
        except SeerrError:
            pass
        manage = has(perms, "MANAGE_REQUESTS")
        pending = None
        if manage:
            try:
                pending = (await svc.get("/request/count", user=uid, ttl=FAST) or {}).get("pending")
            except SeerrError:
                pass
        return {
            "connected": True, "admin": p.is_admin, "pending": pending, "images": IMAGES,
            "user": {"id": u.get("id"), "name": u.get("displayName"), "avatar": u.get("avatar")},
            "can": {"movie": has(perms, "REQUEST", "REQUEST_MOVIE"), "tv": has(perms, "REQUEST", "REQUEST_TV"),
                    "movie4k": bool(pub.get("movie4kEnabled")) and has(perms, "REQUEST_4K", "REQUEST_4K_MOVIE"),
                    "tv4k": bool(pub.get("series4kEnabled")) and has(perms, "REQUEST_4K", "REQUEST_4K_TV"),
                    "advanced": has(perms, "REQUEST_ADVANCED", "MANAGE_REQUESTS"), "manage": manage,
                    "autoMovie": has(perms, "AUTO_APPROVE", "AUTO_APPROVE_MOVIE"), "autoTv": has(perms, "AUTO_APPROVE", "AUTO_APPROVE_TV"),
                    "issues": has(perms, "CREATE_ISSUES", "MANAGE_ISSUES"), "viewIssues": has(perms, "VIEW_ISSUES", "MANAGE_ISSUES", "CREATE_ISSUES"),
                    "manageIssues": has(perms, "MANAGE_ISSUES"), "recent": has(perms, "RECENT_VIEW"),
                    "viewRequests": has(perms, "REQUEST_VIEW", "MANAGE_REQUESTS")},
            "quota": quota,
            # Unwatched cleanup: when on, every movie or show request needs the tick box, and titles show days left.
            "cleanup": {"on": bool(cleanup and cleanup.enabled()), "days": cleanup.days() if cleanup else 14},
            "progress": bool(arr and arr.configured()),
            "settings": {"title": pub.get("applicationTitle") or "Seerr", "movie4k": bool(pub.get("movie4kEnabled")),
                         "series4k": bool(pub.get("series4kEnabled")), "partial": bool(pub.get("partialRequestsEnabled")),
                         "hideAvailable": bool(pub.get("hideAvailable")), "specials": bool(pub.get("enableSpecialEpisodes")),
                         "region": pub.get("discoverRegion") or "", "language": pub.get("originalLanguage") or ""},
        }

    # ---------- Discover ----------
    @r.get("/home")
    async def home(request: Request):
        need(request, "requests")
        try:
            sliders = await svc.get("/settings/discover", ttl=SLOW)
            rows = [{"id": s["id"], "type": s["type"], "title": s.get("title") or dict(DEFAULT_SLIDERS).get(s["type"], ""), "data": s.get("data")}
                    for s in sliders or [] if s.get("enabled")]
        except SeerrError:
            rows = [{"id": -t, "type": t, "title": n, "data": None} for t, n in DEFAULT_SLIDERS]
        for row in rows:  # Seerr's built-in rows keep its default English titles
            if not row["title"]:
                row["title"] = dict(DEFAULT_SLIDERS).get(row["type"], "More")
        return {"rows": rows,
                "studios": [{"name": n, "id": i, "logo": l} for n, i, l in STUDIOS],
                "networks": [{"name": n, "id": i, "logo": l} for n, i, l in NETWORKS]}

    def browse_spec(kind: str, data: str | None = None, page: int = 1) -> tuple[str, dict]:
        today = dt.date.today().isoformat()
        p: dict = {"page": page}
        if kind == "trending":
            return "/discover/trending", p
        if kind == "movies":
            return "/discover/movies", p
        if kind == "tv":
            return "/discover/tv", p
        if kind == "upcoming_movies":
            return "/discover/movies", {**p, "primaryReleaseDateGte": today}
        if kind == "upcoming_tv":
            return "/discover/tv", {**p, "firstAirDateGte": today}
        if kind == "movie_genre":
            return "/discover/movies", {**p, "genre": data}
        if kind == "tv_genre":
            return "/discover/tv", {**p, "genre": data}
        if kind == "movie_keyword":
            return "/discover/movies", {**p, "keywords": data}
        if kind == "tv_keyword":
            return "/discover/tv", {**p, "keywords": data}
        if kind == "studio":
            return f"/discover/movies/studio/{int(data)}", p
        if kind == "network":
            return f"/discover/tv/network/{int(data)}", p
        if kind == "search":
            return "/search", {**p, "query": data}
        if kind in ("movie_streaming", "tv_streaming"):
            region, _, providers = (data or ",").partition(",")
            return "/discover/" + ("movies" if kind == "movie_streaming" else "tv"), {**p, "watchRegion": region, "watchProviders": providers}
        raise HTTPException(404, "No such list.")

    SLIDER_KIND = {4: "trending", 5: "movies", 7: "upcoming_movies", 9: "tv", 11: "upcoming_tv", 13: "movie_keyword", 14: "movie_genre",
                   15: "tv_keyword", 16: "tv_genre", 17: "search", 18: "studio", 19: "network", 20: "movie_streaming", 21: "tv_streaming"}

    async def browse(kind: str, data: str | None, page: int, uid: int | None) -> dict:
        path, params = browse_spec(kind, data, page)
        res = await svc.get(path, params=params, ttl=SLOW)
        return {**page_of(res, [card(x) for x in (res or {}).get("results", [])]), "kind": kind, "data": data}

    @r.get("/row")
    async def row(request: Request, type: int, data: str | None = None, page: int = 1):
        """One Discover row, by Seerr's slider type."""
        _, uid = await who(request)
        if type == 1:  # recently added
            res = await run(svc.get("/media", user=uid, params={"filter": "allavailable", "take": 20, "sort": "mediaAdded"}, ttl=FAST))
            items = await asyncio.gather(*[title_info(m.get("mediaType") or "movie", m.get("tmdbId")) for m in (res or {}).get("results", []) if m.get("tmdbId")])
            states = [media_state(m) for m in (res or {}).get("results", []) if m.get("tmdbId")]
            return {"items": [{**i, **s} for i, s in zip(items, states)], "page": 1, "totalPages": 1, "more": False}
        if type == 2:  # recent requests (Seerr shows only your own unless you may view everyone's)
            res = await run(svc.get("/request", user=uid, params={"filter": "all", "take": 20, "sort": "added", "skip": 0}, ttl=FAST))
            u = await run(me_info(uid))
            out = await requests_page(res, u.get("id"), has(u.get("permissions"), "MANAGE_REQUESTS"))
            return {**out, "requests": True}
        if type == 3:  # watchlist
            return await watchlist(request, page)
        if type in (6, 10):  # genre tiles
            res = await run(svc.get(f"/discover/genreslider/{'movie' if type == 6 else 'tv'}", ttl=LONG))
            return {"genres": [{"id": g.get("id"), "name": g.get("name"), "backdrops": (g.get("backdrops") or [])[:1]} for g in res or []],
                    "kind": "movie_genre" if type == 6 else "tv_genre"}
        if type in (8, 12):
            return {"logos": True}
        kind = SLIDER_KIND.get(type)
        if not kind:
            raise HTTPException(404, "No such row.")
        return await run(browse(kind, data, page, uid))

    @r.get("/browse")
    async def browse_route(request: Request, kind: str, data: str | None = None, page: int = 1):
        _, uid = await who(request)
        return await run(browse(kind, data, max(1, min(page, 500)), uid))

    @r.get("/search")
    async def search(request: Request, q: str, page: int = 1):
        need(request, "requests")
        q = q.strip()[:200]
        if not q:
            return {"items": [], "page": 1, "totalPages": 1, "more": False}
        res = await run(svc.get("/search", params={"query": q, "page": page}, ttl=FAST))
        if page == 1 and not (res or {}).get("results"):
            svc.help.miss(q, "movies & shows")  # the words only, never who searched
        return page_of(res, [card(x) for x in (res or {}).get("results", [])])

    # ---------- titles ----------
    def detail_extra(d: dict) -> dict:
        crew = (d.get("credits") or {}).get("crew") or []
        cast = (d.get("credits") or {}).get("cast") or []
        vids = [v for v in d.get("relatedVideos") or [] if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
        return {"cast": [{"id": c.get("id"), "name": c.get("name"), "role": c.get("character"), "photo": c.get("profilePath")} for c in cast[:20]],
                "crew": [{"id": c.get("id"), "name": c.get("name"), "job": c.get("job")} for c in crew if c.get("job") in ("Director", "Screenplay", "Writer", "Producer", "Creator")][:6],
                "trailer": (vids[0].get("url") if vids else None)}

    def requests_on(mi: dict | None, me_id: int | None = None, manage: bool = False, watching: frozenset = frozenset()) -> list:
        """Requests on a title. Who made someone else's request is only sent to request managers (Glenn: privacy);
        everyone else sees "another member"."""
        out = []
        for q in (mi or {}).get("requests") or []:
            by = q.get("requestedBy") or {}
            mine = me_id is not None and by.get("id") == me_id
            out.append({"id": q.get("id"), "status": REQUEST_STATUS.get(q.get("status"), "unknown"), "is4k": bool(q.get("is4k")),
                        "seasons": sorted(s.get("seasonNumber") for s in (q.get("seasons") or []) if s.get("seasonNumber") is not None),
                        "by": {"id": by.get("id"), "name": by.get("displayName")} if (mine or manage) else None,
                        "mine": mine, "watching": q.get("id") in watching, "created": q.get("createdAt")})
        return out

    async def viewer(p, uid: int | None) -> tuple[int | None, bool, frozenset]:
        """This person's Seerr id, whether they may see who asked, and the requests they follow (Notify me too)."""
        try:
            u = await me_info(uid)
        except SeerrError:
            u = {}
        return u.get("id"), has(u.get("permissions"), "MANAGE_REQUESTS", "REQUEST_VIEW"), \
            frozenset(int(w["request_id"]) for w in svc.help.watched(p.subject))

    @r.get("/scores")
    async def scores(request: Request, ids: str = ""):
        """Ratings and box office for up to 60 titles: ids=movie:27205,tv:1396 (poster chips, sorting, About)."""
        need(request, "requests")
        keys = []
        for part in ids.split(",")[:60]:
            kind, _, tid = part.strip().partition(":")
            if kind in ("movie", "tv") and tid.isdigit():
                keys.append((kind, int(tid)))
        return {"items": await svc.scores(keys)}

    @r.get("/movie/{tmdb_id}")
    async def movie(request: Request, tmdb_id: int):
        p, uid = await who(request)
        d = await run(svc.get(f"/movie/{tmdb_id}", user=uid, ttl=FAST))
        me_id, manage, watching = await viewer(p, uid)
        mi = d.get("mediaInfo") or {}
        return {"kind": "movie", "id": d.get("id"), "title": d.get("title"), "year": year(d), "poster": d.get("posterPath"),
                "backdrop": d.get("backdropPath"), "overview": d.get("overview"), "tagline": d.get("tagline"), "runtime": d.get("runtime"),
                "genres": [{"id": g.get("id"), "name": g.get("name")} for g in d.get("genres") or []], "vote": d.get("voteAverage"),
                "released": d.get("releaseDate"), "status": d.get("status"), "imdbId": d.get("imdbId"),
                "certification": next((x.get("certification") for c in ((d.get("releases") or {}).get("results") or []) if c.get("iso_3166_1") == "US"
                                       for x in c.get("release_dates") or [] if x.get("certification")), None),
                "studios": [{"id": c.get("id"), "name": c.get("name")} for c in (d.get("productionCompanies") or [])[:3]],
                "collection": {"id": d["collection"]["id"], "name": d["collection"].get("name"), "poster": d["collection"].get("posterPath")} if d.get("collection") else None,
                "media": await plex_media_state(mi, "movie", tmdb_id, d.get("title")),
                "requests": requests_on(mi, me_id, manage, watching), "watchlisted": bool(d.get("onUserWatchlist")),
                **detail_extra(d)}

    @r.get("/tv/{tmdb_id}")
    async def tv(request: Request, tmdb_id: int):
        p, uid = await who(request)
        d = await run(svc.get(f"/tv/{tmdb_id}", user=uid, ttl=FAST))
        me_id, manage, watching = await viewer(p, uid)
        mi = d.get("mediaInfo") or {}
        have = {s.get("seasonNumber"): s for s in mi.get("seasons") or []}
        asked = {}
        for q in mi.get("requests") or []:
            st = REQUEST_STATUS.get(q.get("status"))
            if st in ("declined", "completed"):  # Seerr still counts a failed request as taking its seasons
                continue
            for s in q.get("seasons") or []:
                asked.setdefault((s.get("seasonNumber"), bool(q.get("is4k"))), st)
        seasons = []
        for s in d.get("seasons") or []:
            n = s.get("seasonNumber")
            h = have.get(n) or {}
            seasons.append({"number": n, "name": s.get("name"), "episodes": s.get("episodeCount"), "airDate": s.get("airDate"),
                            "poster": s.get("posterPath"), "status": MEDIA_STATUS.get(h.get("status"), "unknown"),
                            "status4k": MEDIA_STATUS.get(h.get("status4k"), "unknown"),
                            "requested": asked.get((n, False)), "requested4k": asked.get((n, True))})
        return {"kind": "tv", "id": d.get("id"), "title": d.get("name"), "year": year(d), "poster": d.get("posterPath"),
                "backdrop": d.get("backdropPath"), "overview": d.get("overview"), "tagline": d.get("tagline"),
                "genres": [{"id": g.get("id"), "name": g.get("name")} for g in d.get("genres") or []], "vote": d.get("voteAverage"),
                "firstAir": d.get("firstAirDate"), "lastAir": d.get("lastAirDate"), "status": d.get("status"),
                "networks": [{"id": n.get("id"), "name": n.get("name")} for n in (d.get("networks") or [])[:3]],
                "tvdbId": (d.get("externalIds") or {}).get("tvdbId"), "episodeRuntime": (d.get("episodeRunTime") or [None])[0],
                "certification": next((c.get("rating") for c in ((d.get("contentRatings") or {}).get("results") or []) if c.get("iso_3166_1") == "US"), None),
                "seasons": seasons, "media": media_state(mi), "requests": requests_on(mi, me_id, manage, watching), "watchlisted": bool(d.get("onUserWatchlist")),
                "next": (d.get("nextEpisodeToAir") or {}).get("airDate"), **detail_extra(d)}

    @r.get("/tv/{tmdb_id}/season/{number}")
    async def season(request: Request, tmdb_id: int, number: int):
        _, uid = await who(request)
        s = await run(svc.get(f"/tv/{tmdb_id}/season/{number}", user=uid, ttl=SLOW))
        return {"number": s.get("seasonNumber"), "name": s.get("name"), "overview": s.get("overview"),
                "episodes": [{"number": e.get("episodeNumber"), "name": e.get("name"), "airDate": e.get("airDate"), "overview": e.get("overview"),
                              "still": e.get("stillPath")} for e in s.get("episodes") or []]}

    @r.get("/{kind}/{tmdb_id}/more")
    async def more(request: Request, kind: str, tmdb_id: int, what: str = "recommendations", page: int = 1):
        _, uid = await who(request)
        if kind not in ("movie", "tv") or what not in ("recommendations", "similar"):
            raise HTTPException(404, "No such list.")
        res = await run(svc.get(f"/{kind}/{tmdb_id}/{what}", params={"page": page}, ttl=SLOW))
        return page_of(res, [card({**x, "mediaType": x.get("mediaType") or kind}) for x in (res or {}).get("results", [])])

    @r.get("/person/{person_id}")
    async def person(request: Request, person_id: int):
        _, uid = await who(request)
        d, cr = await asyncio.gather(run(svc.get(f"/person/{person_id}", ttl=LONG)), run(svc.get(f"/person/{person_id}/combined_credits", ttl=SLOW)))
        seen, items = set(), []
        for x in sorted((cr or {}).get("cast", []) + (cr or {}).get("crew", []), key=lambda x: -(x.get("popularity") or 0)):
            c = card(x)
            if c and (c["kind"], c["id"]) not in seen:
                seen.add((c["kind"], c["id"]))
                items.append(c)
        return {"id": d.get("id"), "name": d.get("name"), "photo": d.get("profilePath"), "bio": d.get("biography"),
                "born": d.get("birthday"), "died": d.get("deathday"), "place": d.get("placeOfBirth"), "known": d.get("knownForDepartment"),
                "items": items[:80]}

    @r.get("/collection/{collection_id}")
    async def collection(request: Request, collection_id: int):
        _, uid = await who(request)
        d = await run(svc.get(f"/collection/{collection_id}", user=uid, ttl=FAST))
        return {"id": d.get("id"), "name": d.get("name"), "overview": d.get("overview"), "poster": d.get("posterPath"),
                "backdrop": d.get("backdropPath"),
                "items": [c for c in (card({**x, "mediaType": "movie"}) for x in d.get("parts") or []) if c]}

    # ---------- asking for things ----------
    @r.post("/request")
    async def make_request(request: Request, body: RequestIn):
        p, uid = await who(request)
        u = await run(me_info(uid))
        b = body.model_dump(exclude_none=True)
        agreed = bool(b.pop("agreeRemoval", False))
        if cleanup and cleanup.enabled() and not agreed:
            raise HTTPException(422, f"Tick the box first: a title nobody starts watching within {cleanup.days()} days of "
                                     "arriving on Plex is removed.")
        # Never on someone else's behalf, never around quotas; advanced choices only if Seerr allows them.
        b.pop("userId", None)
        b.pop("ignoreQuota", None)
        if not has(u.get("permissions"), "REQUEST_ADVANCED", "MANAGE_REQUESTS"):
            for k in ("serverId", "profileId", "rootFolder", "languageProfileId"):
                b.pop(k, None)
        if b["mediaType"] == "movie":
            b.pop("seasons", None)
        elif not b.get("seasons"):
            raise HTTPException(422, "Pick at least one season.")
        res = await run(svc.send("POST", "/request", user=uid, body=b))
        if not isinstance(res, dict) or res.get("id") is None:
            # Seerr answers 202 "No seasons available to request" (and makes nothing) when every season picked is
            # already on Plex, partly on Plex, or has an open request, failed ones included. Say so, not "requested".
            raise HTTPException(409, "Seerr did not make a request: " + (
                "every season you picked is already on Plex, partly on Plex, or already requested."
                if b["mediaType"] == "tv" else "this title is already on Plex or already requested."))
        status = REQUEST_STATUS.get(res.get("status"), "pending")
        try:  # the admins' request log (Settings, Request log); never stops the request itself
            info = await title_info(b["mediaType"], b["mediaId"])
            seasons = sorted(s.get("seasonNumber") for s in ((res or {}).get("seasons") or []) if s.get("seasonNumber") is not None)
            svc.log_request(p, uid, (res or {}).get("id"), b["mediaType"], b["mediaId"], info,
                            seasons or (b.get("seasons") if isinstance(b.get("seasons"), list) else None), bool(b.get("is4k")), status)
        except Exception:  # noqa: BLE001
            log.exception("Could not add a request to the request log")
        if cleanup and agreed:
            try:  # Unwatched cleanup: its clock starts when it arrives on Plex
                info = await title_info(b["mediaType"], b["mediaId"])
                seasons = sorted(s.get("seasonNumber") for s in ((res or {}).get("seasons") or []) if s.get("seasonNumber") is not None)
                cleanup.track(request_id=int(res["id"]), kind=b["mediaType"], tmdb_id=int(b["mediaId"]), is4k=bool(b.get("is4k")),
                              seasons=seasons or (b.get("seasons") if isinstance(b.get("seasons"), list) else None),
                              info={"title": info.get("title"), "year": info.get("year"), "poster": info.get("poster")},
                              plex_id=p.subject if p.kind == "plex" else None, name=p.name)
            except Exception:  # noqa: BLE001
                log.exception("Could not start tracking a request for Unwatched cleanup")
        return {"ok": True, "status": status, "id": (res or {}).get("id")}

    @r.delete("/request/{request_id}")
    async def cancel_request(request: Request, request_id: int, failed: bool = False):
        """Cancel a pending request, or (failed=true) remove a failed one from the person's list."""
        _, uid = await who(request)
        if failed:
            await own_failed(uid, request_id)
            await run(svc.send("DELETE", f"/request/{request_id}", user=None))
            svc.log_end(request_id, "removed")
        else:
            await run(svc.send("DELETE", f"/request/{request_id}", user=uid))
            svc.log_end(request_id, "canceled")
        return {"ok": True}

    @r.post("/request/{request_id}/{decision}")
    async def decide(request: Request, request_id: int, decision: str):
        if decision == "retry":  # this route is matched first; the retry and notify routes below do the work
            return await retry_request(request, request_id)
        if decision == "notify":
            return await notify_admin(request, request_id)
        if decision == "notify-import":  # defined further down; this route is matched first
            return await notify_import(request, request_id)
        _, uid = await who(request)
        if decision not in ("approve", "decline"):
            raise HTTPException(404, "No such action.")
        await run(svc.send("POST", f"/request/{request_id}/{decision}", user=uid))
        return {"ok": True}

    @r.get("/attention")
    async def attention(request: Request):
        """My requests that failed (try again or remove) and approved ones still waiting for a download."""
        _, uid = await who(request)
        u = await run(me_info(uid))
        manage = has(u.get("permissions"), "MANAGE_REQUESTS")
        base = {"take": 50, "skip": 0, "sort": "added", "sortDirection": "desc", "requestedBy": u.get("id")}
        failed_res, open_res = await asyncio.gather(run(svc.get("/request", user=uid, params={**base, "filter": "failed"}, ttl=FAST)),
                                                    run(svc.get("/request", user=uid, params={**base, "filter": "unavailable"}, ttl=FAST)))
        failed = with_help((await requests_page(failed_res, u.get("id"), manage))["items"], manage)
        rows = (await requests_page(open_res, u.get("id"), manage))["items"]
        waiting = [x for x in rows if x["status"] == "approved" and not x["downloading"]
                   and x["media"]["status"] in ("processing", "partial", "pending", "unknown")]
        reasons = await asyncio.gather(*[waiting_reason(x) for x in waiting])
        return {"failed": failed, "waiting": [{**x, "wait": w} for x, w in zip(waiting, reasons)]}

    async def own_failed(uid: int | None, request_id: int) -> dict:
        """A failed request this person made. Seerr only lets people delete their own requests while pending and
        only request managers retry, so for these two actions the dashboard acts with its own Seerr key, after
        checking here that the request is theirs and has failed."""
        u = await run(me_info(uid))
        q = await run(svc.get(f"/request/{request_id}", user=uid))
        if REQUEST_STATUS.get((q or {}).get("status")) != "failed":
            raise HTTPException(409, "Only a failed request can be tried again or removed this way.")
        if not has(u.get("permissions"), "MANAGE_REQUESTS") and (q.get("requestedBy") or {}).get("id") != u.get("id"):
            raise HTTPException(403, "That is not your request.")
        return q

    async def flag_info(q: dict) -> dict:
        m = q.get("media") or {}
        kind = q.get("type") or m.get("mediaType") or "movie"
        info = await title_info(kind, m.get("tmdbId") or 0)
        return {"kind": kind, "tmdbId": m.get("tmdbId"), "title": info.get("title")}

    @r.post("/request/{request_id}/retry")
    async def retry_request(request: Request, request_id: int):
        """Send a failed request to Radarr or Sonarr again (Seerr's own retry). Glenn's rule: people get one try;
        after that, Notify admin. Request managers are not limited."""
        p, uid = await who(request)
        q = await own_failed(uid, request_id)
        manage = has((await run(me_info(uid))).get("permissions"), "MANAGE_REQUESTS")
        if not manage and (svc.help.flag(request_id) or {}).get("retried_at"):
            raise HTTPException(409, "You have already tried this one again. Use Notify admin and the admin will look at it.")
        res = await run(svc.send("POST", f"/request/{request_id}/retry", user=None))
        if not manage:
            svc.help.mark_retried(request_id, p.subject, await flag_info(q))
        return {"ok": True, "status": REQUEST_STATUS.get((res or {}).get("status"), "approved")}

    @r.post("/request/{request_id}/notify")
    async def notify_admin(request: Request, request_id: int):
        """It failed again after the one retry: put it on the admins' Needs you list."""
        p, uid = await who(request)
        q = await own_failed(uid, request_id)
        f = svc.help.flag(request_id) or {}
        if not f.get("retried_at"):
            raise HTTPException(409, "Try it again once first.")
        if f.get("notified_at") and not f.get("handled_at"):
            return {"ok": True, "already": True}
        svc.help.mark_notified(request_id, p.subject, await flag_info(q))
        return {"ok": True}

    # ---------- Notify me too: follow someone else's request (their name is never sent) ----------
    @r.post("/watch/{request_id}")
    async def watch(request: Request, request_id: int):
        p, uid = await who(request)
        u = await run(me_info(uid))
        q = await run(svc.get(f"/request/{request_id}"))  # as Seerr's admin: people cannot read others' requests
        if not isinstance(q, dict) or not q.get("id"):
            raise HTTPException(404, "No such request.")
        if (q.get("requestedBy") or {}).get("id") == u.get("id"):
            raise HTTPException(409, "This is your own request; it is already in My requests.")
        if REQUEST_STATUS.get(q.get("status")) == "declined":
            raise HTTPException(409, "That request is closed. You can request the title yourself.")
        svc.help.watch(request_id, p.subject, await flag_info(q))
        return {"ok": True, "watching": True}

    @r.delete("/watch/{request_id}")
    async def unwatch(request: Request, request_id: int):
        p, _ = await who(request)
        svc.help.unwatch(request_id, p.subject)
        return {"ok": True, "watching": False}

    # ---------- comics, ebooks and audiobooks in My requests: actions go to the media site, signed ----------
    @r.post("/books/{kind}/{item_id}/{action}")
    async def book_action(request: Request, kind: str, item_id: int, action: str):
        p = need(request, "requests")
        if kind not in BOOK_KINDS or action not in BOOK_ACTIONS or item_id < 1:
            raise HTTPException(404, "No such action.")
        if not (media and p.kind == "plex" and p.can("books") and media.enabled()):
            raise HTTPException(409, "Books & comics is not available for this sign-in.")
        try:
            await media.call("POST", f"/api/telemetry/requests/{kind}/{item_id}/{action}?sub={quote(str(p.subject), safe='')}")
        except Exception as e:  # noqa: BLE001  (MediaError carries the media site's own words)
            raise HTTPException(409, str(e) or "The media site did not answer.")
        finally:
            media.forget_member(p.subject)
        return {"ok": True}

    @r.get("/mine")
    async def mine(request: Request, filter: str = "all", page: int = 1, everyone: bool = False):
        _, uid = await who(request)
        if filter not in ("all", "approved", "available", "pending", "processing", "unavailable", "failed", "completed"):
            filter = "all"
        u = await run(me_info(uid))
        manage = has(u.get("permissions"), "MANAGE_REQUESTS")
        take = 20
        params = {"filter": filter, "take": take, "skip": (max(1, page) - 1) * take, "sort": "added", "sortDirection": "desc"}
        if not (everyone and has(u.get("permissions"), "MANAGE_REQUESTS", "REQUEST_VIEW")):
            params["requestedBy"] = u.get("id")
        res = await run(svc.get("/request", user=uid, params=params))
        return await requests_page(res, u.get("id"), manage)

    @r.get("/everything")
    async def everything(request: Request, cursor: str = "0.0", order: str = "desc"):
        """My requests, all of them in one list: movies and shows from Seerr merged with comics, ebooks and audiobooks
        from the media site (for Plex sign-ins with Books & comics), newest first (order=asc for oldest first).

        `cursor` is "<Seerr rows used>.<book items used>.<followed requests used>"; each answer gives the next one. When
        one side does not answer, the other still shows and `sources` says which: ok, down, or off (not set up / not for
        this person). Requests by others that this person follows (Notify me too) come along, never with the other
        person's name."""
        p = need(request, "requests")
        asc = order == "asc"
        try:
            parts = [max(0, int(v)) for v in cursor.split(".")][:3]
        except ValueError:
            parts = []
        s_skip, b_skip, w_skip = (parts + [0, 0, 0])[:3]
        page = EVERYTHING_PAGE
        sources: dict[str, str] = {}

        async def seerr_part() -> tuple[list[dict], bool]:
            if not svc.configured():
                sources["seerr"] = "off"
                return [], False
            try:
                uid = await svc.seerr_user(p, directory)
                u = await me_info(uid)
                manage = has(u.get("permissions"), "MANAGE_REQUESTS")
                res = await svc.get("/request", user=uid, ttl=FAST, params={"filter": "all", "take": page, "skip": s_skip, "sort": "added",
                                                                        "sortDirection": "asc" if asc else "desc", "requestedBy": u.get("id")})
                pg = await requests_page(res, u.get("id"), manage)
                rows = pg["items"]
                need_wait = [x for x in rows if x["status"] == "approved" and not x["downloading"]
                             and x["media"]["status"] in ("processing", "pending", "unknown")]
                waits = dict(zip([x["id"] for x in need_wait], await asyncio.gather(*[waiting_reason(x) for x in need_wait])))
                sources["seerr"] = "ok"
                return with_help([unify_seerr(x, waits.get(x["id"])) for x in rows], manage), bool(pg["more"])
            except (SeerrError, HTTPException) as e:
                log.info("My requests: Seerr part failed: %s", getattr(e, "message", None) or getattr(e, "detail", e))
                sources["seerr"] = "down"
                return [], False

        async def books_part() -> tuple[list[dict], bool]:
            if not (media and p.kind == "plex" and p.can("books") and media.enabled()):
                sources["books"] = "off"
                return [], False
            try:
                items = await media.member_requests(p.subject)
            except Exception as e:  # noqa: BLE001  (MediaError, or anything odd from the other site)
                log.info("My requests: media site part failed: %s", e)
                sources["books"] = "down"
                return [], False
            if asc:
                items = list(reversed(items))
            chunk = items[b_skip:b_skip + page]
            sources["books"] = "ok"
            return [{**x, "source": "books", "poster": None, "wait": None, "canRemove": False, "canRetry": bool(x.get("canRetry")),
                     "open": ("#/books" + x["open"]) if isinstance(x.get("open"), str) and x["open"].startswith("/") else None}
                    for x in chunk], len(items) > b_skip + page

        async def watched_part() -> tuple[list[dict], bool]:
            follows = svc.help.watched(p.subject) if svc.configured() else []
            if not follows:
                return [], False

            async def one(w: dict) -> dict | None:
                try:
                    q = await svc.get(f"/request/{int(w['request_id'])}", ttl=FAST)
                except SeerrError as e:
                    if e.status == 404:  # gone from Seerr: stop following it
                        svc.help.unwatch(w["request_id"], p.subject)
                    return None
                m = q.get("media") or {}
                info = await title_info(q.get("type") or m.get("mediaType") or "movie", m.get("tmdbId") or 0)
                row = req_row(q, info, None, False)
                wait = None
                if row["status"] == "approved" and not row["downloading"] and row["media"]["status"] in ("processing", "pending", "unknown"):
                    wait = await waiting_reason(row)
                at = dt.datetime.fromtimestamp(int(w["created"]), dt.timezone.utc).isoformat().replace("+00:00", "Z")
                item = unify_seerr(row, wait)
                return {**item, "key": f"seerr-watch:{row['id']}", "created": at, "sortAt": at, "watching": True,
                        "note": "Requested by another member" + (". " + item["note"] if item.get("note") else ""),
                        "canRetry": False, "canRemove": False}
            got = [x for x in await asyncio.gather(*[one(w) for w in follows]) if x]
            got.sort(key=lambda x: x["sortAt"], reverse=not asc)
            return got[w_skip:w_skip + page], len(got) > w_skip + page

        (seerr_items, seerr_more), (book_items, book_more), (watch_items, watch_more) = await asyncio.gather(
            seerr_part(), books_part(), watched_part())
        def when(x: dict) -> float:
            try:
                return dt.datetime.fromisoformat(str(x.get("sortAt") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
        merged = sorted(seerr_items + book_items + watch_items, key=lambda x: (when(x), x["key"]), reverse=not asc)
        taken = merged[:page]
        used_s = sum(1 for x in taken if x["source"] == "seerr" and not x.get("watching"))
        used_b = sum(1 for x in taken if x["source"] == "books")
        used_w = sum(1 for x in taken if x["source"] == "seerr" and x.get("watching"))
        more = seerr_more or book_more or watch_more or used_s < len(seerr_items) or used_b < len(book_items) or used_w < len(watch_items)
        if cleanup:
            for x in taken:
                if x.get("source") == "seerr" and x.get("tmdbId"):
                    x["clock"] = cleanup.clock_for("tv" if x["kind"] == "show" else "movie", int(x["tmdbId"]))
        return {"items": [x for x in taken if x["state"] != "hidden"], "cursor": f"{s_skip + used_s}.{b_skip + used_b}.{w_skip + used_w}",
                "more": more, "sources": sources}

    # ---------- Unwatched cleanup and download progress (Radarr/Sonarr) ----------
    def parse_ids(ids: str, cap: int = 30) -> list[tuple[str, int, bool]]:
        out = []
        for part in (ids or "").split(",")[:cap]:
            bits = part.strip().split(":")
            if len(bits) >= 2 and bits[0] in ("movie", "tv") and bits[1].isdigit():
                out.append((bits[0], int(bits[1]), len(bits) > 2 and bits[2] == "4k"))
        return out

    @r.get("/clocks")
    async def clocks(request: Request, ids: str = ""):
        """Days left before an unwatched title is removed, for titles on screen (movie:603,tv:1396). Never who asked."""
        need(request, "requests")
        if not cleanup:
            return {"items": {}}
        return {"items": {f"{k}:{i}": cleanup.clock_for(k, i) for k, i, _ in parse_ids(ids, 60)}}

    def steps_for(q: dict | None, m: dict, prog: dict | None, plex_at: float | None = None) -> list[dict]:
        """Requested, Approved, Copy found, Downloading, Importing, On Plex: done, now or next, with times.
        On Plex is done when Seerr says available OR Plex itself showed the file (`plex_at`)."""
        st = REQUEST_STATUS.get((q or {}).get("status"), "approved")
        stage = (prog or {}).get("stage")
        on_plex = MEDIA_STATUS.get(m.get("status")) == "available" or stage == "onplex"
        names = [("requested", "Requested"), ("approved", "Approved"), ("found", "Copy found"), ("downloading", "Downloading"),
                 ("importing", "Importing"), ("onplex", "On Plex")]
        if on_plex:
            cur = 6
        elif st == "pending":
            cur = 1
        elif stage in ("importing", "problem", "imported"):
            cur = 4
        elif stage in ("downloading", "queued", "paused", "retry"):
            cur = 3
        else:
            cur = 2
        at = {"requested": (q or {}).get("createdAt"), "approved": (q or {}).get("updatedAt") if st != "pending" else None,
              "found": (prog or {}).get("added"),
              # Plex's own time when it got there first: Seerr's mediaAddedAt is the request's, until its scan runs
              "onplex": iso(plex_at) if plex_at else m.get("mediaAddedAt")}
        return [{"key": k, "label": label, "state": "done" if i < cur else "now" if i == cur else "next", "at": at.get(k) if i <= cur else None}
                for i, (k, label) in enumerate(names)]

    @r.get("/progress")
    async def progress(request: Request, ids: str = ""):
        """Where requested titles are: Radarr/Sonarr queue and import state in plain words (movie:603, tv:1396, movie:603:4k).
        Movies get steps and download numbers; shows get every episode of the requested seasons."""
        _, uid = await who(request)
        if not (arr and arr.configured()):
            return {"arr": False, "items": {}}
        u = await run(me_info(uid))
        try:
            every = await svc.all_requests()
        except SeerrError:
            every = {}
        out: dict[str, Any] = {}

        async def one(kind: str, tmdb: int, is4k: bool) -> None:
            reqs = [q for q in every.values() if (q.get("media") or {}).get("tmdbId") == tmdb
                    and (q.get("type") or (q.get("media") or {}).get("mediaType")) == kind and bool(q.get("is4k")) == is4k
                    and REQUEST_STATUS.get(q.get("status")) != "declined"]
            reqs.sort(key=lambda q: q.get("createdAt") or "")
            q = reqs[-1] if reqs else None
            m = (q or {}).get("media") or {}
            mm = {"status": m.get("status4k" if is4k else "status"), "mediaAddedAt": m.get("mediaAddedAt")}
            mine = next((x["id"] for x in reversed(reqs) if (x.get("requestedBy") or {}).get("id") == u.get("id")), None)
            try:
                if kind == "movie":
                    prog = await arr.movie_progress(tmdb, is4k)
                    on_plex = MEDIA_STATUS.get(mm["status"]) == "available"
                    plex_at = None if on_plex else await plex_movie_at(tmdb, is4k, prog)
                    if plex_at:
                        prog = {**(prog or {}), "stage": "onplex", "word": "On Plex"}
                    item = {"kind": "movie", **(prog or {}), "onPlex": on_plex or bool(plex_at),
                            "steps": steps_for(q, mm, prog, plex_at), "mine": mine}
                else:
                    tvdb = m.get("tvdbId")
                    if not tvdb:
                        d = await run(svc.get(f"/tv/{tmdb}", ttl=SLOW))
                        tvdb = ((d or {}).get("externalIds") or {}).get("tvdbId")
                    seasons = sorted({s.get("seasonNumber") for x in reqs for s in (x.get("seasons") or []) if s.get("seasonNumber")})
                    show = await arr.show_progress(int(tvdb), seasons, is4k) if tvdb else None
                    on_plex = MEDIA_STATUS.get(mm["status"]) == "available"
                    if not on_plex and tvdb:
                        on_plex = bool(await plex_has_episodes(int(tvdb), tmdb, show, is4k))
                    item = {"kind": "tv", "show": show, "onPlex": on_plex, "mine": mine,
                            "requestedAt": (reqs[0] if reqs else {}).get("createdAt"), "seasonsAsked": seasons}
            except ArrError as e:
                item = {"kind": kind, "error": e.message}
            out[f"{kind}:{tmdb}" + (":4k" if is4k else "")] = item

        await asyncio.gather(*[one(k, i, f) for k, i, f in parse_ids(ids)])
        return {"arr": True, "items": out}

    @r.post("/request/{request_id}/notify-import")
    async def notify_import(request: Request, request_id: int):
        """A download that could not be added to Plex on its own: put it on the admins' Needs you list."""
        p, uid = await who(request)
        u = await run(me_info(uid))
        q = await run(svc.get(f"/request/{request_id}"))
        if not isinstance(q, dict) or not q.get("id"):
            raise HTTPException(404, "No such request.")
        if (q.get("requestedBy") or {}).get("id") != u.get("id") and not has(u.get("permissions"), "MANAGE_REQUESTS"):
            raise HTTPException(403, "That is not your request.")
        f = svc.help.flag(request_id) or {}
        if f.get("notified_at") and not f.get("handled_at"):
            return {"ok": True, "already": True}
        svc.help.mark_notified(request_id, p.subject, await flag_info(q))
        return {"ok": True}

    downloads_cache: dict[str, tuple[float, dict]] = {}

    def _stamp(v) -> float:
        try:
            t = dt.datetime.fromisoformat(str(v or "").replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return 0.0
        return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()

    fresh_at: dict[str, float] = {}

    @r.get("/downloads")
    async def downloads(request: Request, fresh: int = 0):
        """What this person asked for that is downloading now, or finished in the last 30 minutes, for the green bar at the
        top of every page (Glenn's pick A, Nov 12 2026). Movies and shows: their own Seerr requests with Radarr/Sonarr
        progress. Comics, ebooks and audiobooks: their own requests on the media site (Plex sign-ins with Books & comics).
        Only their own requests, never anyone else's; only plain words, never release names.

        {items: [{key, kind, title, pct, minutes, word, ready, open}], sources}"""
        p = need(request, "requests")
        # fresh=1: the person just asked for something (the Books frame said so). At most every 5 s per person.
        if fresh and time.time() - fresh_at.get(p.subject, 0) >= 5:
            fresh_at[p.subject] = time.time()
            downloads_cache.pop(p.subject, None)
            if media:
                media.forget_member(p.subject)
        hit = downloads_cache.get(p.subject)
        if hit and time.time() - hit[0] < DOWNLOADS_SECONDS:
            return hit[1]
        now = time.time()
        sources: dict[str, str] = {}

        async def seerr_part() -> list[dict]:
            if not (svc.configured() and arr and arr.configured()):
                sources["seerr"] = "off"
                return []
            try:
                uid = await svc.seerr_user(p, directory)
                u = await me_info(uid)
                res = await svc.get("/request", user=uid, ttl=FAST, params={"filter": "all", "take": 20, "skip": 0, "sort": "modified",
                                                                        "sortDirection": "desc", "requestedBy": u.get("id")})
            except (SeerrError, HTTPException) as e:
                log.info("Downloads: Seerr part failed: %s", getattr(e, "message", None) or getattr(e, "detail", e))
                sources["seerr"] = "down"
                return []
            rows = [q for q in (res or {}).get("results") or [] if isinstance(q, dict)
                    and (q.get("requestedBy") or {}).get("id") == u.get("id") and REQUEST_STATUS.get(q.get("status")) in ("approved", "completed")]
            seen: set[str] = set()

            async def one(q: dict) -> dict | None:
                m = q.get("media") or {}
                kind = q.get("type") or m.get("mediaType") or "movie"
                tmdb = m.get("tmdbId")
                is4k = bool(q.get("is4k"))
                key = f"{kind}:{tmdb}" + (":4k" if is4k else "")
                if not tmdb or key in seen:
                    return None
                seen.add(key)
                status = MEDIA_STATUS.get(m.get("status4k" if is4k else "status"))
                open_at = f"#/requests/{'movie' if kind == 'movie' else 'series'}/{int(tmdb)}"
                if status == "available":
                    if now - _stamp(m.get("mediaAddedAt") or m.get("updatedAt")) > READY_SECONDS:
                        return None
                    info = await title_info(kind, int(tmdb))
                    return {"key": key, "kind": kind, "title": info.get("title") or "Your request", "pct": 100, "minutes": None,
                            "word": "Ready", "ready": True, "open": open_at}
                if status not in ("processing", "partial", "pending"):
                    return None
                try:
                    if kind == "movie":
                        prog = await arr.movie_progress(int(tmdb), is4k)
                        if prog and prog.get("stage") == "imported":
                            # Radarr is done. Plex may already have it while Seerr is still scanning.
                            at = await plex_movie_at(int(tmdb), is4k, prog)
                            if not at or now - at > READY_SECONDS:
                                return None
                            info = await title_info(kind, int(tmdb))
                            return {"key": key, "kind": kind, "title": info.get("title") or "Your request", "pct": 100,
                                    "minutes": None, "word": "Ready", "ready": True, "open": open_at}
                        if not prog or prog.get("stage") not in ACTIVE_STAGES:
                            return None
                        pct, minutes, word = prog.get("pct"), prog.get("minutes"), prog.get("word")
                    else:
                        tvdb = m.get("tvdbId")
                        if not tvdb:
                            return None
                        seasons = sorted(s.get("seasonNumber") for s in (q.get("seasons") or []) if s.get("seasonNumber"))
                        show = await arr.show_progress(int(tvdb), seasons, is4k)
                        eps = [e for se in (show or {}).get("seasons") or [] for e in se.get("episodes") or [] if e.get("stage") in ACTIVE_STAGES]
                        if not eps:
                            return None
                        pct = round(sum(int(e.get("pct") or 0) for e in eps) / len(eps))
                        minutes = (show or {}).get("minutes")
                        word = f"{len(eps)} episode{'s' if len(eps) != 1 else ''} downloading"
                except ArrError:
                    return None
                info = await title_info(kind, int(tmdb))
                return {"key": key, "kind": kind, "title": info.get("title") or "Your request", "pct": pct, "minutes": minutes,
                        "word": word, "ready": False, "open": open_at}

            got = await asyncio.gather(*[one(q) for q in rows], return_exceptions=True)
            sources["seerr"] = "ok"
            return [x for x in got if isinstance(x, dict)]

        async def books_part() -> list[dict]:
            if not (media and p.kind == "plex" and p.can("books") and media.enabled()):
                sources["books"] = "off"
                return []
            try:
                items = await media.member_requests(p.subject)
            except Exception as e:  # noqa: BLE001  (MediaError, or anything odd from the other site)
                log.info("Downloads: media site part failed: %s", e)
                sources["books"] = "down"
                return []
            out = []
            for x in items:
                if x.get("watching"):
                    continue  # someone else's request: the bar is only for what this person asked for
                label = str(x.get("label") or "")
                kind = x.get("kind") if x.get("kind") in ("comic", "ebook", "audiobook") else "ebook"
                op = x.get("open")
                open_at = ("#/books" + op) if isinstance(op, str) and op.startswith("/") else "#/requests/mine"
                if x.get("state") == "ready":
                    if not x.get("doneAt") or now - _stamp(x.get("doneAt")) > READY_SECONDS:
                        continue
                    out.append({"key": str(x.get("key")), "kind": kind, "title": str(x.get("title") or ""), "pct": 100, "minutes": None,
                                "word": "Ready", "ready": True, "open": open_at})
                elif x.get("state") == "work" and (label.startswith("Downloading") or label.startswith("Adding")):
                    pr = x.get("progress")
                    out.append({"key": str(x.get("key")), "kind": kind, "title": str(x.get("title") or ""),
                                "pct": int(pr) if isinstance(pr, (int, float)) else (100 if label.startswith("Adding") else None),
                                "minutes": None, "word": label, "ready": False, "open": "#/requests/mine"})
            sources["books"] = "ok"
            return out

        a, b = await asyncio.gather(seerr_part(), books_part())
        items = a + b
        # Downloading first (furthest along first), then what just finished.
        items.sort(key=lambda x: (x["ready"], -(x["pct"] or 0)))
        body = {"items": items[:DOWNLOADS_MAX], "more": max(0, len(items) - DOWNLOADS_MAX), "sources": sources}
        downloads_cache[p.subject] = (time.time(), body)
        if len(downloads_cache) > 500:
            downloads_cache.pop(next(iter(downloads_cache)))
        return body

    @r.get("/recent")
    async def recent(request: Request, days: int = RECENT_DAYS):
        """Recently requested (the viewer home, Glenn's pick B): what everyone asked for in the last `days` days (1 to
        14), newest first. Movies and shows come from Seerr's full request list (as its admin, cached a minute), comics,
        ebooks and audiobooks from the media site (for Plex sign-ins with Books & comics). Nothing here says who asked:
        no names, ids or avatars. The same title asked for more than once shows once, with `times`."""
        p = need(request, "requests")
        days = max(1, min(14, int(days)))
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        sources: dict[str, str] = {}

        def stamp(v) -> float:
            try:
                t = dt.datetime.fromisoformat(str(v or "").replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                return 0.0
            return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()

        async def seerr_part() -> list[dict]:
            if not svc.configured():
                sources["seerr"] = "off"
                return []
            try:
                every = await svc.all_requests()
            except SeerrError as e:
                log.info("Recently requested: Seerr part failed: %s", e.message)
                sources["seerr"] = "down"
                return []
            rows = [q for q in every.values() if stamp(q.get("createdAt")) >= cutoff.timestamp()
                    and REQUEST_STATUS.get(q.get("status")) != "declined"]
            rows.sort(key=lambda q: stamp(q.get("createdAt")), reverse=True)
            rows = rows[:200]
            infos = await asyncio.gather(*[title_info(q.get("type") or (q.get("media") or {}).get("mediaType") or "movie",
                                                      (q.get("media") or {}).get("tmdbId") or 0) for q in rows])
            sources["seerr"] = "ok"
            out = []
            for q, info in zip(rows, infos):
                u = unify_seerr(req_row(q, info, None, False), None)
                if u["state"] != "hidden":
                    out.append({"kind": u["kind"], "title": u["title"], "sub": u.get("sub"), "poster": u.get("poster"),
                                "created": u.get("created"), "state": u["state"], "label": u.get("label"), "open": u.get("open"),
                                "clock": cleanup.clock_for(u["kind"], int(u["tmdbId"])) if cleanup and u.get("tmdbId") else None})
            return out

        async def books_part() -> list[dict]:
            if not (media and p.kind == "plex" and p.can("books") and media.enabled()):
                sources["books"] = "off"
                return []
            try:
                items = await media.recent_requests(days)
            except Exception as e:  # noqa: BLE001  (MediaError, or anything odd from the other site)
                log.info("Recently requested: media site part failed: %s", e)
                sources["books"] = "down"
                return []
            sources["books"] = "ok"

            def cover(x: dict) -> str | None:
                kind, _, num = str(x.get("ref") or "").partition(":")
                return f"/api/requests/recent/cover/{kind}/{int(num)}" if kind == x.get("kind") and num.isdigit() else None
            return [{"kind": x.get("kind"), "title": x.get("title") or "Untitled", "sub": x.get("sub"), "poster": cover(x),
                     "created": x.get("created"), "state": x.get("state"), "label": x.get("label"), "open": None}
                    for x in items if x.get("kind") in BOOK_KINDS and stamp(x.get("created")) >= cutoff.timestamp()]

        seerr_items, book_items = await asyncio.gather(seerr_part(), books_part())
        merged = sorted(seerr_items + book_items, key=lambda x: stamp(x.get("created")), reverse=True)
        seen: dict[tuple, dict] = {}
        items: list[dict] = []
        for x in merged:
            k = (x["kind"], str(x["title"]).strip().lower())
            if k in seen:
                seen[k]["times"] += 1
                continue
            word, shape = public_status(x["state"], x.get("label"))
            row = {"kind": x["kind"], "title": x["title"], "sub": x.get("sub"), "poster": x.get("poster"),
                   "created": x.get("created"), "status": word, "shape": shape, "times": 1, "open": x.get("open"),
                   "clock": x.get("clock")}
            seen[k] = row
            items.append(row)
        counts = {k: 0 for k in ("movie", "show", "ebook", "audiobook", "comic")}
        status: dict[str, int] = {}
        for x in items:
            counts[x["kind"]] = counts.get(x["kind"], 0) + 1
            status[x["status"]] = status.get(x["status"], 0) + 1
        return {"days": days, "total": len(items), "counts": counts, "status": status,
                "items": items[:RECENT_SHOWN], "more": len(items) > RECENT_SHOWN, "sources": sources}

    @r.get("/recent/cover/{kind}/{item_id}")
    async def recent_cover(request: Request, kind: str, item_id: int):
        """A comic, ebook or audiobook request's cover, fetched from the media site (signed) and served from here, so
        this site's CSP stays images-from-itself-and-TMDB. 404 when there is none: the page keeps the letter tile."""
        p = need(request, "requests")
        if kind not in BOOK_KINDS or item_id < 1 or not (media and p.kind == "plex" and p.can("books") and media.enabled()):
            raise HTTPException(404, "No cover.")
        body = await media.cover(kind, item_id)
        if not body:
            raise HTTPException(404, "No cover.")
        return Response(content=body, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

    @r.get("/services/{kind}")
    async def services(request: Request, kind: str, is4k: bool = False):
        """Servers, quality profiles and folders for the advanced options (people Seerr allows)."""
        _, uid = await who(request)
        svc_name = "radarr" if kind == "movie" else "sonarr"
        servers = [s for s in (await run(svc.get(f"/service/{svc_name}", user=uid, ttl=SLOW)) or []) if bool(s.get("is4k")) == is4k]
        out = []
        for s in servers:
            d = await run(svc.get(f"/service/{svc_name}/{s['id']}", user=uid, ttl=SLOW))
            out.append({"id": s["id"], "name": s.get("name"), "default": bool(s.get("isDefault")),
                        "profiles": [{"id": p.get("id"), "name": p.get("name")} for p in d.get("profiles") or []],
                        "folders": [{"path": f.get("path")} for f in d.get("rootFolders") or []],
                        "languages": [{"id": x.get("id"), "name": x.get("name")} for x in d.get("languageProfiles") or []],
                        "defaultProfile": (d.get("server") or {}).get("activeProfileId"), "defaultFolder": (d.get("server") or {}).get("activeDirectory"),
                        "defaultLanguage": (d.get("server") or {}).get("activeLanguageProfileId")})
        return {"servers": out}

    # ---------- watchlist ----------
    @r.get("/watchlist")
    async def watchlist(request: Request, page: int = 1):
        _, uid = await who(request)
        res = await run(svc.get("/discover/watchlist", user=uid, params={"page": page}, ttl=FAST))
        rows = (res or {}).get("results") or []
        infos = await asyncio.gather(*[title_info(x.get("mediaType") or "movie", x.get("tmdbId")) for x in rows if x.get("tmdbId")])
        details = []
        for i in infos:
            try:
                d = await svc.get(f"/{i['kind']}/{i['id']}", user=uid, ttl=FAST)
                details.append({**i, **media_state(d.get("mediaInfo"))})
            except SeerrError:
                details.append(i)
        return {**page_of(res, []), "items": details}

    @r.post("/watchlist")
    async def watch_add(request: Request, body: WatchIn):
        _, uid = await who(request)
        await run(svc.send("POST", "/watchlist", user=uid, body=body.model_dump()))
        return {"ok": True}

    @r.delete("/watchlist/{tmdb_id}")
    async def watch_remove(request: Request, tmdb_id: int):
        _, uid = await who(request)
        await run(svc.send("DELETE", f"/watchlist/{tmdb_id}", user=uid))
        return {"ok": True}

    # ---------- issues ----------
    def issue_row(i: dict, info: dict) -> dict:
        return {**info, "tmdbId": info.get("id"), "id": i.get("id"), "type": {1: "Video", 2: "Audio", 3: "Subtitles", 4: "Other"}.get(i.get("issueType"), "Other"),
                "status": "open" if i.get("status") == 1 else "resolved", "created": i.get("createdAt"), "updated": i.get("updatedAt"),
                "by": (i.get("createdBy") or {}).get("displayName"), "season": i.get("problemSeason"), "episode": i.get("problemEpisode"),
                "comments": [{"by": (c.get("user") or {}).get("displayName"), "message": c.get("message"), "at": c.get("createdAt")}
                             for c in i.get("comments") or []]}

    @r.get("/issues")
    async def issues(request: Request, filter: str = "open", page: int = 1):
        _, uid = await who(request)
        u = await run(me_info(uid))
        params = {"take": 20, "skip": (max(1, page) - 1) * 20, "sort": "added", "filter": filter if filter in ("all", "open", "resolved") else "open"}
        if not has(u.get("permissions"), "MANAGE_ISSUES"):
            params["requestedBy"] = u.get("id")
        res = await run(svc.get("/issue", user=uid, params=params))
        rows = (res or {}).get("results") or []
        infos = await asyncio.gather(*[title_info((i.get("media") or {}).get("mediaType") or "movie", (i.get("media") or {}).get("tmdbId") or 0) for i in rows])
        pi = (res or {}).get("pageInfo") or {}
        return {"items": [issue_row(i, inf) for i, inf in zip(rows, infos)], "page": pi.get("page", 1), "totalPages": pi.get("pages", 1),
                "more": pi.get("page", 1) < pi.get("pages", 1)}

    @r.post("/issue")
    async def new_issue(request: Request, body: IssueIn):
        _, uid = await who(request)
        res = await run(svc.send("POST", "/issue", user=uid, body=body.model_dump(exclude_none=True)))
        return {"ok": True, "id": (res or {}).get("id")}

    @r.post("/issue/{issue_id}/comment")
    async def comment(request: Request, issue_id: int, body: CommentIn):
        _, uid = await who(request)
        await run(svc.send("POST", f"/issue/{issue_id}/comment", user=uid, body=body.model_dump()))
        return {"ok": True}

    @r.post("/issue/{issue_id}/{state}")
    async def issue_state(request: Request, issue_id: int, state: str):
        _, uid = await who(request)
        if state not in ("resolved", "open"):
            raise HTTPException(404, "No such action.")
        await run(svc.send("POST", f"/issue/{issue_id}/{state}", user=uid))
        return {"ok": True}

    return r
