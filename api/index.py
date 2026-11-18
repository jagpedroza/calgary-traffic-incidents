"""
FastAPI backend for the Vercel deployment.

Each request fetches the selected date range live from the Open Calgary API, loads it into an
in-memory SQLite database and runs the shared SQL in `traffic/queries.py`. Ranges are cached in
memory for a few minutes per warm instance, and responses carry CDN cache headers.

Local dev (also serves the static frontend from /public):
    python -m uvicorn api.index:app --reload --port 8000
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from traffic import core, queries as Q  # noqa: E402
from traffic.periods import DEFAULT_PRESET, PRESETS, grain_for, preset_range, today_local  # noqa: E402

app = FastAPI(title="Calgary Traffic Incidents API", docs_url="/api/docs", openapi_url="/api/openapi.json")

@app.middleware("http")
async def path_debug(request, call_next):
    response = await call_next(request)
    if response.status_code == 404:
        s = request.scope
        response.headers["X-Path-Debug"] = f"path={s.get('path')!r} root={s.get('root_path')!r} raw={s.get('raw_path')!r}"
    return response


CACHE_TTL_SECONDS = 300
_cache: dict[tuple[date, date], tuple[float, object]] = {}
_lock = threading.Lock()


def range_db(start: date, end: date):
    """In-memory SQLite for a date range, cached for CACHE_TTL_SECONDS."""
    key = (start, end)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    con = core.memory_db_for_range(start, end, app_token=os.environ.get("SOCRATA_APP_TOKEN"))
    with _lock:
        if len(_cache) > 8:  # keep memory bounded on small serverless instances
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
        _cache[key] = (time.time(), con)
    return con


def cached(payload: dict, seconds: int) -> JSONResponse:
    return JSONResponse(payload, headers={
        "Cache-Control": f"public, max-age=60, s-maxage={seconds}, stale-while-revalidate={seconds * 4}"
    })


def resolve_period(preset: str, start: date | None, end: date | None) -> tuple[date, date]:
    if preset == "Custom":
        if not start or not end:
            raise HTTPException(400, "Custom period needs start and end dates")
        start, end = min(start, end), max(start, end)
        return max(start, core.FIRST_DATE), min(end, today_local())
    if preset not in PRESETS:
        raise HTTPException(400, f"Unknown preset. Use one of: {', '.join(PRESETS)}")
    return preset_range(preset)


def split(value: str | None) -> list[str] | None:
    return None if value is None else [v for v in value.split(",") if v]


@app.get("/api/meta")
def meta():
    return cached({
        "presets": PRESETS,
        "default_preset": DEFAULT_PRESET,
        "categories": core.CATEGORIES,
        "quadrants": ["NE", "NW", "SE", "SW", core.NO_QUADRANT],
        "first_date": core.FIRST_DATE.isoformat(),
        "today": today_local().isoformat(),
        "attribution": core.ATTRIBUTION,
        "licence_url": core.LICENCE_URL,
        "dataset_url": core.DATASET_URL,
    }, 3600)


@app.get("/api/dashboard")
def dashboard(
    preset: str = Query(DEFAULT_PRESET),
    start: date | None = None,
    end: date | None = None,
    quadrants: str | None = Query(None, description="Comma-separated; omit for all"),
    categories: str | None = Query(None, description="Comma-separated; omit for all"),
    search: str = Query("", max_length=100),
    blocking: bool = False,
    grain: str | None = Query(None, pattern="^(hour|day|week|month)$"),
):
    start_d, end_d = resolve_period(preset, start, end)
    f = Q.Filters(start_d, end_d, quadrants=split(quadrants), categories=split(categories),
                  search=search, only_blocking=blocking)
    try:
        con = range_db(start_d, end_d)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Open Calgary API unavailable: {exc}") from exc
    g = grain or grain_for(start_d, end_d)
    with _lock:  # sqlite connection shared between threads
        payload = {
            "period": {"preset": preset, "start": start_d.isoformat(), "end": end_d.isoformat(), "grain": g},
            "kpis": Q.kpis(con, f),
            "timeseries": Q.timeseries(con, f, g),
            "hour_weekday": Q.hour_weekday(con, f),
            "by_quadrant": Q.by_quadrant(con, f),
            "by_category": Q.by_category(con, f),
            "top_locations": Q.top_locations(con, f, 15),
            "points": Q.points(con, f, 20_000),
            "recent": Q.recent(con, f, 500),
        }
    # Short ranges change often; long historical ranges can be cached longer at the edge.
    ttl = 120 if (end_d - start_d).days < 2 else 900
    return cached(payload, ttl)


# Serve the static frontend when running locally (Vercel serves /public itself).
if not os.environ.get("VERCEL") and (ROOT / "public").is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="static")
