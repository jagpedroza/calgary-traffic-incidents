"""
Streamlit dashboard (local version): Calgary traffic incidents.

    python -m streamlit run streamlit_app.py

Data: The City of Calgary — Open Calgary, "Traffic Incidents" (35ra-9556).
Contains information licensed under the Open Government Licence – City of Calgary.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import etl
from traffic import core, queries as Q
from traffic.periods import DEFAULT_PRESET, PRESETS, grain_for, preset_range, today_local

DB_PATH = etl.DB_PATH
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CALGARY_CENTER = {"lat": 51.045, "lon": -114.06}
AUTO_REFRESH_SECONDS = 600

st.set_page_config(page_title="Calgary Traffic Incidents", page_icon="🚦", layout="wide")


# ---------------------------------------------------------------- data
@st.cache_resource(ttl=AUTO_REFRESH_SECONDS, show_spinner="Checking Open Calgary for new incidents…")
def auto_refresh() -> int:
    """Incremental load at most every 10 minutes, so 'Today' stays current."""
    try:
        return etl.run(full=False, quiet=True)
    except Exception:  # noqa: BLE001 - offline is fine, keep the local data
        return -1


def db_version() -> float:
    return DB_PATH.stat().st_mtime if DB_PATH.exists() else 0.0


@st.cache_data(show_spinner="Querying the database…")
def run(fn_name: str, filters: Q.Filters, *args, _version: float = 0.0) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as con:
        result = getattr(Q, fn_name)(con, filters, *args)
    return pd.DataFrame([result] if isinstance(result, dict) else result)


@st.cache_data
def raw_sql(sql: str, _version: float = 0.0) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as con:
        return pd.read_sql_query(sql, con)


def q(fn_name: str, filters: Q.Filters, *args) -> pd.DataFrame:
    return run(fn_name, filters, *args, _version=db_version())


if not DB_PATH.exists():
    st.title("🚦 Calgary Traffic Incidents")
    st.info("No local data yet. Download it from Open Calgary to get started (≈30 s).")
    if st.button("Download data", type="primary"):
        with st.spinner("Downloading from data.calgary.ca…"):
            etl.run(full=True)
        st.rerun()
    st.stop()

auto_refresh()
v = db_version()
meta = raw_sql("SELECT count(*) AS n, max(start_dt_local) AS latest FROM incidents", v).iloc[0]
last_run = raw_sql("SELECT max(run_at_utc) AS t FROM etl_runs", v).iloc[0]["t"]
quadrants = raw_sql(f"SELECT DISTINCT {Q.QUADRANT_SQL} AS q FROM incidents ORDER BY 1", v)["q"].tolist()
categories = raw_sql("SELECT category FROM incidents GROUP BY 1 ORDER BY count(*) DESC", v)["category"].tolist()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Filters")
    preset = st.radio("Period", PRESETS, index=PRESETS.index(DEFAULT_PRESET))
    today = today_local()
    if preset == "Custom":
        rng = st.date_input("Date range", (today.replace(day=1), today),
                            min_value=core.FIRST_DATE, max_value=today)
        start_d, end_d = rng if isinstance(rng, (tuple, list)) and len(rng) == 2 else (rng[0], rng[0])
    else:
        start_d, end_d = preset_range(preset, today)

    sel_quad = st.multiselect("Quadrant", quadrants, default=quadrants)
    sel_cat = st.multiselect("Incident type", categories, default=categories)
    text = st.text_input("Search location", placeholder="e.g. Deerfoot, Macleod, 16 Avenue")
    only_blocking = st.checkbox("Only incidents blocking lanes")

    st.divider()
    st.caption(f"{int(meta['n']):,} local records · last sync: {last_run or '—'} UTC")
    if st.button("🔄 Refresh data now"):
        with st.spinner("Looking for new records…"):
            loaded = etl.run(full=False, quiet=True)
        st.toast(f"{loaded:,} new or updated records")
        st.cache_data.clear()
        st.rerun()

if not sel_quad or not sel_cat:
    st.warning("Select at least one quadrant and one incident type.")
    st.stop()

F = Q.Filters(start_d, end_d,
              quadrants=None if len(sel_quad) == len(quadrants) else sel_quad,
              categories=None if len(sel_cat) == len(categories) else sel_cat,
              search=text, only_blocking=only_blocking)

# ---------------------------------------------------------------- header
st.title("🚦 Calgary Traffic Incidents")
period_label = f"{start_d:%b %d, %Y}" if start_d == end_d else f"{start_d:%b %d, %Y} – {end_d:%b %d, %Y}"
st.caption(f"**{preset}** · {period_label} · Calgary local time · "
           f"Data: [The City of Calgary – Open Calgary]({core.DATASET_URL})")

k = q("kpis", F).iloc[0]
n = int(k["incidents"])
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Incidents", f"{n:,}")
c2.metric("Daily average", f"{k['per_day']:.1f}")
c3.metric("Vehicle collisions", f"{int(k['collisions']):,}")
c4.metric("Pedestrians / cyclists", f"{int(k['vulnerable']):,}")
c5.metric("Blocking lanes", f"{k['pct_blocking']:.0f}%")

if n == 0:
    st.info("No incidents match these filters"
            + (" yet today." if preset == "Today" else ".")
            + " Try a longer period such as **Last 7 days**.")
    st.stop()

tab_map, tab_trend, tab_where, tab_data, tab_sql, tab_about = st.tabs(
    ["🗺️ Map", "📈 Trends", "📍 Locations", "📋 Data", "🧮 SQL", "ℹ️ Source & licence"]
)

# ---------------------------------------------------------------- map
with tab_map:
    default_mode = 1 if n < 300 else 0
    mode = st.radio("View", ["Heatmap", "Points"], index=default_mode, horizontal=True)
    pts = q("points", F)
    if mode == "Heatmap":
        fig = px.density_map(pts, lat="lat", lon="lon", radius=7, zoom=9.6, center=CALGARY_CENTER,
                             map_style="carto-positron", height=640, color_continuous_scale="YlOrRd",
                             hover_data={"location": True})
    else:
        fig = px.scatter_map(pts, lat="lat", lon="lon", color="category", zoom=9.6, center=CALGARY_CENTER,
                             map_style="carto-positron", height=640, hover_name="location",
                             hover_data={"time": True, "description": True, "lat": False, "lon": False})
        fig.update_traces(marker={"size": 9 if n < 300 else 6, "opacity": 0.8})
    fig.update_coloraxes(showscale=False)
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h", y=1.02, title=None))
    st.plotly_chart(fig, width="stretch")
    if len(pts) == 60000:
        st.caption("Showing the 60,000 most recent incidents for this filter.")

# ---------------------------------------------------------------- trends
with tab_trend:
    grains = {"Hour": "hour", "Day": "day", "Week": "week", "Month": "month"}
    auto = grain_for(start_d, end_d)
    grain_label = st.radio("Group by", list(grains), index=list(grains.values()).index(auto), horizontal=True)
    ts = q("timeseries", F, grains[grain_label])
    fig = px.bar(ts, x="period", y="incidents", color="category", height=420,
                 labels={"period": "", "category": "Type", "incidents": "Incidents"})
    fig.update_layout(margin=dict(t=10), bargap=0.05, legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch")

    left, right = st.columns(2)
    with left:
        st.subheader("When do they happen? (hour × weekday)")
        hm = q("hour_weekday", F)
        grid = (hm.pivot(index="weekday", columns="hour", values="incidents")
                  .reindex(index=range(7), columns=range(24)).fillna(0))
        grid.index = WEEKDAYS
        fig = px.imshow(grid, aspect="auto", color_continuous_scale="OrRd",
                        labels=dict(x="Local hour", y="", color="Incidents"), height=330)
        fig.update_layout(margin=dict(t=10, l=0, r=0))
        st.plotly_chart(fig, width="stretch")
    with right:
        st.subheader("By quadrant and type")
        qd = q("by_quadrant", F)
        fig = px.bar(qd, x="quadrant", y="incidents", color="category", height=330,
                     labels={"quadrant": "Quadrant", "incidents": "Incidents", "category": "Type"})
        fig.update_layout(margin=dict(t=10), showlegend=False)
        st.plotly_chart(fig, width="stretch")

    st.subheader("Year over year (all filters except the period)")
    yearly = q("yearly", F)
    fig = px.bar(yearly, x="year", y="incidents", text_auto=True, height=300,
                 labels={"year": "", "incidents": "Incidents"})
    fig.update_layout(margin=dict(t=10))
    st.plotly_chart(fig, width="stretch")
    st.caption("2016 only includes December; the current year is incomplete.")

# ---------------------------------------------------------------- locations
with tab_where:
    top_n = st.slider("Top locations", 10, 50, 20)
    top = q("top_locations", F, top_n)
    fig = px.bar(top.iloc[::-1], x="incidents", y="location", orientation="h", color="quadrant",
                 height=max(400, 22 * len(top)), labels={"location": "", "incidents": "Incidents"})
    fig.update_layout(margin=dict(t=10, l=0))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(top, width="stretch", hide_index=True)

# ---------------------------------------------------------------- data
with tab_data:
    rows = q("recent", F)
    st.caption(f"Showing {len(rows):,} of {n:,} incidents (most recent first).")
    st.dataframe(rows, width="stretch", hide_index=True, height=520)
    st.download_button("⬇️ Download filtered CSV", rows.to_csv(index=False).encode("utf-8"),
                       file_name="calgary_traffic_incidents.csv", mime="text/csv")

# ---------------------------------------------------------------- SQL
with tab_sql:
    st.markdown("Query the local **SQLite** database (read-only). Table: `incidents`.")
    with st.expander("Schema"):
        st.code(core.SCHEMA.strip(), language="sql")
    examples = {
        "Incidents by year and quadrant": "SELECT year, quadrant, count(*) AS n\nFROM incidents\nGROUP BY 1, 2\nORDER BY 1, 2;",
        "Weekday rush hours": "SELECT hour, count(*) AS n\nFROM incidents\nWHERE weekday < 5\nGROUP BY hour\nORDER BY n DESC\nLIMIT 10;",
        "Intersections with most pedestrian incidents": "SELECT incident_info, count(*) AS n\nFROM incidents\nWHERE category = 'Pedestrian'\nGROUP BY 1\nORDER BY n DESC\nLIMIT 15;",
        "Month-over-month change (LAG)": "WITH m AS (\n  SELECT substr(start_dt_local,1,7) AS month, count(*) AS n\n  FROM incidents GROUP BY 1\n)\nSELECT month, n, n - LAG(n) OVER (ORDER BY month) AS change\nFROM m ORDER BY month DESC LIMIT 24;",
    }
    pick = st.selectbox("Examples", list(examples))
    sql = st.text_area("SQL", examples[pick], height=180, key=f"sql_{pick}")
    if st.button("Run query", type="primary"):
        stmt = sql.strip().rstrip(";")
        if not stmt.lower().startswith(("select", "with")) or ";" in stmt:
            st.error("Only a single SELECT / WITH query is allowed.")
        else:
            try:
                res = raw_sql(stmt, db_version())
                st.caption(f"{len(res):,} rows")
                st.dataframe(res.head(10_000), width="stretch", hide_index=True)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Error: {exc}")

# ---------------------------------------------------------------- source
with tab_about:
    st.markdown(f"""
**Source:** [Traffic Incidents — Open Calgary]({core.DATASET_URL}), published by **The City of Calgary**
(Mobility business unit). Collected by the *Advanced Traveler Information System (ATIS)*, updated
continuously, covering December 2016 to the present.

**What it contains:** according to the City, this is an *unofficial* archive of incidents that affect
traffic flow (traffic signal issues, hazardous road conditions, stalled vehicles and unverified
collisions), based on traffic camera imagery. **It is not an official collision record** and may have
gaps due to system malfunctions. The *Type* column in this app is derived automatically from the
description text.

**Licence:** {core.ATTRIBUTION.replace("Open Government Licence – City of Calgary", f"[Open Government Licence – City of Calgary]({core.LICENCE_URL})")}

This app is independent and is not endorsed by The City of Calgary. Information is provided "as is",
without warranty of any kind.
""")

st.divider()
st.caption(
    f"Contains information licensed under the [Open Government Licence – City of Calgary]({core.LICENCE_URL}). "
    f"Source: [The City of Calgary – Open Calgary]({core.DATASET_URL}). "
    "Independent app, not endorsed by The City of Calgary."
)
