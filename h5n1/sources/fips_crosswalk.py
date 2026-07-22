"""Resolve (state, county name) -> 5-digit county FIPS against dim_county.

APHIS/NOAA/USGS sources identify places by full state name + county name; the
warehouse keys everything on FIPS. This is the shared resolver every name-based
source uses. It normalizes both sides against the 2021-vintage county master
(which is why traditional Connecticut counties resolve — see census.py).

Build a resolver once, reuse it per row:

    from h5n1.sources.census import counties_dataframe
    from h5n1.sources.fips_crosswalk import FipsResolver
    r = FipsResolver(counties_dataframe())
    r.resolve("Utah", "Cache")           # -> "49005"
    r.resolve("Missouri", "Unknown")     # -> None
"""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "dc": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

# County-type words. The master (dim_county) always carries one; APHIS sometimes
# drops it and sometimes keeps a bare "City" (independent cities, and true names
# like Nevada's "Carson City"). We index the master WITHOUT its type word, then
# probe each source name two ways — with and without a trailing "city" — and union
# the hits, so "Virginia Beach City", "Virginia Beach", and "Carson City" all land.
_FULL = re.compile(
    r"\s+(city and borough|census area|municipality|municipio|county|parish|borough|city)$"
)
_NO_CITY = re.compile(
    r"\s+(city and borough|census area|municipality|municipio|county|parish|borough)$"
)

# Hand-maintained fixes for genuine source quirks the normalizer can't reach:
# typos and a post-2010 split with no 1:1 name. Keyed on (USPS, no-space no-type).
_OVERRIDES = {
    ("PA", "huntington"): "42061",          # APHIS typo for Huntingdon
    ("LA", "jeffersondavispari"): "22053",  # truncated "Parish"
    ("AK", "valdezcordova"): "02063",        # 2019 split; map to Chugach
}


def _base(name: str) -> str:
    n = str(name).strip().lower().replace(".", "").replace("'", "").replace("-", " ")
    n = n.replace("saint ", "st ")
    return re.sub(r"\s+", " ", n).strip()


def _key_full(name: str) -> str:
    return _FULL.sub("", _base(name)).replace(" ", "")


def _key_no_city(name: str) -> str:
    return _NO_CITY.sub("", _base(name)).replace(" ", "")


class FipsResolver:
    """State+county -> FIPS, built from a dim_county-shaped frame (state, county_name, fips)."""

    def __init__(self, counties: pd.DataFrame) -> None:
        self._by_name: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in counties.itertuples():
            self._by_name[(row.state, _key_full(row.county_name))].add(row.fips)

    def resolve(self, state: str, county: str) -> str | None:
        abbr = STATE_ABBR.get(str(state).strip().lower())
        if abbr is None:
            return None  # territory or unrecognized state
        keys = {_key_full(county), _key_no_city(county)}
        for k in keys:
            if (abbr, k) in _OVERRIDES:
                return _OVERRIDES[(abbr, k)]
        hits: set[str] = set()
        for k in keys:
            hits |= self._by_name.get((abbr, k), set())
        if not hits:
            return None
        # When a county and a same-named independent city both match (VA Fairfax,
        # MO St. Louis, ...), prefer the county — the larger area, the right unit
        # for area-based detections. County FIPS sorts below the city's 5xx code.
        return min(hits)
