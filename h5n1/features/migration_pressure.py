"""Build feature_migration_pressure: directional migration-driven infection pressure.

Hypothesis: infection arrives in a county carried by migrating wild birds, from the
direction the birds are coming from — the breeding-ground side in fall, the
wintering-ground side in spring. For each county-day we sum recent WILD-BIRD HPAI
detections in each directional sector, distance-weighted, over a trailing window.
The time-series model is left to learn the seasonal weighting (breeding-side matters
in fall, wintering-side in spring), so no migration-phase term is imposed here.

PROVISIONAL PARAMETERS (first cut — all tunable):
- SECTOR_HALF_WIDTH_DEG = 45  -> a 90 deg quadrant around each axis bearing
- RADIUS_KM             = 300  (county_proximity is stored to 400 km, so this is a
                                query-time filter and can be raised to 400 freely)
- DECAY_SCALE_KM        = 150  -> weight = exp(-distance / 150 km)
- LAG_DAYS              = 21   -> trailing window [day-21, day-1], PAST ONLY (no
                                same-day term, to avoid leakage)
- Source                = fact_wild_bird_detection (the migrating vector; poultry
                                do not migrate, so they are excluded from transport)

    python -m h5n1.features.migration_pressure
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.migration import angular_diff

SECTOR_HALF_WIDTH_DEG = 45.0
RADIUS_KM = 300.0
DECAY_SCALE_KM = 150.0
LAG_DAYS = 21

UPSERT = text(
    """
    INSERT INTO feature_migration_pressure
        (fips, day, breeding_side_pressure, wintering_side_pressure)
    VALUES (:fips, :day, :breeding_side_pressure, :wintering_side_pressure)
    ON CONFLICT (fips, day) DO UPDATE SET
        breeding_side_pressure  = EXCLUDED.breeding_side_pressure,
        wintering_side_pressure = EXCLUDED.wintering_side_pressure
    """
)


def _sector_edges(prox: pd.DataFrame, axis: pd.DataFrame, bearing_col: str) -> pd.DataFrame:
    """Proximity pairs whose bearing falls in the county's sector, with a decay weight.

    Returns fips (target county C), neighbor_fips (source county N), weight.
    """
    e = prox.merge(axis[["fips", bearing_col]], on="fips")
    e = e[e["distance_km"] <= RADIUS_KM]
    e = e[angular_diff(e["bearing_deg"], e[bearing_col]) <= SECTOR_HALF_WIDTH_DEG]
    e = e.assign(weight=np.exp(-e["distance_km"] / DECAY_SCALE_KM))
    return e[["fips", "neighbor_fips", "weight"]]


def _side_pressure(edges: pd.DataFrame, det: pd.DataFrame) -> pd.DataFrame:
    """Trailing-window, distance-weighted detection sum per (county C, day D)."""
    # weighted contribution of each source county's detections to each target county
    j = edges.merge(det, left_on="neighbor_fips", right_on="fips", suffixes=("", "_src"))
    j["contrib"] = j["weight"] * j["detection_count"]
    daily = j.groupby(["fips", "day"], as_index=False)["contrib"].sum()  # (C, d', value)

    # spread each source day d' across the trailing window: it feeds days d'+1 .. d'+LAG_DAYS
    spread = [
        daily.assign(day=pd.to_datetime(daily["day"]) + pd.Timedelta(days=k))
        for k in range(1, LAG_DAYS + 1)
    ]
    s = pd.concat(spread, ignore_index=True)
    s["day"] = s["day"].dt.date
    return s.groupby(["fips", "day"], as_index=False)["contrib"].sum()


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        prox = pd.DataFrame(
            conn.execute(text("SELECT fips, neighbor_fips, distance_km, bearing_deg FROM county_proximity")).all(),
            columns=["fips", "neighbor_fips", "distance_km", "bearing_deg"],
        )
        axis = pd.DataFrame(
            conn.execute(text("SELECT fips, breeding_bearing_deg, wintering_bearing_deg FROM county_migration_axis")).all(),
            columns=["fips", "breeding_bearing_deg", "wintering_bearing_deg"],
        )
        det = pd.DataFrame(
            conn.execute(text("SELECT fips, day, detection_count FROM fact_wild_bird_detection")).all(),
            columns=["fips", "day", "detection_count"],
        )

    breeding = _side_pressure(_sector_edges(prox, axis, "breeding_bearing_deg"), det)
    breeding = breeding.rename(columns={"contrib": "breeding_side_pressure"})
    wintering = _side_pressure(_sector_edges(prox, axis, "wintering_bearing_deg"), det)
    wintering = wintering.rename(columns={"contrib": "wintering_side_pressure"})

    feat = breeding.merge(wintering, on=["fips", "day"], how="outer")

    with engine.begin() as conn:
        conn.execute(UPSERT, _records(feat))
    print(f"feature_migration_pressure: {len(feat)} county-days "
          f"(breeding {breeding.shape[0]}, wintering {wintering.shape[0]})")


if __name__ == "__main__":
    main()
