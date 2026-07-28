"""USDA NASS Census of Agriculture 2022 poultry exposure -> county_poultry_census.

Answers the question no other source in this project answers: how much poultry is
there to infect? Every other predictor measures infection PRESSURE (nearby detections,
migration geometry, weather); none measure EXPOSURE. Without it the model's easiest
win is just learning where poultry farms are, and dim_county.population is a actively
misleading denominator for a poultry DV -- a county with three commercial layer
complexes and 5,000 residents is a high-risk county.

Source: the keyless NASS Quick Stats bulk census file
    https://www.nass.usda.gov/datasets/qs.census2022.txt.gz
~295 MB gzipped, 6.08M rows, tab-delimited. Unlike the APHIS dashboards (which need a
Tableau session handshake and were hand-downloaded), this is a plain static URL, so
acquisition is fully automated here. We stream-filter it to the 119,767 county-level
POULTRY rows before anything touches disk, then archive that subset to GCS -- the same
"archive the pre-filtered snapshot, not the 13GB corpus" decision made for the NABBP
banding load in h5n1/sources/usgs_bird_banding.py.

    python -m h5n1.sources.nass_poultry --acquire   # download + archive to GCS, then load
    python -m h5n1.sources.nass_poultry             # load from the existing GCS snapshot

VINTAGE (checked, not assumed -- this project has been bitten before): NASS 2022
reports Connecticut by TRADITIONAL counties 09001-09015, NOT the 091xx planning
regions, so it is 1:1 with our 2021-vintage dim_county. 3,052 counties appear; 3,051
resolve. The single orphan is 02010 "Aleutian Islands", a pre-1980 Alaska code NASS
still uses for agricultural reporting (it was split into Aleutians East 02013 and
Aleutians West 02016); it is dropped, with no poultry consequence.

SUPPRESSION is the thing to understand before using this table. NASS prints "(D)" for
values that would disclose an individual operation. Measured on the real file:
    OPERATIONS counts:  0% suppressed on every measure
    INVENTORY values:   11.4% layers, 22.2% broilers, 28.3% turkeys, 15.9% ducks,
                        28.1% poultry sales $
So operations counts are the complete-but-coarse exposure measure and inventory is the
rich-but-holey one. We densify to all 3,143 counties and encode three distinct states:
    operations = 0, inventory = 0     -> genuinely no such commodity (92 counties have
                                          no poultry at all; they are real zeros)
    operations > 0, inventory IS NULL -> has the commodity, amount WITHHELD (MISSING)
    operations > 0, inventory > 0     -> fully reported
Coalescing the middle case to zero would tell the model that Lancaster-County-scale
operations are empty. Don't.

Broilers use SALES IN HEAD rather than inventory: they grow in ~6-week cycles, so a
census-day headcount understates annual throughput by roughly an order of magnitude.
Layers and turkeys use inventory, which is the meaningful standing-flock measure.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import sys
import urllib.request

import pandas as pd
from google.cloud import storage
from sqlalchemy import text

from h5n1.db import get_engine

BULK_URL = "https://www.nass.usda.gov/datasets/qs.census2022.txt.gz"
RAW_BUCKET = "h5n1-raw"
RAW_OBJECT = "nass_poultry/qs_census2022_county_poultry.tsv"
CENSUS_YEAR = 2022

# NASS SHORT_DESC -> our column. Only DOMAIN_DESC='TOTAL' rows are used; the INVENTORY
# and SALES domains break the same totals down by operation size and would double-count.
MEASURES = {
    "POULTRY TOTALS - OPERATIONS WITH INVENTORY":       "poultry_operations",
    "CHICKENS, LAYERS - OPERATIONS WITH INVENTORY":     "layer_operations",
    "CHICKENS, LAYERS - INVENTORY":                     "layer_inventory",
    "CHICKENS, BROILERS - OPERATIONS WITH SALES":       "broiler_operations",
    "CHICKENS, BROILERS - SALES, MEASURED IN HEAD":     "broiler_sales_head",
    "TURKEYS - OPERATIONS WITH INVENTORY":              "turkey_operations",
    "TURKEYS - INVENTORY":                              "turkey_inventory",
    "DUCKS - OPERATIONS WITH INVENTORY":                "duck_operations",
    "DUCKS - INVENTORY":                                "duck_inventory",
    "POULTRY TOTALS, INCL EGGS - SALES, MEASURED IN $": "poultry_sales_usd",
}

# Each magnitude column and the operations count that governs whether a NULL in it
# means "none here" (operations = 0) or "withheld" (operations > 0).
MAGNITUDE_GOVERNED_BY = {
    "layer_inventory":    "layer_operations",
    "broiler_sales_head": "broiler_operations",
    "turkey_inventory":   "turkey_operations",
    "duck_inventory":     "duck_operations",
    "poultry_sales_usd":  "poultry_operations",
}

OPERATION_COLS = ["poultry_operations", "layer_operations", "broiler_operations",
                  "turkey_operations", "duck_operations"]

UPSERT = text(
    """
    INSERT INTO county_poultry_census (
        fips, census_year, poultry_operations,
        layer_operations, layer_inventory,
        broiler_operations, broiler_sales_head,
        turkey_operations, turkey_inventory,
        duck_operations, duck_inventory,
        poultry_sales_usd)
    VALUES (
        :fips, :census_year, :poultry_operations,
        :layer_operations, :layer_inventory,
        :broiler_operations, :broiler_sales_head,
        :turkey_operations, :turkey_inventory,
        :duck_operations, :duck_inventory,
        :poultry_sales_usd)
    ON CONFLICT (fips, census_year) DO UPDATE SET
        poultry_operations = EXCLUDED.poultry_operations,
        layer_operations   = EXCLUDED.layer_operations,
        layer_inventory    = EXCLUDED.layer_inventory,
        broiler_operations = EXCLUDED.broiler_operations,
        broiler_sales_head = EXCLUDED.broiler_sales_head,
        turkey_operations  = EXCLUDED.turkey_operations,
        turkey_inventory   = EXCLUDED.turkey_inventory,
        duck_operations    = EXCLUDED.duck_operations,
        duck_inventory     = EXCLUDED.duck_inventory,
        poultry_sales_usd  = EXCLUDED.poultry_sales_usd
    """
)


def acquire() -> str:
    """Stream the NASS bulk file, keep county POULTRY rows, archive to GCS.

    The full file is 6.08M rows; filtering during the stream means the 295 MB download
    never lands on disk in full. Returns the sha256 of the archived subset.
    """
    req = urllib.request.Request(BULK_URL, headers={"User-Agent": "h5n1-capstone/1.0"})
    buf = io.StringIO()
    kept = total = 0
    print(f"  streaming {BULK_URL}")
    with urllib.request.urlopen(req, timeout=300) as resp:
        text_stream = io.TextIOWrapper(gzip.GzipFile(fileobj=resp), encoding="utf-8",
                                       errors="replace")
        reader = csv.DictReader(text_stream, delimiter="\t")
        writer = csv.DictWriter(buf, fieldnames=reader.fieldnames, delimiter="\t",
                               lineterminator="\n")
        writer.writeheader()
        for row in reader:
            total += 1
            if row.get("AGG_LEVEL_DESC") == "COUNTY" and row.get("GROUP_DESC") == "POULTRY":
                writer.writerow(row)
                kept += 1
    payload = buf.getvalue().encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    storage.Client().bucket(RAW_BUCKET).blob(RAW_OBJECT).upload_from_string(payload)
    print(f"  scanned {total:,} rows, archived {kept:,} county poultry rows "
          f"to gs://{RAW_BUCKET}/{RAW_OBJECT}")
    print(f"  sha256 {digest}")
    return digest


def _read_raw() -> pd.DataFrame:
    blob = storage.Client().bucket(RAW_BUCKET).blob(RAW_OBJECT)
    return pd.read_csv(io.BytesIO(blob.download_as_bytes()), sep="\t", dtype=str)


def _parse_value(s: pd.Series) -> pd.Series:
    """NASS VALUE -> float. '(D)' withheld and '(NA)' become NaN; '(Z)' is a real zero."""
    v = s.str.strip().str.replace(",", "", regex=False)
    v = v.replace({"(D)": None, "(NA)": None, "(X)": None, "(Z)": "0"})
    return pd.to_numeric(v, errors="coerce")


def transform(raw: pd.DataFrame, master_fips: list[str]) -> pd.DataFrame:
    """Pivot the long NASS rows to one row per county, densified over dim_county."""
    t = raw[(raw["DOMAIN_DESC"] == "TOTAL") & (raw["SHORT_DESC"].isin(MEASURES))].copy()
    t["fips"] = t["STATE_FIPS_CODE"].str.zfill(2) + t["COUNTY_CODE"].str.zfill(3)
    t["col"] = t["SHORT_DESC"].map(MEASURES)
    t["val"] = _parse_value(t["VALUE"])

    orphans = sorted(set(t["fips"]) - set(master_fips))
    if orphans:
        print(f"  dropping {len(orphans)} FIPS not in dim_county: {orphans}")
        t = t[t["fips"].isin(master_fips)]

    wide = t.pivot_table(index="fips", columns="col", values="val", aggfunc="first")
    # Densify: a county absent from the census poultry rows has no poultry, which is a
    # real zero, not missing data. Reindexing before the fill is what makes that explicit.
    wide = wide.reindex(master_fips)
    for c in MEASURES.values():
        if c not in wide.columns:
            wide[c] = pd.NA

    # Operations counts are never suppressed, so any NaN here is a genuine absence.
    wide[OPERATION_COLS] = wide[OPERATION_COLS].fillna(0)

    # A magnitude is only a real zero when its governing operations count is zero.
    # Where operations > 0 the NaN is NASS withholding it, and must stay NULL.
    for mag, ops in MAGNITUDE_GOVERNED_BY.items():
        wide[mag] = wide[mag].where(~((wide[mag].isna()) & (wide[ops] == 0)), 0)

    wide = wide.reset_index().rename(columns={"index": "fips"})
    wide["census_year"] = CENSUS_YEAR
    return wide


def _records(df: pd.DataFrame) -> list[dict]:
    cols = ["fips", "census_year"] + list(MEASURES.values())
    out = df[cols].copy()
    for c in MEASURES.values():
        out[c] = out[c].astype("object").where(out[c].notna(), None)
    return out.to_dict("records")


def load(conn) -> None:
    master = [r[0] for r in conn.execute(text("SELECT fips FROM dim_county ORDER BY fips")).all()]
    wide = transform(_read_raw(), master)
    conn.execute(UPSERT, _records(wide))

    supp = {mag: int(((wide[mag].isna()) & (wide[ops] > 0)).sum())
            for mag, ops in MAGNITUDE_GOVERNED_BY.items()}
    print(f"  county_poultry_census: {len(wide)} counties upserted for {CENSUS_YEAR}")
    print(f"  counties with any poultry operation: "
          f"{int((wide['poultry_operations'] > 0).sum())}")
    print(f"  suppressed (has operations, value withheld): {supp}")


def main() -> None:
    if "--acquire" in sys.argv:
        acquire()
    engine = get_engine()
    with engine.begin() as conn:
        print("Loading NASS poultry exposure (county_poultry_census):")
        load(conn)
    print("done")


if __name__ == "__main__":
    main()
