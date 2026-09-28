"""Unit tests for the GDELT parsers and geography guards. Pure: no DB, no network.

The point-in-polygon test uses synthetic squares, not TIGER, so it runs offline; the
real-boundary check (e.g. Des Moines -> 19153) is part of the load's verification.
"""
from __future__ import annotations

import datetime as dt

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from h5n1.sources import gdelt

STATES = {"IA", "KS", "CT", "MN"}

# Real-shaped V2Locations: a state (centroid), a US city, a country, a foreign city.
LOCS = (
    "2#Kansas, United States#US#USKS##38.5111#-96.8005#KS#120;"
    "3#Des Moines, Iowa, United States#US#USIA#IA153#41.6006#-93.6091#465961#300;"
    "1#United States#US#US##39.828175#-98.5795#US#410;"
    "4#Toronto, Ontario, Canada#CA#CA08##43.6667#-79.4167#-574890#520"
)


def test_crawl_day():
    assert gdelt.crawl_day(20240312151500) == dt.date(2024, 3, 12)


def test_parse_tone():
    t = gdelt.parse_tone("-3.2,1.1,4.3,5.4,20.5,0.8,412")
    assert t["tone"] == -3.2 and t["tone_neg"] == 4.3 and t["word_count"] == 412
    assert all(v is None for v in gdelt.parse_tone("").values())
    assert all(v is None for v in gdelt.parse_tone("1,2,x,4,5,6,7").values())


def test_parse_locations():
    locs = gdelt.parse_locations(LOCS)
    assert [x["loc_type"] for x in locs] == [2, 3, 1, 4]
    assert locs[1]["adm1"] == "USIA" and locs[1]["lat"] == pytest.approx(41.6006)
    assert gdelt.parse_locations(None) == []
    assert gdelt.parse_locations("garbage;3#x") == []


def test_usps_from_adm1():
    assert gdelt.usps_from_adm1("USIA", STATES) == "IA"
    assert gdelt.usps_from_adm1("USZZ", STATES) is None
    assert gdelt.usps_from_adm1("CA08", STATES) is None
    assert gdelt.usps_from_adm1(None, STATES) is None


def test_adm2_to_fips():
    fp = {"TN": "47", "CT": "09"}
    assert gdelt.adm2_to_fips("TN157", fp) == "47157"
    assert gdelt.adm2_to_fips("CT003", fp) == "09003"
    assert gdelt.adm2_to_fips("ZZ001", fp) is None
    assert gdelt.adm2_to_fips("TN15", fp) is None
    assert gdelt.adm2_to_fips(None, fp) is None
    assert gdelt.adm2_to_fips(float("nan"), fp) is None
    assert gdelt.usps_from_adm1(float("nan"), STATES) is None


def test_syndication_key():
    d = dt.date(2024, 3, 12)
    tone = "-3.2,1.1,4.3,5.4,20.5,0.8,412"
    # Same day + same tone = same wire copy, across outlets.
    assert gdelt.syndication_key(d, tone, "a") == gdelt.syndication_key(d, tone, "b")
    # Different day: a different story event.
    assert gdelt.syndication_key(d + dt.timedelta(1), tone, "a") != gdelt.syndication_key(d, tone, "a")
    # No tone: never collapse unrelated records together.
    assert gdelt.syndication_key(d, None, "a") != gdelt.syndication_key(d, None, "b")


def _counties():
    # Two unit squares; a point in the first, a point in the second, one in neither.
    return gpd.GeoDataFrame(
        {"fips": ["19153", "20177"]},
        geometry=[box(-94, 41, -93, 42), box(-97, 38, -96, 39)],
        crs="EPSG:4269",
    )


def test_assign_counties_only_type3():
    loc = pd.DataFrame([x for x in gdelt.parse_locations(LOCS) if x["country"] == "US"])
    fips = gdelt.assign_counties(loc, _counties())
    by_type = dict(zip(loc["loc_type"], fips))
    assert by_type[3] == "19153"
    # The Kansas STATE location sits at (38.51, -96.80), squarely inside the second
    # square. It must still get no county: it's a state centroid, not a place.
    assert by_type[2] is None
    assert by_type[1] is None


def test_assign_counties_miss_is_none():
    loc = pd.DataFrame([{"loc_type": 3, "lat": 10.0, "lon": 10.0}])
    assert gdelt.assign_counties(loc, _counties()).iloc[0] is None


def test_guard_state_centroid():
    loc = pd.DataFrame([{"loc_type": 2, "fips": "20177"}])
    with pytest.raises(RuntimeError, match="state-centroid"):
        gdelt.check_location_guards(loc, {"20177"})


def test_guard_ct_planning_region():
    loc = pd.DataFrame([{"loc_type": 3, "fips": "09110"}])
    with pytest.raises(RuntimeError, match="091xx"):
        gdelt.check_location_guards(loc, {"09110"})


def test_guard_orphan_fips():
    loc = pd.DataFrame([{"loc_type": 3, "fips": "72001"}])
    with pytest.raises(RuntimeError, match="not in dim_county"):
        gdelt.check_location_guards(loc, {"19153"})


def test_guard_passes_clean():
    loc = pd.DataFrame([{"loc_type": 3, "fips": "19153"}, {"loc_type": 2, "fips": None}])
    gdelt.check_location_guards(loc, {"19153"})


def test_theme_regex_needs_exact_theme():
    import re
    rx = re.compile(gdelt.THEME_RE)
    assert rx.search("TAX_DISEASE_BIRD_FLU,123;")
    assert not rx.search("TAX_DISEASE_BIRD_FLU_SOMETHING,123;")


def test_parse_gcam():
    g = gdelt.parse_gcam("wc:412,c1.1:3,c3.1:7,c5.2:2,c5.33:1,v10.1:0.23")
    assert g["gcam_wc"] == 412
    assert g["gcam_negative"] == 7 and g["gcam_death"] == 2 and g["gcam_anxiety"] == 1
    # Absent dimension on a scored article is a real zero, not unknown.
    assert g["gcam_uncertainty"] == 0 and g["gcam_tentative"] == 0
    # c5.2 must not match c5.26 or c5.21 (exact keys, not prefixes).
    assert gdelt.parse_gcam("wc:10,c5.26:4")["gcam_death"] == 0
    assert gdelt.parse_gcam("wc:10,c5.26:4")["gcam_tentative"] == 4


def test_parse_gcam_unmeasured_is_null():
    for s in (None, "", "c5.33:1", "wc:0,c5.33:1", "wc:x"):
        assert all(v is None for v in gdelt.parse_gcam(s).values()), s
