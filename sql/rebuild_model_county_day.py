"""Rebuild the frozen modeling table `model_county_day` from feature_county_day.

`model_county_day` is a physical snapshot, not a matview, so that a fitted model can be
tied to a known vintage (see the header of 012_model_county_day.sql). The flip side is
that it does NOT follow feature_county_day: after any refresh of the 008 -> 009 -> 011
chain it is stale until this script runs.

`run_migrations.py` records each .sql file in schema_migrations and skips it thereafter,
so re-running the migration will not rebuild anything. This script executes the same
file directly, bypassing that ledger -- one source of truth for the column list.

Usage (Cloud SQL Auth Proxy on 5433, or a local Postgres):
    uv run python sql/rebuild_model_county_day.py
    uv run python sql/rebuild_model_county_day.py --refresh-upstream

--refresh-upstream refreshes the three upstream matviews first, in the only order that
does not carry stale lags forward.
"""

from __future__ import annotations

import argparse
import pathlib

from sqlalchemy import text

from h5n1.db import get_engine

MIGRATION = pathlib.Path(__file__).parent / "012_model_county_day.sql"

# Order matters: feature_county_day reads the other two. Refreshing it alone silently
# carries stale spatial and weather lags. Mirrors the note in 011.
UPSTREAM = [
    "feature_spatial_lag",
    "feature_weather_lag",
    "feature_county_day",
]


def refresh_upstream(conn) -> None:
    conn.exec_driver_sql("SET work_mem = '64MB'")
    for view in UPSTREAM:
        print(f"refresh {view} ...", flush=True)
        conn.exec_driver_sql(f"REFRESH MATERIALIZED VIEW {view}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--refresh-upstream",
        action="store_true",
        help="REFRESH feature_spatial_lag, feature_weather_lag, feature_county_day first",
    )
    args = ap.parse_args()

    engine = get_engine()
    with engine.begin() as conn:
        if args.refresh_upstream:
            refresh_upstream(conn)

        print(f"rebuild model_county_day from {MIGRATION.name} ...", flush=True)
        # psycopg runs multi-statement files; exec_driver_sql skips placeholder parsing,
        # which the migration runner needs too (a literal percent sign would otherwise
        # raise "incomplete placeholder").
        conn.exec_driver_sql(MIGRATION.read_text())

        row = conn.execute(
            text(
                """
                SELECT built_at, n_rows, n_predictors, source_max_day, feature_set
                FROM model_build_log
                WHERE table_name = 'model_county_day'
                ORDER BY build_id DESC
                LIMIT 1
                """
            )
        ).one()

    print(
        f"built {row.n_rows:,} rows x {row.n_predictors} predictors "
        f"through {row.source_max_day}  ({row.feature_set})"
    )
    print(f"vintage: {row.built_at}")


if __name__ == "__main__":
    main()
