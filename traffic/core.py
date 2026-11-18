"""
Shared data layer: download Calgary traffic incidents from the official Open Calgary
API (Socrata SODA) and load them into SQLite (a file locally, or in memory on Vercel).

Source: The City of Calgary — Open Calgary, "Traffic Incidents" (35ra-9556)
Contains information licensed under the Open Government Licence – City of Calgary.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

import requests

API_URL = "https://data.calgary.ca/resource/35ra-9556.json"
DATASET_URL = "https://data.calgary.ca/Transportation-Transit/Traffic-Incidents/35ra-9556"
LICENCE_URL = "https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa"
ATTRIBUTION = "Contains information licensed under the Open Government Licence – City of Calgary."
FIRST_DATE = date(2016, 12, 6)
LOCAL_TZ = ZoneInfo("America/Edmonton")  # Calgary local time
PAGE_SIZE = 50_000
COLUMNS = "id,incident_info,description,start_dt_utc,modified_dt_utc,quadrant,latitude,longitude"

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id               TEXT PRIMARY KEY,
    incident_info    TEXT,     -- location (free text)
    description      TEXT,     -- original description
    category         TEXT,     -- category derived from the description
    lanes_blocked    INTEGER,  -- 1 if the description mentions blocked lanes
    start_dt_utc     TEXT,     -- ISO 8601, UTC
    modified_dt_utc  TEXT,
    start_dt_local   TEXT,     -- ISO 8601, Calgary local time
    year             INTEGER,
    month            INTEGER,
    weekday          INTEGER,  -- 0 = Monday ... 6 = Sunday
    hour             INTEGER,
    quadrant         TEXT,
    latitude         REAL,
    longitude        REAL
);
CREATE INDEX IF NOT EXISTS ix_incidents_start    ON incidents(start_dt_local);
CREATE INDEX IF NOT EXISTS ix_incidents_quadrant ON incidents(quadrant);
CREATE INDEX IF NOT EXISTS ix_incidents_category ON incidents(category);

CREATE TABLE IF NOT EXISTS etl_runs (
    run_at_utc   TEXT,
    mode         TEXT,
    rows_loaded  INTEGER
);
"""

# Order matters: the first matching rule wins.
CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("Pedestrian", re.compile(r"pedestrian", re.I)),
    ("Cyclist", re.compile(r"cyclist|bicycle|bike", re.I)),
    ("Multi-vehicle", re.compile(r"multi[- ]?vehicle|three vehicle|3 vehicle|four vehicle", re.I)),
    ("Two-vehicle", re.compile(r"two vehicle|2 vehicle", re.I)),
    ("Single vehicle", re.compile(r"single vehicle|one vehicle|1 vehicle|rollover", re.I)),
    ("Stalled vehicle", re.compile(r"stall", re.I)),
    ("Traffic signals", re.compile(r"signal|lights? (are|is) (out|flashing)", re.I)),
    ("Truck / bus", re.compile(r"semi|truck|bus\b|transit", re.I)),
    ("Debris / road hazard", re.compile(r"debris|road condition|ice|flood|water main|pothole", re.I)),
    ("Traffic incident", re.compile(r"traffic incident|incident", re.I)),
]
OTHER = "Other"
CATEGORIES = [name for name, _ in CATEGORY_RULES] + [OTHER]
COLLISION_CATEGORIES = ("Two-vehicle", "Multi-vehicle", "Single vehicle")
VULNERABLE_CATEGORIES = ("Pedestrian", "Cyclist")
NO_QUADRANT = "Unknown"


def categorize(description: str | None) -> str:
    text = description or ""
    for name, pattern in CATEGORY_RULES:
        if pattern.search(text):
            return name
    return OTHER


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def to_row(rec: dict) -> tuple | None:
    start = _parse_utc(rec.get("start_dt_utc"))
    if start is None or not rec.get("id"):
        return None
    local = start.astimezone(LOCAL_TZ)
    desc = (rec.get("description") or "").strip()
    try:
        lat = float(rec["latitude"]) if rec.get("latitude") else None
        lon = float(rec["longitude"]) if rec.get("longitude") else None
    except ValueError:
        lat = lon = None
    return (
        rec["id"],
        (rec.get("incident_info") or "").strip(),
        desc,
        categorize(desc),
        1 if re.search(r"blocking", desc, re.I) else 0,
        start.strftime("%Y-%m-%dT%H:%M:%S"),
        rec.get("modified_dt_utc"),
        local.strftime("%Y-%m-%dT%H:%M:%S"),
        local.year,
        local.month,
        local.weekday(),
        local.hour,
        (rec.get("quadrant") or "").strip().upper() or None,
        lat,
        lon,
    )


def local_day_bounds_utc(start: date, end: date) -> tuple[str, str]:
    """Local Calgary dates [start, end] -> UTC timestamps [from, to) for SoQL."""
    lo = datetime.combine(start, dtime.min, LOCAL_TZ).astimezone(timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), dtime.min, LOCAL_TZ).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return lo.strftime(fmt), hi.strftime(fmt)


def fetch(where: str | None, order: str = "modified_dt_utc, id",
          app_token: str | None = None, timeout: int = 120) -> Iterator[list[dict]]:
    """Yield pages of raw records from the Socrata API."""
    session = requests.Session()
    session.headers["User-Agent"] = "calgary-traffic-incidents/1.0"
    if app_token:
        session.headers["X-App-Token"] = app_token
    offset = 0
    while True:
        params = {"$select": COLUMNS, "$order": order, "$limit": PAGE_SIZE, "$offset": offset}
        if where:
            params["$where"] = where
        for attempt in range(4):
            try:
                resp = session.get(API_URL, params=params, timeout=timeout)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt == 3:
                    raise
                wait = 2 ** attempt * 3
                print(f"  API error {exc!r}; retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
        page = resp.json()
        if not page:
            return
        yield page
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def insert(con: sqlite3.Connection, records: Iterable[dict]) -> int:
    rows = [r for r in map(to_row, records) if r]
    con.executemany("INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def recategorize(con: sqlite3.Connection) -> None:
    """Re-derive categories if the stored ones come from an older rule set."""
    known = set(CATEGORIES)
    stored = {r[0] for r in con.execute("SELECT DISTINCT category FROM incidents")}
    if stored <= known:
        return
    rows = con.execute("SELECT id, description FROM incidents").fetchall()
    con.executemany("UPDATE incidents SET category = ? WHERE id = ?",
                    [(categorize(d), i) for i, d in rows])
    con.commit()


def memory_db_for_range(start: date, end: date, app_token: str | None = None) -> sqlite3.Connection:
    """Fetch one local-date range from the API into an in-memory SQLite database."""
    lo, hi = local_day_bounds_utc(start, end)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.executescript(SCHEMA)
    for page in fetch(f"start_dt_utc >= '{lo}' AND start_dt_utc < '{hi}'",
                      order="start_dt_utc, id", app_token=app_token, timeout=50):
        insert(con, page)
    con.commit()
    return con
