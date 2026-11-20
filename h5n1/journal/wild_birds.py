"""Reload APHIS wild-bird HPAI detections locally, keeping BOTH dates.

The shared `fact_wild_bird_detection.day` is the Collection Date (fallback Date Detected).
A detection was not knowable until Date Detected, a median 15 days later, so every trailing
wild-bird predictor built on it looks ahead. This module rebuilds, in the LOCAL database:

  public.fact_wild_bird_sample          one row per raw sample, with collection_date and
                                        detected_date (the record of truth for both dates)
  public.fact_wild_bird_detection       county x day, day = detected_date   (PRIMARY)
  public.fact_wild_bird_detection_coll  county x day, day = collection_date, falling back to
                                        detected_date (the legacy definition; sensitivity only)

The county-day tables keep the original (fips, day, detection_count, n_species) shape so
008, 011 and model_state_week.sql run unchanged and pick up the primary dating.

Before replacing it, the copied legacy table is kept as public.fact_wild_bird_detection_shared
and the rebuilt collection-date table is checked against it row for row.

    uv run python -m h5n1.journal.wild_birds
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import DATA_DIR, assert_local, local_engine
from h5n1.sources.fips_crosswalk import FipsResolver

RAW = DATA_DIR / "raw" / "hpai-wild-birds.csv"   # downloaded unchanged from gs://h5n1-raw

DDL = """
DROP TABLE IF EXISTS fact_wild_bird_sample;
CREATE TABLE fact_wild_bird_sample (
    sample_id        INTEGER PRIMARY KEY,          -- 1-based row number in the raw file
    fips             CHAR(5) NOT NULL REFERENCES dim_county,
    collection_date  DATE,                         -- when the bird was sampled; NULL if blank
    detected_date    DATE NOT NULL,                -- when the detection was confirmed
    lag_days         INTEGER,                      -- detected_date - collection_date
    hpai_strain      TEXT,
    bird_species     TEXT,
    woah_class       TEXT,
    sampling_method  TEXT,
    submitting_agency TEXT
);
COMMENT ON TABLE fact_wild_bird_sample IS
    'APHIS wild-bird HPAI detections, one row per raw sample, both dates. Journal rebuild.';
"""

AGG = """
-- CASCADE: the feature matviews read this table. They are rebuilt by build_baseline.py,
-- which must be run again after this script.
DROP TABLE IF EXISTS {table} CASCADE;
CREATE TABLE {table} (
    fips            CHAR(5) NOT NULL REFERENCES dim_county,
    day             DATE    NOT NULL REFERENCES dim_date,
    detection_count INTEGER NOT NULL,
    n_species       INTEGER,
    PRIMARY KEY (fips, day)
);
INSERT INTO {table}
SELECT fips, {day_expr} AS day, COUNT(*), COUNT(DISTINCT bird_species)
FROM fact_wild_bird_sample
GROUP BY 1, 2;
COMMENT ON TABLE {table} IS '{comment}';
"""


def main() -> None:
    eng = local_engine()
    assert_local(eng)
    df = pd.read_csv(RAW, dtype=str)
    df["sample_id"] = range(1, len(df) + 1)
    with eng.connect() as cn:
        counties = pd.read_sql(text("select state, county_name, fips from dim_county"), cn)
    resolver = FipsResolver(counties)
    df["fips"] = [resolver.resolve(s, c) for s, c in zip(df["State"], df["County"])]
    n_raw = len(df)
    unresolved = df[df["fips"].isna()]
    df = df[df["fips"].notna()].copy()
    if df["fips"].str.startswith("091").any():
        raise RuntimeError("091xx Connecticut planning-region FIPS found; expected 2021 vintage")

    coll = pd.to_datetime(df["Collection Date"], errors="coerce")
    det = pd.to_datetime(df["Date Detected"], errors="coerce")
    if det.isna().any():
        raise RuntimeError(f"{det.isna().sum()} rows have no Date Detected")
    out = pd.DataFrame({
        "sample_id": df["sample_id"], "fips": df["fips"],
        "collection_date": coll.dt.date.where(coll.notna(), None),
        "detected_date": det.dt.date,
        "lag_days": (det - coll).dt.days.astype("Int64"),
        "hpai_strain": df["HPAI Strain"], "bird_species": df["Bird Species"],
        "woah_class": df["WOAH Classification"].str.strip().str.capitalize(),
        "sampling_method": df["Sampling Method"].str.strip().str.capitalize(),
        "submitting_agency": df["Submitting Agency"],
    })

    with eng.begin() as cn:
        # keep the copied legacy table once, for the reconciliation below
        has_shared = cn.execute(text(
            "select to_regclass('public.fact_wild_bird_detection_shared')")).scalar()
        if has_shared is None:
            cn.execute(text("ALTER TABLE fact_wild_bird_detection "
                            "RENAME TO fact_wild_bird_detection_shared"))
            cn.execute(text("ALTER INDEX IF EXISTS fact_wild_bird_detection_pkey "
                            "RENAME TO fact_wild_bird_detection_shared_pkey"))
        cn.exec_driver_sql(DDL)
    out.to_sql("fact_wild_bird_sample", eng, if_exists="append", index=False,
               chunksize=5000, method="multi")
    with eng.begin() as cn:
        cn.exec_driver_sql(AGG.format(
            table="fact_wild_bird_detection", day_expr="detected_date",
            comment="County x day wild-bird detections dated by Date Detected (journal rebuild, "
                    "primary). Source of truth: fact_wild_bird_sample."))
        cn.exec_driver_sql(AGG.format(
            table="fact_wild_bird_detection_coll",
            day_expr="COALESCE(collection_date, detected_date)",
            comment="County x day wild-bird detections dated by Collection Date, fallback Date "
                    "Detected: the legacy definition. Sensitivity only."))
        q = lambda s: cn.execute(text(s)).scalar()
        print(f"raw rows {n_raw:,}; unresolved FIPS {len(unresolved):,}; samples loaded "
              f"{q('select count(*) from fact_wild_bird_sample'):,}")
        if len(unresolved):
            print(unresolved.groupby(["State", "County"]).size().sort_values(ascending=False)
                  .head(15).to_string())
        for t in ("fact_wild_bird_detection", "fact_wild_bird_detection_coll",
                  "fact_wild_bird_detection_shared"):
            print(f"{t}: {q(f'select count(*) from {t}'):,} county-days, "
                  f"{q(f'select sum(detection_count) from {t}'):,} detections, "
                  f"{q(f'select min(day) from {t}')} .. {q(f'select max(day) from {t}')}")
        diff = q("""
            select count(*) from fact_wild_bird_detection_coll a
            full join fact_wild_bird_detection_shared b using (fips, day)
            where a.fips is null or b.fips is null
               or a.detection_count <> b.detection_count
               or a.n_species is distinct from b.n_species""")
        print(f"collection-date rebuild vs shared legacy table: {diff} differing county-days")
        print(pd.read_sql(text("""
            select count(*) n, count(collection_date) with_collection,
                   percentile_cont(0.5) within group (order by lag_days) median_lag,
                   percentile_cont(0.25) within group (order by lag_days) p25,
                   percentile_cont(0.75) within group (order by lag_days) p75,
                   percentile_cont(0.9) within group (order by lag_days) p90,
                   count(*) filter (where lag_days < 0) negative_lag
            from fact_wild_bird_sample"""), cn).to_string(index=False))


if __name__ == "__main__":
    main()
