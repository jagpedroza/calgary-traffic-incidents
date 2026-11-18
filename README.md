# 🚦 Calgary Traffic Incidents

Dashboard of traffic incidents in Calgary, Alberta, built on open data from
[The City of Calgary](https://data.calgary.ca/Transportation-Transit/Traffic-Incidents/35ra-9556).
Defaults to **Today** (Calgary local time), with one-click periods: Yesterday, Last 7 days,
Last 30 days, This month, This year, Last 12 months, All time, or a custom range.

The project ships two front ends that share the same Python data layer and SQL:

| | Web app (Vercel) | Streamlit (local) |
|---|---|---|
| Entry point | `api/index.py` + `public/` | `streamlit_app.py` |
| Data | Live from the Open Calgary API, loaded per request into in-memory SQLite | Local SQLite file (`calgary_traffic.db`) synced by `etl.py` |
| Extras | Shareable URLs, CSV export, dark mode | Free-form read-only SQL explorer, year-over-year chart |

## Project layout

```
traffic/            shared package
  core.py           API client, categorisation, SQLite schema/loading
  queries.py        parameterised SQL used by both apps
  periods.py        date presets (Today, Last 7 days, …)
api/index.py        FastAPI backend (Vercel serverless function)
public/             static frontend (HTML/CSS/JS + Plotly)
streamlit_app.py    local Streamlit dashboard
etl.py              local ETL: Open Calgary API -> calgary_traffic.db
vercel.json         Vercel config
```

## Run the web app locally

```bash
python -m pip install -r requirements-dev.txt
```

```bash
python -m uvicorn api.index:app --reload --port 8000
```

Open http://localhost:8000. API docs at http://localhost:8000/api/docs.

## Deploy to Vercel

1. Push this folder to a GitHub repository.
2. In Vercel, **Add New → Project**, import the repo. Framework preset: **Other**; no build command needed.
3. (Optional) Add an environment variable `SOCRATA_APP_TOKEN` with a free
   [Socrata app token](https://data.calgary.ca/profile/edit/developer_settings) for higher API rate limits.
4. Deploy.

Or with the CLI:

```bash
npx vercel
```

How it works on Vercel: `public/` is served as static files and `/api/*` is routed to the
FastAPI function in `api/index.py`. Responses are cached at the edge (2 minutes for today's data,
15 minutes for longer ranges), so repeated visits are fast. The first load of very long ranges
(Last 12 months ≈ 10 s, All time ≈ 20 s) is slower because it downloads that range from the
City's API; `maxDuration` is set to 60 s in `vercel.json`. Only `requirements.txt` (FastAPI,
requests, tzdata) is installed on Vercel; local-only files are excluded via `.vercelignore`.

## Run the Streamlit dashboard locally

```bash
python -m pip install -r requirements-streamlit.txt
```

```bash
python etl.py
```

```bash
python -m streamlit run streamlit_app.py
```

The Streamlit app syncs new incidents automatically at most every 10 minutes. `python etl.py --full`
reloads everything.

## SQLite schema

Table `incidents`: `id`, `incident_info` (location), `description`, `category`, `lanes_blocked`,
`start_dt_utc`, `modified_dt_utc`, `start_dt_local`, `year`, `month`, `weekday` (0 = Monday),
`hour`, `quadrant`, `latitude`, `longitude`. Table `etl_runs` logs each sync.

```sql
SELECT hour, count(*) AS n
FROM incidents
WHERE weekday < 5
GROUP BY hour
ORDER BY n DESC;
```

## About the data

- Dataset: [Traffic Incidents (35ra-9556)](https://data.calgary.ca/Transportation-Transit/Traffic-Incidents/35ra-9556),
  published by The City of Calgary (Mobility). Updated continuously, December 2016 to present.
- The City describes it as an **unofficial archive** of incidents affecting traffic flow (signal issues,
  hazardous road conditions, stalled vehicles and unverified collisions) based on traffic-camera imagery.
  **It is not an official collision record** and may have gaps.
- The `category` column is this project's own classification (regular expressions over `description`),
  not an official field.

## Licence and attribution

Contains information licensed under the
[Open Government Licence – City of Calgary](https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa).

What the licence (v2.1) requires, and how this project complies:

1. Include the attribution statement and, where possible, a link to the licence → app footer,
   the "About the data" / "Source & licence" sections, and this README.
2. Don't use the City's names, crests or logos as branding → none are used.
3. Don't imply official status or endorsement → the app states it is independent.
4. Information is provided "as is", without warranty.
