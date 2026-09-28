"""Rebuild a frozen modeling table: `model_county_day` (baseline) or `model_county_day_gdelt`.

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
    uv run python sql/rebuild_model_county_day.py --variant gdelt

--refresh-upstream refreshes the three upstream matviews first, in the only order that
does not carry stale lags forward. It rebuilds the BASELINE only; the gdelt variant
reads model_county_day as-is, so rebuild the baseline first when it is stale.

--variant gdelt refreshes feature_gdelt_county_day (014), then builds
model_county_day_gdelt from sql/builds/model_county_day_gdelt.sql.
"""

from __future__ import annotations

import argparse
import pathlib

from sqlalchemy import text

from h5n1.db import get_engine

SQL_DIR = pathlib.Path(__file__).parent
VARIANTS = {
    "baseline": ("model_county_day", SQL_DIR / "012_model_county_day.sql"),
    "gdelt": ("model_county_day_gdelt", SQL_DIR / "builds" / "model_county_day_gdelt.sql"),
}

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
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="baseline")
    args = ap.parse_args()
    table, sql_file = VARIANTS[args.variant]

    engine = get_engine()
    with engine.begin() as conn:
        if args.refresh_upstream:
            refresh_upstream(conn)

        if args.variant == "gdelt":
            # Plain REFRESH, not CONCURRENTLY: the view is created WITH NO DATA and
            # CONCURRENTLY refuses an unpopulated view.
            conn.exec_driver_sql("SET work_mem = '64MB'")
            print("refresh feature_gdelt_county_day ...", flush=True)
            conn.exec_driver_sql("REFRESH MATERIALIZED VIEW feature_gdelt_county_day")

        print(f"rebuild {table} from {sql_file.name} ...", flush=True)
        # psycopg runs multi-statement files; exec_driver_sql skips placeholder parsing,
        # which the migration runner needs too (a literal percent sign would otherwise
        # raise "incomplete placeholder").
        conn.exec_driver_sql(sql_file.read_text())

        row = conn.execute(
            text(
                """
                SELECT built_at, n_rows, n_predictors, source_max_day, feature_set
                FROM model_build_log
                WHERE table_name = :t
                ORDER BY build_id DESC
                LIMIT 1
                """
            ),
            {"t": table},
        ).one()

    print(
        f"built {table}: {row.n_rows:,} rows x {row.n_predictors} predictors "
        f"through {row.source_max_day}  ({row.feature_set})"
    )
    print(f"vintage: {row.built_at}")


if __name__ == "__main__":
    main()
