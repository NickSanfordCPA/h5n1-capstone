"""Seed the county/time backbone: dim_county, dim_date, and county_adjacency.

These are what every fact_* table foreign-keys to, so this must run once (after
migrations) before any source data can load. Re-runnable: dim_county upserts by
FIPS, dim_date by day, county_adjacency inserts-or-ignores.

Usage (Cloud SQL Auth Proxy running):
    python sql/seed_dimensions.py                      # default 2022-01-01..2026-12-31
    python sql/seed_dimensions.py 2022-01-01 2027-12-31
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd
from sqlalchemy import text

from h5n1.dates import date_dimension
from h5n1.db import get_engine
from h5n1.sources.census import counties_dataframe
from h5n1.sources.census_adjacency import adjacency_dataframe

DEFAULT_START = date(2022, 1, 1)
DEFAULT_END = date(2026, 12, 31)

# population is deliberately NOT overwritten on conflict: the Gazetteer has none,
# so a later ACS backfill must survive a re-seed.
COUNTY_UPSERT = text(
    """
    INSERT INTO dim_county
        (fips, state, county_name, centroid_lat, centroid_lon, population, land_area_sqmi)
    VALUES
        (:fips, :state, :county_name, :centroid_lat, :centroid_lon, :population, :land_area_sqmi)
    ON CONFLICT (fips) DO UPDATE SET
        state          = EXCLUDED.state,
        county_name    = EXCLUDED.county_name,
        centroid_lat   = EXCLUDED.centroid_lat,
        centroid_lon   = EXCLUDED.centroid_lon,
        land_area_sqmi = EXCLUDED.land_area_sqmi
    """
)

DATE_UPSERT = text(
    """
    INSERT INTO dim_date
        (day, year, month, iso_week, day_of_year, season, is_us_holiday)
    VALUES
        (:day, :year, :month, :iso_week, :day_of_year, :season, :is_us_holiday)
    ON CONFLICT (day) DO UPDATE SET
        year          = EXCLUDED.year,
        month         = EXCLUDED.month,
        iso_week      = EXCLUDED.iso_week,
        day_of_year   = EXCLUDED.day_of_year,
        season        = EXCLUDED.season,
        is_us_holiday = EXCLUDED.is_us_holiday
    """
)


ADJACENCY_INSERT = text(
    """
    INSERT INTO county_adjacency (fips, neighbor_fips)
    VALUES (:fips, :neighbor_fips)
    ON CONFLICT (fips, neighbor_fips) DO NOTHING
    """
)


def _records(df: pd.DataFrame) -> list[dict]:
    """Rows as dicts with pandas NaN coerced to None (SQL NULL)."""
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def main(start: date, end: date) -> None:
    counties = counties_dataframe()
    dates = date_dimension(start, end)
    adjacency = adjacency_dataframe()

    # An adjacency endpoint outside the county master would fail the FK and abort
    # the whole load. Filter to resolvable pairs and surface anything dropped, so a
    # missed FIPS-vintage change is visible rather than silent.
    valid = set(counties["fips"])
    mask = adjacency["fips"].isin(valid) & adjacency["neighbor_fips"].isin(valid)
    offenders = (
        set(adjacency.loc[~mask, "fips"]) | set(adjacency.loc[~mask, "neighbor_fips"])
    ) - valid
    adjacency = adjacency[mask]

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(COUNTY_UPSERT, _records(counties))
        conn.execute(DATE_UPSERT, _records(dates))
        conn.execute(ADJACENCY_INSERT, _records(adjacency))

    print(f"dim_county:       {len(counties)} rows")
    print(f"dim_date:         {len(dates)} rows  ({start} .. {end})")
    print(f"county_adjacency: {len(adjacency)} rows")
    if offenders:
        print(f"  WARNING: dropped pairs referencing {len(offenders)} non-master FIPS: "
              f"{sorted(offenders)}")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
    else:
        start, end = DEFAULT_START, DEFAULT_END
    main(start, end)
