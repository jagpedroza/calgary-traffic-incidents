"""
SQL queries over the `incidents` table, shared by the Streamlit app (SQLite file)
and the Vercel API (in-memory SQLite). Every function returns plain Python data.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .core import COLLISION_CATEGORIES, NO_QUADRANT, VULNERABLE_CATEGORIES

GRAIN_SQL = {
    "hour": "substr(start_dt_local, 1, 13) || ':00'",
    "day": "substr(start_dt_local, 1, 10)",
    "week": "date(start_dt_local, 'weekday 1', '-7 days')",
    "month": "substr(start_dt_local, 1, 7) || '-01'",
}
QUADRANT_SQL = f"COALESCE(quadrant, '{NO_QUADRANT}')"


@dataclass
class Filters:
    start: date
    end: date
    quadrants: list[str] | None = None   # None = all
    categories: list[str] | None = None  # None = all
    search: str = ""
    only_blocking: bool = False

    def where(self, include_dates: bool = True) -> tuple[str, tuple]:
        """Parameterized WHERE clause (no user value is concatenated into SQL)."""
        clauses: list[str] = []
        params: list = []
        if include_dates:
            clauses += ["start_dt_local >= ?", "start_dt_local < ?"]
            params += [self.start.isoformat(), (self.end + timedelta(days=1)).isoformat()]
        if self.quadrants is not None:
            clauses.append(f"{QUADRANT_SQL} IN ({','.join('?' * len(self.quadrants)) or 'NULL'})")
            params += self.quadrants
        if self.categories is not None:
            clauses.append(f"category IN ({','.join('?' * len(self.categories)) or 'NULL'})")
            params += self.categories
        if self.search.strip():
            clauses.append("incident_info LIKE ?")
            params.append(f"%{self.search.strip()}%")
        if self.only_blocking:
            clauses.append("lanes_blocked = 1")
        return (" AND ".join(clauses) or "1=1"), tuple(params)


def _all(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def kpis(con, f: Filters) -> dict:
    w, p = f.where()
    coll = ",".join(f"'{c}'" for c in COLLISION_CATEGORIES)
    vuln = ",".join(f"'{c}'" for c in VULNERABLE_CATEGORIES)
    row = _all(con, f"""
        SELECT count(*) AS incidents,
               coalesce(sum(category IN ({coll})), 0) AS collisions,
               coalesce(sum(category IN ({vuln})), 0) AS vulnerable,
               coalesce(avg(lanes_blocked) * 100, 0) AS pct_blocking,
               max(start_dt_local) AS latest
        FROM incidents WHERE {w}""", p)[0]
    days = (f.end - f.start).days + 1
    row["days"] = days
    row["per_day"] = row["incidents"] / days if days else 0
    return row


def timeseries(con, f: Filters, grain: str) -> list[dict]:
    w, p = f.where()
    return _all(con, f"""
        SELECT {GRAIN_SQL[grain]} AS period, category, count(*) AS incidents
        FROM incidents WHERE {w} GROUP BY 1, 2 ORDER BY 1""", p)


def hour_weekday(con, f: Filters) -> list[dict]:
    w, p = f.where()
    return _all(con, f"SELECT weekday, hour, count(*) AS incidents FROM incidents WHERE {w} GROUP BY 1, 2", p)


def by_quadrant(con, f: Filters) -> list[dict]:
    w, p = f.where()
    return _all(con, f"""
        SELECT {QUADRANT_SQL} AS quadrant, category, count(*) AS incidents
        FROM incidents WHERE {w} GROUP BY 1, 2 ORDER BY 1""", p)


def by_category(con, f: Filters) -> list[dict]:
    w, p = f.where()
    return _all(con, f"""
        SELECT category, count(*) AS incidents
        FROM incidents WHERE {w} GROUP BY 1 ORDER BY 2 DESC""", p)


def yearly(con, f: Filters) -> list[dict]:
    w, p = f.where(include_dates=False)
    return _all(con, f"SELECT year, count(*) AS incidents FROM incidents WHERE {w} GROUP BY 1 ORDER BY 1", p)


def top_locations(con, f: Filters, limit: int = 20) -> list[dict]:
    w, p = f.where()
    vuln = ",".join(f"'{c}'" for c in VULNERABLE_CATEGORIES)
    return _all(con, f"""
        SELECT incident_info AS location, {QUADRANT_SQL} AS quadrant, count(*) AS incidents,
               sum(category IN ({vuln})) AS pedestrians_cyclists,
               max(start_dt_local) AS latest
        FROM incidents WHERE {w}
        GROUP BY incident_info, quadrant ORDER BY incidents DESC LIMIT ?""", p + (limit,))


def points(con, f: Filters, limit: int = 60_000) -> list[dict]:
    w, p = f.where()
    return _all(con, f"""
        SELECT latitude AS lat, longitude AS lon, start_dt_local AS time, incident_info AS location,
               description, category, {QUADRANT_SQL} AS quadrant
        FROM incidents WHERE {w} AND latitude IS NOT NULL
        ORDER BY start_dt_local DESC LIMIT ?""", p + (limit,))


def recent(con, f: Filters, limit: int = 5000) -> list[dict]:
    w, p = f.where()
    return _all(con, f"""
        SELECT start_dt_local AS local_time, incident_info AS location, description,
               category, {QUADRANT_SQL} AS quadrant, latitude, longitude
        FROM incidents WHERE {w} ORDER BY start_dt_local DESC LIMIT ?""", p + (limit,))
