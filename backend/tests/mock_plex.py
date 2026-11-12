"""A stand-in Plex Media Server for tests: library sections, items with their ids and files, and
`checkFiles` on one item. Served at http://plex.test (see `tests/test_onplex.py`).

`state` is shaped like the real answers, so a test can describe a library the way Plex does:

    state["sections"] = [{"key": "1", "type": "movie", "title": "Movies"}]
    state["items"]["1"] = [movie(157350, size=800, resolution="1080")]
"""
from fastapi import FastAPI, HTTPException, Request

TOKEN = "plex-test-token"
MACHINE = "machine-perseus"
app = FastAPI()
state: dict = {}


def reset():
    state.clear()
    state.update(sections=[], items={}, leaves={}, machine=MACHINE, calls=[], fail=set())


reset()


def movie(tmdb=None, *, rating_key="1", title="A Movie", year=2014, size=800, resolution="1080",
          file=r"Z:\Movies\A Movie (2014)\A Movie (2014) Bluray-1080p.mkv", updated=1, guids=True,
          exists=True, accessible=True):
    """One library item in Plex's shape; `guids=False` is an entry an old Plex agent left without ids."""
    return {"ratingKey": str(rating_key), "type": "movie", "title": title, "year": year, "updatedAt": updated,
            "Guid": ([{"id": f"tmdb://{tmdb}"}, {"id": "imdb://tt1"}] if guids and tmdb else []),
            "Media": [{"videoResolution": resolution,
                       "Part": [{"id": 10 + int(rating_key), "file": file, "size": size,
                                 "exists": exists, "accessible": accessible}]}]}


def episode(season, number, *, size=500, resolution="1080", rating_key="500"):
    return {"ratingKey": str(rating_key), "type": "episode", "parentIndex": season, "index": number,
            "title": f"S{season}E{number}",
            "Media": [{"videoResolution": resolution, "Part": [{"id": 900 + number, "file": f"Z:\\TV\\s{season}e{number}.mkv",
                                                                "size": size, "exists": True, "accessible": True}]}]}


def show(*, rating_key="80", title="A Show", tvdb=None, tmdb=None, updated=1):
    return {"ratingKey": str(rating_key), "type": "show", "title": title, "updatedAt": updated,
            "Guid": [g for g in ([{"id": f"tvdb://{tvdb}"}] if tvdb else []) + ([{"id": f"tmdb://{tmdb}"}] if tmdb else [])]}


def check(request: Request, what: str):
    if request.headers.get("x-plex-token") != TOKEN:
        raise HTTPException(401, "Unauthorized")
    state["calls"].append((what, dict(request.query_params)))
    if what in state["fail"]:
        raise HTTPException(500, "Plex is unhappy")


@app.get("/")
def identity(request: Request):
    check(request, "identity")
    return {"MediaContainer": {"machineIdentifier": state["machine"], "friendlyName": "Perseus"}}


@app.get("/library/sections")
def sections(request: Request):
    check(request, "sections")
    return {"MediaContainer": {"Directory": state["sections"]}}


@app.get("/library/sections/{key}/all")
def items(key: str, request: Request, type: int = 1, title: str | None = None):
    check(request, "items")
    rows = [x for x in state["items"].get(key, []) if (type == 2) == (x.get("type") == "show")]
    if title:
        rows = [x for x in rows if title.lower() in str(x.get("title", "")).lower()]
    else:
        rows = sorted(rows, key=lambda x: -(x.get("updatedAt") or 0))
    size = request.query_params.get("X-Plex-Container-Size")
    if size and str(size).isdigit():
        rows = rows[:int(size)]
    state["calls"][-1] = ("items", {**state["calls"][-1][1], "section": str(key)})
    return {"MediaContainer": {"size": len(rows), "totalSize": len(state["items"].get(key, [])), "Metadata": rows}}


@app.get("/library/metadata/{rating_key}/allLeaves")
def leaves(rating_key: str, request: Request):
    check(request, "leaves")
    return {"MediaContainer": {"Metadata": state["leaves"].get(str(rating_key), [])}}


@app.get("/library/metadata/{rating_key}")
def one(rating_key: str, request: Request, checkFiles: int = 0):
    check(request, "metadata")
    for rows in state["items"].values():
        for x in rows:
            if str(x.get("ratingKey")) == str(rating_key):
                return {"MediaContainer": {"Metadata": [x]}}
    raise HTTPException(404, "No such item")
