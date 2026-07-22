"""USDA/APHIS HPAI ingestion — poultry (fact_h5n1_outbreak) and wild birds
(fact_wild_bird_detection).

Reference implementation of the source pattern: read the immutable raw file from
gs://h5n1-raw, resolve place names to FIPS via the shared crosswalk, aggregate to
the canonical county x day grain, and upsert idempotently. NOAA/USGS loaders copy
this shape. Re-running is safe: both loads upsert on the primary key.

Files were downloaded manually from the APHIS dashboards (a Tableau export for
poultry; a CSV for wild birds) and uploaded to the raw bucket. This is a one-shot
snapshot load, not a scheduled pull.

    python -m h5n1.sources.usda           # loads both from gs://h5n1-raw
"""
from __future__ import annotations

import io

import pandas as pd
from google.cloud import storage
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.census import counties_dataframe
from h5n1.sources.fips_crosswalk import FipsResolver

RAW_BUCKET = "h5n1-raw"
POULTRY_OBJECT = "APHIS Confirmed Detections.xlsx"
WILD_OBJECT = "hpai-wild-birds.csv"

OUTBREAK_UPSERT = text(
    """
    INSERT INTO fact_h5n1_outbreak (fips, day, flock_type, confirmed_flag, birds_affected)
    VALUES (:fips, :day, :flock_type, TRUE, :birds_affected)
    ON CONFLICT (fips, day, flock_type) DO UPDATE SET
        confirmed_flag = TRUE,
        birds_affected = EXCLUDED.birds_affected
    """
)

WILD_UPSERT = text(
    """
    INSERT INTO fact_wild_bird_detection (fips, day, detection_count, n_species)
    VALUES (:fips, :day, :detection_count, :n_species)
    ON CONFLICT (fips, day) DO UPDATE SET
        detection_count = EXCLUDED.detection_count,
        n_species       = EXCLUDED.n_species
    """
)


def _read_raw(object_name: str) -> bytes:
    blob = storage.Client().bucket(RAW_BUCKET).blob(object_name)
    return blob.download_as_bytes()


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def _report_drops(name: str, total: int, kept: pd.DataFrame, resolver_col: str) -> None:
    dropped = total - len(kept)
    print(f"  {name}: {total} source rows, {len(kept)} resolved, {dropped} dropped (unmatched FIPS)")


def load_poultry(resolver: FipsResolver, conn) -> None:
    df = pd.read_excel(io.BytesIO(_read_raw(POULTRY_OBJECT)), dtype=str)
    df["fips"] = [resolver.resolve(s, c) for s, c in zip(df["State"], df["County Name"])]
    total = len(df)
    df = df[df["fips"].notna()].copy()
    _report_drops("poultry", total, df, "fips")

    df["day"] = pd.to_datetime(df["Confirmed Diagnosis"], errors="coerce").dt.date
    df["flock_type"] = df["Production"].fillna("unknown").str.strip()
    df["birds_affected"] = pd.to_numeric(df["Affected"], errors="coerce")
    df = df[df["day"].notna()]

    # Multiple premises can share a county-day-flock_type; sum birds to the PK grain.
    agg = (
        df.groupby(["fips", "day", "flock_type"], as_index=False)["birds_affected"]
        .sum(min_count=1)
    )
    conn.execute(OUTBREAK_UPSERT, _records(agg))
    print(f"  fact_h5n1_outbreak: {len(agg)} rows upserted")


def load_wild(resolver: FipsResolver, conn) -> None:
    df = pd.read_csv(io.BytesIO(_read_raw(WILD_OBJECT)), dtype=str)
    df["fips"] = [resolver.resolve(s, c) for s, c in zip(df["State"], df["County"])]
    total = len(df)
    df = df[df["fips"].notna()].copy()
    _report_drops("wild", total, df, "fips")

    # Collection Date is when the bird was sampled (the presence signal); fall back
    # to Date Detected (lab confirmation) when collection is blank.
    coll = pd.to_datetime(df["Collection Date"], errors="coerce")
    det = pd.to_datetime(df["Date Detected"], errors="coerce")
    df["day"] = coll.fillna(det).dt.date
    df = df[df["day"].notna()]

    agg = df.groupby(["fips", "day"], as_index=False).agg(
        detection_count=("Bird Species", "size"),
        n_species=("Bird Species", "nunique"),
    )
    conn.execute(WILD_UPSERT, _records(agg))
    print(f"  fact_wild_bird_detection: {len(agg)} rows upserted")


def main() -> None:
    resolver = FipsResolver(counties_dataframe())
    engine = get_engine()
    with engine.begin() as conn:
        print("Loading poultry (fact_h5n1_outbreak):")
        load_poultry(resolver, conn)
        print("Loading wild birds (fact_wild_bird_detection):")
        load_wild(resolver, conn)
    print("done")


if __name__ == "__main__":
    main()
