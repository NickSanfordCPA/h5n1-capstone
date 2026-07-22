"""county_adjacency source: the Census County Adjacency File (2010 vintage).

Pairs each county with the counties it shares a border with (queen contiguity).
The published file is 2010-vintage, which MATCHES this project's county master:
we key Connecticut by traditional counties (as APHIS does) and seed dim_county
from the 2021 Gazetteer for the same reason, so CT needs no reconciliation here.
Three counties renamed/merged AFTER 2010 but present in 2021 are remapped to
current FIPS so both endpoints resolve against dim_county.

File format: tab-delimited, latin-1, four columns
    source_name, source_geoid, neighbor_name, neighbor_geoid
It is sparse: source_name/geoid appear only on a county's first row and are blank
on continuation rows (forward-filled below). Each county lists itself first.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

ADJACENCY_URL = "https://www2.census.gov/geo/docs/reference/county_adjacency.txt"

# Renamed/merged after the 2010 file but present in the 2021 county master.
# Connecticut is deliberately absent: both the file and dim_county use traditional
# CT counties, which is the whole reason dim_county is seeded from the 2021 vintage.
FIPS_REMAP = {
    "02270": "02158",  # AK: Wade Hampton -> Kusilvak (2015)
    "46113": "46102",  # SD: Shannon -> Oglala Lakota (2015)
    "51515": "51019",  # VA: Bedford independent city -> merged into Bedford County (2013)
}

# Valdez-Cordova Census Area (02261) was SPLIT in 2019 into Chugach (02063) and
# Copper River (02066). A 1->2 split has no single-valued remap, and this is remote
# AK with no commercial poultry, so its adjacency is not reconstructed: pairs
# touching 02261 are dropped. Net effect: 02063/02066 carry no neighbor rows, a
# documented gap in a region the outbreak model does not reach.
DROPPED_SPLITS = {"02261"}


def adjacency_dataframe() -> pd.DataFrame:
    """Download + parse into de-duplicated (fips, neighbor_fips) pairs, self excluded."""
    resp = requests.get(ADJACENCY_URL, timeout=60)
    resp.raise_for_status()

    df = pd.read_csv(
        io.StringIO(resp.content.decode("latin-1")),
        sep="\t",
        header=None,
        dtype=str,
        names=["src_name", "fips", "nbr_name", "neighbor_fips"],
    )
    # Source county appears once per block; forward-fill it down the continuation rows.
    df["fips"] = df["fips"].ffill()
    df = df[["fips", "neighbor_fips"]].dropna()

    # Normalize + apply post-2010 renames BEFORE dropping self-pairs, so a merge
    # (e.g. VA 51515 -> 51019) collapses into a self-pair and is removed.
    df["fips"] = df["fips"].str.zfill(5).replace(FIPS_REMAP)
    df["neighbor_fips"] = df["neighbor_fips"].str.zfill(5).replace(FIPS_REMAP)

    df = df[df["fips"] != df["neighbor_fips"]]  # exclude self-adjacency
    df = df[~df["fips"].isin(DROPPED_SPLITS) & ~df["neighbor_fips"].isin(DROPPED_SPLITS)]
    in_states = (df["fips"].str[:2].astype(int) < 60) & (
        df["neighbor_fips"].str[:2].astype(int) < 60
    )  # drop territories (state FIPS >= 60: AS/GU/MP/PR/VI)
    return df[in_states].drop_duplicates().reset_index(drop=True)
