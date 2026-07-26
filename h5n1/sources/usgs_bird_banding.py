"""USGS North American Bird Banding Program (NABBP) ingestion -> fact_bird_density.

Source: NABBP 2025 data request (ScienceBase item 68837a85d4be027deba86316), banding
and encounter records for the 9 species recommended_h5n1_high_risk_species.csv flags
as highest H5N1-relevance (Mallard, Canada Goose, Wood Duck, Large Canada Goose,
Blue-winged Teal, Green-winged Teal, Northern Pintail, Lesser Snow Goose, Common
Tern). The raw NABBP release is ~13GB across 57 files covering every species since
the 1960s; what's archived to gs://h5n1-raw is a pre-filtered snapshot (9 species,
2018+) so the loader doesn't need to pull or store data this project never uses.
See manifests/usgs_bird_banding.json for the snapshot's source URL and checksum.

The archived snapshot covers 2018+, but this loader clips to MIN_YEAR=2020: dim_date
is currently only seeded from 2020-01-01 onward, and a day outside its range would
violate fact_bird_density's FK and abort the whole upsert. Raising MIN_YEAR back to
2018 is safe once dim_date is extended (h5n1/dates.py: "extending it later is safe").

Grain: fact_bird_density is (fips, day). We use only event_type='B' (banding, i.e.
the bird's capture location) rather than 'E' (encounter/recovery) -- encounters carry
a different geographic and reporting bias (e.g. hunter-harvest clustering) that would
confound a presence signal. Records are further restricted to iso_country='US' (only
US bands resolve against dim_county) and to coordinates_precision_code finer than
state/country centroid (12, 72) -- assigning those to one county would fabricate
precision the data doesn't carry. See NABBP_lookups_2025/coordinates_precision.csv.

band_count_smoothed currently holds the RAW daily county count, not a smoothed
value -- despite the column name inherited from sql/002_facts.sql's strawman. Actual
smoothing (e.g. a rolling window) belongs in the L2 feature layer (h5n1/features/),
not baked into the fact table, so it can be tuned without reloading this source.

Bands carry lat/lon (blocked to a privacy grid, mostly 10-minute or 1-degree blocks),
not place names, so this source resolves FIPS by nearest county centroid rather than
the name-based h5n1.sources.fips_crosswalk -- same technique as h5n1.sources.noaa,
just point-to-county instead of county-to-station. Only ~600-700 distinct blocked
coordinates appear even across ~1.2M banding records, so a plain vectorized pairwise
haversine against all of dim_county is fast; no spatial index needed.

    python -m h5n1.sources.usgs_bird_banding      # loads from gs://h5n1-raw
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
from google.cloud import storage
from sqlalchemy import text

from h5n1.db import get_engine

RAW_BUCKET = "h5n1-raw"
RAW_OBJECT = "banding_recent_top_risk_species_clean.csv"

# The 9 species_id values in the archived snapshot (species_name is free-text and
# inconsistent -- typos, case variants -- species_id is the reliable key).
SPECIES_IDS = {"1320", "1720", "1440", "1723", "1400", "1390", "1430", "1690", "0700"}

# coordinates_precision_code values coarser than a county: 12 = state centroid,
# 72 = country centroid. See NABBP_lookups_2025/coordinates_precision.csv.
EXCLUDED_PRECISION = {"12.0", "72.0"}

# dim_date currently only spans 2020-01-01 onward; a day outside that range would
# violate fact_bird_density's FK and abort the whole upsert. The archived snapshot
# covers 2018+, so this clips ~85k pre-2020 rows rather than extending dim_date.
MIN_YEAR = 2020

BIRD_DENSITY_UPSERT = text(
    """
    INSERT INTO fact_bird_density (fips, day, band_count_smoothed)
    VALUES (:fips, :day, :band_count_smoothed)
    ON CONFLICT (fips, day) DO UPDATE SET
        band_count_smoothed = EXCLUDED.band_count_smoothed
    """
)


def _read_raw() -> pd.DataFrame:
    blob = storage.Client().bucket(RAW_BUCKET).blob(RAW_OBJECT)
    raw = blob.download_as_bytes()
    return pd.read_csv(io.BytesIO(raw), dtype=str)


def _filtered(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df)
    year = pd.to_numeric(df["event_year"], errors="coerce")
    keep = (
        (df["iso_country"] == "US")
        & (df["event_type"] == "B")
        & (df["species_id"].isin(SPECIES_IDS))
        & (year >= MIN_YEAR)
        & (~df["coordinates_precision_code"].isin(EXCLUDED_PRECISION))
    )
    out = df[keep].copy()
    print(f"  {total} raw rows -> {len(out)} after US/banding/species/year/precision filters")
    return out


def nearest_fips(points: pd.DataFrame, counties: pd.DataFrame) -> pd.DataFrame:
    """Nearest county centroid (great-circle) for each point.

    points: columns lat, lon (typically de-duplicated first -- see load()).
    counties: columns fips, lat, lon.
    """
    lat1 = np.radians(points["lat"].to_numpy())[:, None]
    lon1 = np.radians(points["lon"].to_numpy())[:, None]
    lat2 = np.radians(counties["lat"].to_numpy())[None, :]
    lon2 = np.radians(counties["lon"].to_numpy())[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    dist_km = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    nearest_idx = np.argmin(dist_km, axis=1)
    return pd.DataFrame(
        {
            "lat": points["lat"].to_numpy(),
            "lon": points["lon"].to_numpy(),
            "fips": counties["fips"].to_numpy()[nearest_idx],
            "dist_km": dist_km[np.arange(len(nearest_idx)), nearest_idx],
        }
    )


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def load(conn) -> None:
    df = _filtered(_read_raw())

    df["lat"] = pd.to_numeric(df["lat_dd"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon_dd"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])

    # event_year/month/day are already-normalized integer components -- build the
    # date from those rather than parsing event_date's string format.
    ymd = df[["event_year", "event_month", "event_day"]].apply(pd.to_numeric, errors="coerce")
    df["day"] = pd.to_datetime(
        dict(year=ymd["event_year"], month=ymd["event_month"], day=ymd["event_day"]),
        errors="coerce",
    ).dt.date
    dropped = df["day"].isna().sum()
    if dropped:
        print(f"  dropping {dropped} rows with unparseable event dates")
    df = df.dropna(subset=["day"])

    counties = pd.DataFrame(
        conn.execute(
            text("SELECT fips, centroid_lat AS lat, centroid_lon AS lon FROM dim_county")
        ).all(),
        columns=["fips", "lat", "lon"],
    ).dropna(subset=["lat", "lon"])

    uniq = df[["lat", "lon"]].drop_duplicates()
    resolved = nearest_fips(uniq, counties)
    print(
        f"  {len(uniq)} unique coordinates resolved to counties; "
        f"max match distance {resolved['dist_km'].max():.0f} km"
    )
    df = df.merge(resolved[["lat", "lon", "fips"]], on=["lat", "lon"], how="left")

    agg = (
        df.groupby(["fips", "day"], as_index=False)
        .size()
        .rename(columns={"size": "band_count_smoothed"})
    )
    conn.execute(BIRD_DENSITY_UPSERT, _records(agg))
    print(f"  fact_bird_density: {len(agg)} county-day rows upserted")


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        print("Loading USGS bird banding (fact_bird_density):")
        load(conn)
    print("done")


if __name__ == "__main__":
    main()
