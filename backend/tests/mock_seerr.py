"""A small stand-in for Seerr's API, for tests and local browser checks.

It checks the API key, acts as the user named in X-API-User (or user 1), and keeps requests,
issues and a watchlist in memory. Run alone with: uvicorn tests.mock_seerr:app --port 5055
"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

KEY = "seerr-test-key"
REQUEST, REQUEST_4K, ADVANCED, MANAGE, AUTO = 32, 1024, 8192, 16, 128

app = FastAPI()
calls: list[dict] = []
state: dict = {}


def reset():
    state.clear()
    state.update({
        "users": [
            {"id": 1, "email": "cluster@mail.com", "plexUsername": "glenn", "username": None, "displayName": "Glenn", "permissions": 2,
             "userType": 1, "plexToken": "secret-admin-token", "avatar": "/a1.png"},
            {"id": 5, "email": "s@x.com", "plexUsername": "sable", "username": None, "displayName": "sable", "permissions": REQUEST | REQUEST_4K | 4194304,
             "userType": 1, "plexToken": "", "avatar": "/a5.png"},
        ],
        "plex_shares": {"2002": {"email": "k@x.com", "username": "kit"}},  # who Seerr's own Plex account can import
        "requests": [],
        "issues": [],
        "watch": {},
        "next": 100,
    })
    calls.clear()


reset()

MOVIES = {
    603: {"title": "The Matrix", "releaseDate": "1999-03-31", "posterPath": "/p603.jpg", "backdropPath": "/b603.jpg", "status": 5},
    27205: {"title": "Inception", "releaseDate": "2010-07-15", "posterPath": "/p27205.jpg", "backdropPath": "/b27205.jpg", "status": None},
    157336: {"title": "Interstellar", "releaseDate": "2014-11-05", "posterPath": "/p157336.jpg", "backdropPath": "/b157336.jpg", "status": 2},
    1001: {"title": "Night Harbor", "releaseDate": "2026-12-01", "posterPath": "/p1001.jpg", "backdropPath": "/b1001.jpg", "status": None},
}
SHOWS = {
    1399: {"name": "Game of Thrones", "firstAirDate": "2011-04-17", "posterPath": "/p1399.jpg", "backdropPath": "/b1399.jpg", "seasons": 8,
           "have": {1: 5, 2: 5, 3: 4}},
    1396: {"name": "Breaking Bad", "firstAirDate": "2008-01-20", "posterPath": "/p1396.jpg", "backdropPath": "/b1396.jpg", "seasons": 5, "have": {}},
}


def me(request: Request) -> dict:
    if request.headers.get("x-api-key") != KEY:
        raise HTTPException(403, "You do not have permission to access this endpoint")
    uid = int(request.headers.get("x-api-user") or 1)
    u = next((u for u in state["users"] if u["id"] == uid), None)
    if not u:
        raise HTTPException(403, "You do not have permission to access this endpoint")
    calls.append({"path": request.url.path, "method": request.method, "user": uid})
    return u


def can(u, bit):
    return bool(u["permissions"] & 2) or bool(u["permissions"] & bit)


def media_info(kind, tmdb):
    if kind == "movie":
        st = MOVIES[tmdb]["status"]
        reqs = [r for r in state["requests"] if r["type"] == "movie" and r["media"]["tmdbId"] == tmdb]
        if not st and reqs:
            st = 2
        if not st:
            return None
        return {"id": tmdb, "tmdbId": tmdb, "status": st, "status4k": 1, "mediaUrl": "https://app.plex.tv/x" if st == 5 else None,
                "requests": [full_req(r) for r in reqs], "seasons": []}
    s = SHOWS[tmdb]
    reqs = [r for r in state["requests"] if r["type"] == "tv" and r["media"]["tmdbId"] == tmdb]
    if not s["have"] and not reqs:
        return None
    seasons = [{"seasonNumber": n, "status": st, "status4k": 1} for n, st in s["have"].items()]
    status = 4 if s["have"] else 2
    return {"id": tmdb + 50000, "tmdbId": tmdb, "status": status, "status4k": 1, "seasons": seasons, "requests": [full_req(r) for r in reqs]}


def result(kind, tmdb):
    if kind == "movie":
        m = MOVIES[tmdb]
        return {"id": tmdb, "mediaType": "movie", "title": m["title"], "releaseDate": m["releaseDate"], "posterPath": m["posterPath"],
                "backdropPath": m["backdropPath"], "overview": f"About {m['title']}.", "voteAverage": 8.1, "mediaInfo": media_info("movie", tmdb)}
    s = SHOWS[tmdb]
    return {"id": tmdb, "mediaType": "tv", "name": s["name"], "firstAirDate": s["firstAirDate"], "posterPath": s["posterPath"],
            "backdropPath": s["backdropPath"], "overview": f"About {s['name']}.", "voteAverage": 8.9, "mediaInfo": media_info("tv", tmdb)}


def all_results():
    return [result("movie", i) for i in MOVIES] + [result("tv", i) for i in SHOWS]


def full_req(r):
    by = next(u for u in state["users"] if u["id"] == r["by"])
    return {"id": r["id"], "status": r["status"], "type": r["type"], "is4k": r["is4k"], "createdAt": r["created"], "updatedAt": r["created"],
            "media": {"tmdbId": r["media"]["tmdbId"], "mediaType": r["type"], "status": 2, "status4k": 1},
            "seasons": [{"seasonNumber": n, "status": r["status"]} for n in r.get("seasons", [])],
            "requestedBy": dict(by), "modifiedBy": None}


@app.get("/api/v1/status")
def status(request: Request):
    me(request)
    return {"version": "3.0.0-mock"}


@app.get("/api/v1/auth/me")
def auth_me(request: Request):
    return dict(me(request))


@app.get("/api/v1/settings/public")
def public(request: Request):
    me(request)
    return {"applicationTitle": "Perseus", "movie4kEnabled": True, "series4kEnabled": True, "partialRequestsEnabled": True,
            "hideAvailable": False, "enableSpecialEpisodes": False, "discoverRegion": "US", "originalLanguage": ""}


@app.get("/api/v1/settings/discover")
def sliders(request: Request):
    u = me(request)
    if not can(u, 4):
        raise HTTPException(403, "no")
    names = {1: "", 2: "", 3: "", 4: "", 5: "", 6: "", 7: "", 8: "", 9: "", 10: "", 11: "", 12: ""}
    return [{"id": t, "type": t, "title": n or None, "enabled": t != 12, "isBuiltIn": True} for t, n in names.items()]


@app.get("/api/v1/user")
def users(request: Request, q: str = "", take: int = 20):
    me(request)
    q = q.lower()
    return {"pageInfo": {"page": 1, "pages": 1}, "results": [dict(u) for u in state["users"]
                                                           if not q or q in (u["email"] or "").lower() or q in (u["plexUsername"] or "").lower()]}


@app.post("/api/v1/user/import-from-plex")
async def import_plex(request: Request):
    me(request)
    body = await request.json()
    made = []
    for pid in body.get("plexIds", []):
        sh = state["plex_shares"].get(pid)
        if sh and not any(u["email"] == sh["email"] for u in state["users"]):
            state["next"] += 1
            u = {"id": state["next"], "email": sh["email"], "plexUsername": sh["username"], "username": None, "displayName": sh["username"],
                 "permissions": REQUEST, "userType": 1, "plexToken": "", "avatar": ""}
            state["users"].append(u)
            made.append(u)
    return {"createdUsers": made, "refreshedUsers": 0}


@app.post("/api/v1/user")
async def create_user(request: Request):
    me(request)
    body = await request.json()
    if any(u["email"] == body["email"] for u in state["users"]):
        raise HTTPException(409, "User already exists with submitted email.")
    state["next"] += 1
    u = {"id": state["next"], "email": body["email"], "plexUsername": None, "username": body.get("username"), "displayName": body.get("username"),
         "permissions": REQUEST, "userType": 2, "plexToken": "", "avatar": ""}
    state["users"].append(u)
    return u


@app.get("/api/v1/user/{uid}/quota")
def quota(request: Request, uid: int):
    me(request)
    used = len([r for r in state["requests"] if r["by"] == uid and r["type"] == "movie"])
    return {"movie": {"days": 7, "limit": 10, "used": used, "remaining": 10 - used, "restricted": used >= 10},
            "tv": {"days": 7, "limit": 0, "used": 0, "remaining": 0, "restricted": False}}


@app.get("/api/v1/discover/trending")
@app.get("/api/v1/discover/movies")
@app.get("/api/v1/discover/tv")
@app.get("/api/v1/search")
def discover(request: Request, page: int = 1, query: str = ""):
    me(request)
    res = all_results()
    if "movies" in request.url.path:
        res = [r for r in res if r["mediaType"] == "movie"]
    if request.url.path.endswith("/tv"):
        res = [r for r in res if r["mediaType"] == "tv"]
    if query:
        res = [r for r in res if query.lower() in (r.get("title") or r.get("name")).lower()]
        if "keanu" in query.lower():
            res.append({"id": 6384, "mediaType": "person", "name": "Keanu Reeves", "profilePath": "/k.jpg", "knownFor": [{"title": "The Matrix"}]})
    return {"page": page, "totalPages": 2, "totalResults": len(res) * 2, "results": res}


@app.get("/api/v1/discover/genreslider/{kind}")
def genres(request: Request, kind: str):
    me(request)
    return [{"id": 28, "name": "Action", "backdrops": ["/g28.jpg"]}, {"id": 35, "name": "Comedy", "backdrops": ["/g35.jpg"]}]


@app.get("/api/v1/discover/watchlist")
def watchlist(request: Request, page: int = 1):
    u = me(request)
    items = [{"tmdbId": t, "mediaType": k, "title": "x"} for t, k in state["watch"].get(u["id"], {}).items()]
    return {"page": 1, "totalPages": 1, "totalResults": len(items), "results": items}


@app.post("/api/v1/watchlist")
async def watch_add(request: Request):
    u = me(request)
    b = await request.json()
    state["watch"].setdefault(u["id"], {})[b["tmdbId"]] = b["mediaType"]
    return b


@app.delete("/api/v1/watchlist/{tmdb}")
def watch_del(request: Request, tmdb: int):
    u = me(request)
    state["watch"].get(u["id"], {}).pop(tmdb, None)
    return Response(status_code=204)


@app.get("/api/v1/media")
def media(request: Request):
    me(request)
    return {"pageInfo": {"page": 1, "pages": 1}, "results": [
        {"id": 603, "tmdbId": 603, "mediaType": "movie", "status": 5, "status4k": 1, "mediaAddedAt": "2026-10-20"},
        {"id": 51399, "tmdbId": 1399, "mediaType": "tv", "status": 4, "status4k": 1, "mediaAddedAt": "2026-10-19"}]}


@app.get("/api/v1/movie/{tmdb}")
def movie(request: Request, tmdb: int):
    me(request)
    if tmdb not in MOVIES:
        raise HTTPException(404, "Unable to retrieve movie.")
    r = result("movie", tmdb)
    return {**r, "tagline": "Free your mind.", "runtime": 136, "genres": [{"id": 28, "name": "Action"}], "status": "Released",
            "credits": {"cast": [{"id": 6384, "name": "Keanu Reeves", "character": "Neo", "profilePath": "/k.jpg"}],
                        "crew": [{"id": 9339, "name": "Lana Wachowski", "job": "Director"}]},
            "relatedVideos": [{"site": "YouTube", "type": "Trailer", "url": "https://www.youtube.com/watch?v=abc"}],
            "collection": {"id": 2344, "name": "The Matrix Collection", "posterPath": "/c.jpg"} if tmdb == 603 else None,
            "releases": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "R"}]}]}, "productionCompanies": [{"id": 174, "name": "Warner Bros. Pictures"}]}


@app.get("/api/v1/tv/{tmdb}")
def tv(request: Request, tmdb: int):
    me(request)
    if tmdb not in SHOWS:
        raise HTTPException(404, "Unable to retrieve series.")
    s = SHOWS[tmdb]
    r = result("tv", tmdb)
    return {**r, "seasons": [{"seasonNumber": 0, "name": "Specials", "episodeCount": 3}] +
            [{"seasonNumber": n, "name": f"Season {n}", "episodeCount": 10, "airDate": f"{2010 + n}-04-01", "posterPath": f"/s{n}.jpg"} for n in range(1, s["seasons"] + 1)],
            "genres": [{"id": 18, "name": "Drama"}], "networks": [{"id": 49, "name": "HBO"}], "externalIds": {"tvdbId": 121361},
            "credits": {"cast": [{"id": 22970, "name": "Peter Dinklage", "character": "Tyrion", "profilePath": "/pd.jpg"}], "crew": []},
            "contentRatings": {"results": [{"iso_3166_1": "US", "rating": "TV-MA"}]}, "status": "Ended", "episodeRunTime": [60]}


@app.get("/api/v1/tv/{tmdb}/season/{n}")
def season(request: Request, tmdb: int, n: int):
    me(request)
    return {"seasonNumber": n, "name": f"Season {n}", "episodes": [{"episodeNumber": e, "name": f"Episode {e}", "airDate": "2011-04-17", "overview": "Things happen."} for e in range(1, 11)]}


@app.get("/api/v1/{kind}/{tmdb}/recommendations")
@app.get("/api/v1/{kind}/{tmdb}/similar")
def related(request: Request, kind: str, tmdb: int):
    me(request)
    return {"page": 1, "totalPages": 1, "results": [r for r in all_results() if r["id"] != tmdb]}


@app.get("/api/v1/person/{pid}")
def person(request: Request, pid: int):
    me(request)
    return {"id": pid, "name": "Keanu Reeves", "profilePath": "/k.jpg", "biography": "An actor.", "birthday": "1964-09-02", "knownForDepartment": "Acting"}


@app.get("/api/v1/person/{pid}/combined_credits")
def credits(request: Request, pid: int):
    me(request)
    return {"cast": [result("movie", 603)], "crew": []}


@app.get("/api/v1/collection/{cid}")
def collection(request: Request, cid: int):
    me(request)
    return {"id": cid, "name": "The Matrix Collection", "overview": "All of them.", "parts": [result("movie", 603), result("movie", 1001)]}


@app.get("/api/v1/request/count")
def count(request: Request):
    me(request)
    return {"pending": len([r for r in state["requests"] if r["status"] == 1])}


@app.get("/api/v1/request")
def list_requests(request: Request, requestedBy: int | None = None, filter: str = "all", take: int = 20, skip: int = 0):
    u = me(request)
    rows = state["requests"]
    if not can(u, MANAGE):
        if requestedBy and requestedBy != u["id"]:
            raise HTTPException(403, "You do not have permission to view this user's requests.")
        rows = [r for r in rows if r["by"] == u["id"]]
    elif requestedBy:
        rows = [r for r in rows if r["by"] == requestedBy]
    if filter == "pending":
        rows = [r for r in rows if r["status"] == 1]
    rows = list(reversed(rows))
    return {"pageInfo": {"page": skip // take + 1, "pages": max(1, -(-len(rows) // take)), "results": len(rows)},
            "results": [full_req(r) for r in rows[skip:skip + take]]}


@app.post("/api/v1/request")
async def new_request(request: Request):
    u = me(request)
    b = await request.json()
    state.setdefault("bodies", []).append(b)
    if b.get("is4k") and not can(u, REQUEST_4K):
        raise HTTPException(403, "You do not have permission to make 4K requests.")
    state["next"] += 1
    r = {"id": state["next"], "type": b["mediaType"], "status": 2 if can(u, AUTO) else 1, "is4k": bool(b.get("is4k")), "by": u["id"],
         "created": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "media": {"tmdbId": b["mediaId"]},
         "seasons": (list(range(1, SHOWS[b["mediaId"]]["seasons"] + 1)) if b.get("seasons") == "all" else b.get("seasons") or []) if b["mediaType"] == "tv" else []}
    state["requests"].append(r)
    return full_req(r)


@app.delete("/api/v1/request/{rid}")
def delete_request(request: Request, rid: int):
    u = me(request)
    r = next((r for r in state["requests"] if r["id"] == rid), None)
    if not r:
        raise HTTPException(404, "Request not found.")
    if not can(u, MANAGE) and (r["by"] != u["id"] or r["status"] != 1):
        raise HTTPException(401, "You do not have permission to delete this request.")
    state["requests"].remove(r)
    return Response(status_code=204)


@app.post("/api/v1/request/{rid}/{decision}")
def decide(request: Request, rid: int, decision: str):
    u = me(request)
    if not can(u, MANAGE):
        raise HTTPException(403, "You do not have permission to access this endpoint")
    r = next(r for r in state["requests"] if r["id"] == rid)
    r["status"] = 2 if decision == "approve" else 3
    return full_req(r)


@app.get("/api/v1/service/{name}")
def services(request: Request, name: str):
    u = me(request)
    if not can(u, ADVANCED | MANAGE):
        raise HTTPException(403, "You do not have permission to access this endpoint")
    return [{"id": 0, "name": name.title() + " HD", "is4k": False, "isDefault": True}, {"id": 1, "name": name.title() + " 4K", "is4k": True, "isDefault": True}]


@app.get("/api/v1/service/{name}/{sid}")
def service(request: Request, name: str, sid: int):
    me(request)
    return {"server": {"activeProfileId": 4, "activeDirectory": "/media/movies"}, "profiles": [{"id": 4, "name": "HD-1080p"}, {"id": 5, "name": "Ultra-HD"}],
            "rootFolders": [{"path": "/media/movies"}, {"path": "/media/kids"}]}


@app.get("/api/v1/issue")
def issues(request: Request, requestedBy: int | None = None, filter: str = "open"):
    u = me(request)
    rows = [i for i in state["issues"] if can(u, 1048576) or i["by"] == u["id"]]
    return {"pageInfo": {"page": 1, "pages": 1}, "results": [
        {"id": i["id"], "issueType": i["type"], "status": 1, "createdAt": "2026-10-20T00:00:00Z", "media": {"tmdbId": i["mediaId"], "mediaType": "movie"},
         "createdBy": {"displayName": "sable", "email": "s@x.com"}, "comments": [{"user": {"displayName": "sable", "email": "s@x.com"}, "message": i["message"]}]}
        for i in rows]}


@app.post("/api/v1/issue")
async def new_issue(request: Request):
    u = me(request)
    b = await request.json()
    state["next"] += 1
    state["issues"].append({"id": state["next"], "type": b["issueType"], "message": b["message"], "mediaId": b["mediaId"], "by": u["id"]})
    return {"id": state["next"]}


@app.get("/img/{size}/{name}")
def img(size: str, name: str):
    """Plain coloured posters for local browser checks (TMDB in production)."""
    h = sum(map(ord, name)) % 360
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="hsl({h},45%,38%)"/><stop offset="1" stop-color="hsl({(h + 60) % 360},40%,14%)"/></linearGradient></defs>'
           f'<rect width="300" height="450" fill="url(#g)"/><circle cx="210" cy="120" r="54" fill="hsl({(h + 180) % 360},55%,70%)" opacity=".5"/></svg>')
    return Response(svg, media_type="image/svg+xml")


@app.get("/_reset")
def _reset():
    reset()
    return {"ok": True}
