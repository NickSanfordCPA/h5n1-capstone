"""dim_county source: the U.S. Census county Gazetteer.

Supplies the FIPS geo backbone every fact table joins to — 5-digit county FIPS,
name, state, interior-point centroid, and land area. Population is NOT in the
Gazetteer; it's left NULL here and backfilled from ACS separately.

This table is also the authority the FIPS crosswalk resolves APHIS / NOAA / USGS
place names against, so seeding it accurately matters beyond this one table.
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import requests

# 2021 vintage, chosen deliberately to match our data sources. APHIS (the outbreak
# DV) reports Connecticut by TRADITIONAL counties (New Haven, New London, ...), not
# the nine planning regions (FIPS 091xx) that replaced them in the 2022+ Gazetteers.
# 2021 gives traditional CT counties AND the current codes for older changes that
# APHIS also uses (AK Kusilvak 02158, SD Oglala Lakota 46102, VA Bedford merge
# 51019 — all pre-2016). So one vintage conforms the whole county master to the data.
GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2021_Gazetteer/2021_Gaz_counties_national.zip"
)


def counties_dataframe() -> pd.DataFrame:
    """Download + parse the Gazetteer into dim_county-shaped rows (50 states + DC)."""
    resp = requests.get(GAZETTEER_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        txt_name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
        raw = zf.read(txt_name)

    # Gazetteer files are tab-delimited, latin-1, and carry stray whitespace in
    # header names (notably a trailing space on INTPTLONG). Read FIPS as str so
    # leading zeros survive (e.g. Alabama = "01001").
    df = pd.read_csv(io.BytesIO(raw), sep="\t", dtype=str, encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame(
        {
            "fips": df["GEOID"].str.zfill(5),
            "state": df["USPS"].str.strip(),
            "county_name": df["NAME"].str.strip(),
            "centroid_lat": pd.to_numeric(df["INTPTLAT"], errors="coerce"),
            "centroid_lon": pd.to_numeric(df["INTPTLONG"], errors="coerce"),
            "population": None,  # not in the Gazetteer; ACS backfill later
            "land_area_sqmi": pd.to_numeric(df["ALAND_SQMI"], errors="coerce"),
        }
    )
    # 50 states + DC only; drop territories (state FIPS >= 60: AS/GU/MP/PR/VI).
    keep = out["fips"].str[:2].astype(int) < 60
    return out[keep].reset_index(drop=True)
