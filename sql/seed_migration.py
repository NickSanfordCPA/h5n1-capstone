"""Seed the migration-geometry tables: county_migration_axis and county_proximity.

Derived reference structure (like county_adjacency): computed from dim_county
centroids + the static flyway assignment. Re-runnable — both upsert.

Usage (Cloud SQL Auth Proxy running):
    python sql/seed_migration.py           # proximity stored to R_MAX_KM
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.migration import county_proximity, migration_axis

# Store pairs out to 400 km although features operate at 300 km, so the radius can
# be tuned up to 400 without regenerating the table.
R_MAX_KM = 400.0

AXIS_UPSERT = text(
    """
    INSERT INTO county_migration_axis (fips, flyway, breeding_bearing_deg, wintering_bearing_deg)
    VALUES (:fips, :flyway, :breeding_bearing_deg, :wintering_bearing_deg)
    ON CONFLICT (fips) DO UPDATE SET
        flyway                = EXCLUDED.flyway,
        breeding_bearing_deg  = EXCLUDED.breeding_bearing_deg,
        wintering_bearing_deg = EXCLUDED.wintering_bearing_deg
    """
)

PROXIMITY_UPSERT = text(
    """
    INSERT INTO county_proximity (fips, neighbor_fips, distance_km, bearing_deg)
    VALUES (:fips, :neighbor_fips, :distance_km, :bearing_deg)
    ON CONFLICT (fips, neighbor_fips) DO UPDATE SET
        distance_km = EXCLUDED.distance_km,
        bearing_deg = EXCLUDED.bearing_deg
    """
)


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def main() -> None:
    with get_engine().connect() as conn:
        counties = pd.DataFrame(
            conn.execute(text(
                "SELECT fips, state, centroid_lat AS lat, centroid_lon AS lon FROM dim_county"
            )).all(),
            columns=["fips", "state", "lat", "lon"],
        )

    axis = migration_axis(counties)
    prox = county_proximity(counties, R_MAX_KM)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(AXIS_UPSERT, _records(axis))
        conn.execute(PROXIMITY_UPSERT, _records(prox))

    print(f"county_migration_axis: {len(axis)} rows")
    print(f"county_proximity:      {len(prox)} rows (<= {R_MAX_KM:.0f} km)")


if __name__ == "__main__":
    main()
