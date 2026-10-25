"""Requests pages: what the browser asks for, answered from Seerr as the signed-in person.

Every route needs the `requests` section. Seerr itself decides what each person may do (request,
4K, advanced options, approve), because each call is made as their own Seerr user.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .seerr import MEDIA_STATUS, REQUEST_STATUS, Requests, SeerrError, has

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
            "mediaId": mi.get("id"), "plexUrl": mi.get("mediaUrl"), "plexUrl4k": mi.get("mediaUrl4k")}


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


def make_router(svc: Requests, directory, need: Callable[[Request, str], Any]) -> APIRouter:
    r = APIRouter(prefix="/api/requests")

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
                "media": {"status": MEDIA_STATUS.get(m.get("status4k" if q.get("is4k") else "status"), "unknown"), "tmdbId": m.get("tmdbId")}}

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
        return page_of(res, [card(x) for x in (res or {}).get("results", [])])

    # ---------- titles ----------
    def detail_extra(d: dict) -> dict:
        crew = (d.get("credits") or {}).get("crew") or []
        cast = (d.get("credits") or {}).get("cast") or []
        vids = [v for v in d.get("relatedVideos") or [] if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
        return {"cast": [{"id": c.get("id"), "name": c.get("name"), "role": c.get("character"), "photo": c.get("profilePath")} for c in cast[:20]],
                "crew": [{"id": c.get("id"), "name": c.get("name"), "job": c.get("job")} for c in crew if c.get("job") in ("Director", "Screenplay", "Writer", "Producer", "Creator")][:6],
                "trailer": (vids[0].get("url") if vids else None)}

    def requests_on(mi: dict | None) -> list:
        return [{"id": q.get("id"), "status": REQUEST_STATUS.get(q.get("status"), "unknown"), "is4k": bool(q.get("is4k")),
                 "seasons": sorted(s.get("seasonNumber") for s in (q.get("seasons") or []) if s.get("seasonNumber") is not None),
                 "by": {"id": (q.get("requestedBy") or {}).get("id"), "name": (q.get("requestedBy") or {}).get("displayName")},
                 "created": q.get("createdAt")} for q in (mi or {}).get("requests") or []]

    @r.get("/movie/{tmdb_id}")
    async def movie(request: Request, tmdb_id: int):
        _, uid = await who(request)
        d = await run(svc.get(f"/movie/{tmdb_id}", user=uid, ttl=FAST))
        mi = d.get("mediaInfo") or {}
        return {"kind": "movie", "id": d.get("id"), "title": d.get("title"), "year": year(d), "poster": d.get("posterPath"),
                "backdrop": d.get("backdropPath"), "overview": d.get("overview"), "tagline": d.get("tagline"), "runtime": d.get("runtime"),
                "genres": [{"id": g.get("id"), "name": g.get("name")} for g in d.get("genres") or []], "vote": d.get("voteAverage"),
                "released": d.get("releaseDate"), "status": d.get("status"), "imdbId": d.get("imdbId"),
                "certification": next((x.get("certification") for c in ((d.get("releases") or {}).get("results") or []) if c.get("iso_3166_1") == "US"
                                       for x in c.get("release_dates") or [] if x.get("certification")), None),
                "studios": [{"id": c.get("id"), "name": c.get("name")} for c in (d.get("productionCompanies") or [])[:3]],
                "collection": {"id": d["collection"]["id"], "name": d["collection"].get("name"), "poster": d["collection"].get("posterPath")} if d.get("collection") else None,
                "media": media_state(mi), "requests": requests_on(mi), "watchlisted": bool(d.get("onUserWatchlist")),
                **detail_extra(d)}

    @r.get("/tv/{tmdb_id}")
    async def tv(request: Request, tmdb_id: int):
        _, uid = await who(request)
        d = await run(svc.get(f"/tv/{tmdb_id}", user=uid, ttl=FAST))
        mi = d.get("mediaInfo") or {}
        have = {s.get("seasonNumber"): s for s in mi.get("seasons") or []}
        asked = {}
        for q in mi.get("requests") or []:
            st = REQUEST_STATUS.get(q.get("status"))
            if st in ("declined", "failed", "completed"):
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
                "seasons": seasons, "media": media_state(mi), "requests": requests_on(mi), "watchlisted": bool(d.get("onUserWatchlist")),
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
        _, uid = await who(request)
        u = await run(me_info(uid))
        b = body.model_dump(exclude_none=True)
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
        return {"ok": True, "status": REQUEST_STATUS.get((res or {}).get("status"), "pending"), "id": (res or {}).get("id")}

    @r.delete("/request/{request_id}")
    async def cancel_request(request: Request, request_id: int):
        _, uid = await who(request)
        await run(svc.send("DELETE", f"/request/{request_id}", user=uid))
        return {"ok": True}

    @r.post("/request/{request_id}/{decision}")
    async def decide(request: Request, request_id: int, decision: str):
        _, uid = await who(request)
        if decision not in ("approve", "decline"):
            raise HTTPException(404, "No such action.")
        await run(svc.send("POST", f"/request/{request_id}/{decision}", user=uid))
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
