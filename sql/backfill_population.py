"""Backfill dim_county.population from the 2020 Decennial Census (one-time, idempotent).

The static human-population denominator (exact April-1-2020 enumerated count, P1_001N).
dim_county is seeded with population NULL (the Gazetteer has none); this fills it. Kept
separate from seed_dimensions.py, which deliberately preserves population on re-seed.

Re-runnable: a pure UPDATE by FIPS. Rows whose FIPS is not in dim_county update nothing;
any dim_county row the source doesn't cover stays NULL and is surfaced below.

Requires CENSUS_API_KEY in .env and the Cloud SQL Auth Proxy running.
    python sql/backfill_population.py
"""
from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.census import county_population_2020

load_dotenv()

UPDATE = text("UPDATE dim_county SET population = :population WHERE fips = :fips")


def main() -> None:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise SystemExit("CENSUS_API_KEY not set — add it to .env (api.census.gov/data/key_signup.html)")

    pops = county_population_2020(key)

    # Vintage guard: our locked geography must be present and the CT planning regions
    # absent, or we'd be filling the wrong counties (the 091xx trap, in reverse).
    fips = set(pops["fips"])
    for f in ("09009", "09011", "02158", "46102", "51019"):
        if f not in fips:
            raise SystemExit(f"expected traditional-vintage FIPS {f} missing from source — wrong vintage?")
    if any(f.startswith("091") and f != "09100" for f in fips) and "09110" in fips:
        raise SystemExit("source returned CT planning regions (091xx) — wrong vintage")

    records = pops.astype(object).where(pd.notna(pops), None).to_dict("records")

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(UPDATE, records)
        total = conn.execute(text("SELECT count(*) FROM dim_county")).scalar_one()
        nulls = conn.execute(text("SELECT count(*) FROM dim_county WHERE population IS NULL")).scalar_one()
        # sanity spot-checks against known 2020 counts
        checks = conn.execute(
            text("SELECT fips, county_name, state, population FROM dim_county "
                 "WHERE fips IN ('06037','01001','09009') ORDER BY fips")
        ).all()

    print(f"source counties (50 states + DC): {len(pops)}")
    print(f"dim_county: {total} rows, {total - nulls} populated, {nulls} still NULL")
    for fips_, name, state, pop in checks:
        print(f"  {fips_} {name}, {state}: {pop:,}")
    if nulls:
        print("  WARNING: some counties have no population — investigate FIPS mismatch")


if __name__ == "__main__":
    main()
