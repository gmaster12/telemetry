"""A stand-in Radarr and Sonarr (v3 API) for tests: http://arr.test/radarr and http://arr.test/sonarr."""
from fastapi import FastAPI, HTTPException, Request

KEY = "arr-key"
app = FastAPI()
state: dict = {}


def reset():
    state.clear()
    state.update(movies=[], series=[], episodes=[], files=[], queue={"radarr": [], "sonarr": []}, calls=[])


reset()


def check(request: Request, kind: str):
    if request.headers.get("x-api-key") != KEY:
        raise HTTPException(401, "Unauthorized")
    state["calls"].append((kind, request.method, request.url.path, dict(request.query_params)))


@app.get("/{kind}/api/v3/system/status")
def status(kind: str, request: Request):
    check(request, kind)
    return {"appName": kind.title(), "version": "5.0.0"}


@app.get("/{kind}/api/v3/queue")
def queue(kind: str, request: Request):
    check(request, kind)
    return {"page": 1, "records": state["queue"][kind]}


@app.get("/radarr/api/v3/movie")
def movies(request: Request, tmdbId: int | None = None):
    check(request, "radarr")
    return [m for m in state["movies"] if tmdbId is None or m["tmdbId"] == tmdbId]


@app.delete("/radarr/api/v3/movie/{mid}")
def delete_movie(mid: int, request: Request):
    check(request, "radarr")
    state["movies"] = [m for m in state["movies"] if m["id"] != mid]
    return None


@app.get("/sonarr/api/v3/series")
def series(request: Request, tvdbId: int | None = None):
    check(request, "sonarr")
    return [s for s in state["series"] if tvdbId is None or s["tvdbId"] == tvdbId]


@app.get("/sonarr/api/v3/series/{sid}")
def one_series(sid: int, request: Request):
    check(request, "sonarr")
    return next(s for s in state["series"] if s["id"] == sid)


@app.put("/sonarr/api/v3/series/{sid}")
async def put_series(sid: int, request: Request):
    check(request, "sonarr")
    body = await request.json()
    state["series"] = [body if s["id"] == sid else s for s in state["series"]]
    return body


@app.delete("/sonarr/api/v3/series/{sid}")
def delete_series(sid: int, request: Request):
    check(request, "sonarr")
    state["series"] = [s for s in state["series"] if s["id"] != sid]
    return None


@app.get("/sonarr/api/v3/episode")
def episodes(request: Request, seriesId: int):
    check(request, "sonarr")
    return [e for e in state["episodes"] if e["seriesId"] == seriesId]


@app.get("/sonarr/api/v3/episodefile")
def files(request: Request, seriesId: int):
    check(request, "sonarr")
    return [f for f in state["files"] if f["seriesId"] == seriesId]


@app.delete("/sonarr/api/v3/episodefile/bulk")
async def delete_files(request: Request):
    check(request, "sonarr")
    ids = set((await request.json())["episodeFileIds"])
    state["files"] = [f for f in state["files"] if f["id"] not in ids]
    return None
