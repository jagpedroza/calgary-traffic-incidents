"""
ETL: download Calgary traffic incidents from the official Open Calgary API
(Socrata SODA) into a local SQLite database used by the Streamlit app.

Source: The City of Calgary — Open Calgary, "Traffic Incidents" (35ra-9556)
Contains information licensed under the Open Government Licence – City of Calgary.

Usage:
    python etl.py            # incremental load (new/modified records only)
    python etl.py --full     # full reload
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from traffic import core

DB_PATH = Path(__file__).with_name("calgary_traffic.db")


def run(full: bool = False, app_token: str | None = None, db_path: Path = DB_PATH, quiet: bool = False) -> int:
    log = (lambda *a: None) if quiet else print
    con = sqlite3.connect(db_path)
    con.executescript(core.SCHEMA)
    since = None
    if full:
        con.execute("DELETE FROM incidents")
    else:
        since = con.execute("SELECT max(modified_dt_utc) FROM incidents").fetchone()[0]

    log(f"Downloading {'everything' if since is None else f'changes since {since}'} from {core.API_URL}")
    total = 0
    for page in core.fetch(f"modified_dt_utc >= '{since}'" if since else None, app_token=app_token):
        total += core.insert(con, page)
        con.commit()
        log(f"  {total:,} records processed")
    core.recategorize(con)
    con.execute("INSERT INTO etl_runs VALUES (?,?,?)", (
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "full" if since is None else "incremental", total))
    con.commit()
    n = con.execute("SELECT count(*) FROM incidents").fetchone()[0]
    con.close()
    log(f"Done. {total:,} records loaded; {n:,} total in {db_path.name}")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="delete and reload everything")
    parser.add_argument("--app-token", help="optional Socrata app token for higher rate limits")
    args = parser.parse_args()
    run(full=args.full, app_token=args.app_token)
