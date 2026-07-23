"""Migration geometry for directional spatial-lag features.

Two derived structures, both computed from dim_county centroids plus a static
flyway assignment — no external migration product:

- county_migration_axis: each county's flyway and the great-circle bearings from
  its centroid toward that flyway's breeding grounds (north) and wintering grounds
  (south). Because the four flyways anchor to different parts of the Arctic/Canada,
  the "toward breeding" bearing curves by region (Atlantic ~N, Mississippi ~NNW,
  Pacific ~NW) — which is what makes the migration axis location-dependent rather
  than a single global NW/SE.
- county_proximity: directed county pairs within a max radius, with great-circle
  distance and bearing. Storing raw geometry (not sector membership) lets the
  sector half-width and radius stay query-time parameters, so they can be tuned
  without regenerating the table.

CONUS only (48 states + DC): the flyway framework and anchors are continental;
Alaska/Hawaii are skipped, consistent with the weather load.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# USFWS administrative flyways, assigned by whole state. The split mountain-west
# states (MT, WY, CO, NM) are placed in Central per the administrative convention;
# their western portions are biologically Pacific, a documented simplification.
STATE_FLYWAY = {
    **{s: "Atlantic" for s in
       ["CT", "DE", "FL", "GA", "ME", "MD", "MA", "NH", "NJ", "NY", "NC", "PA",
        "RI", "SC", "VT", "VA", "WV", "DC"]},
    **{s: "Mississippi" for s in
       ["AL", "AR", "IL", "IN", "IA", "KY", "LA", "MI", "MN", "MS", "MO", "OH",
        "TN", "WI"]},
    **{s: "Central" for s in
       ["CO", "KS", "MT", "NE", "NM", "ND", "OK", "SD", "TX", "WY"]},
    **{s: "Pacific" for s in
       ["AZ", "CA", "ID", "NV", "OR", "UT", "WA"]},
}

# Hand-placed anchor points (lat, lon) per flyway: breeding = the northern origin
# of that flyway's migrants, wintering = the southern destination. Their differing
# longitudes are what tilt each flyway's axis correctly.
FLYWAY_ANCHORS = {
    "Atlantic":    {"breeding": (58.0, -68.0),  "wintering": (28.0, -81.0)},
    "Mississippi": {"breeding": (60.0, -100.0), "wintering": (29.0, -91.0)},
    "Central":     {"breeding": (58.0, -108.0), "wintering": (26.0, -98.0)},
    "Pacific":     {"breeding": (64.0, -150.0), "wintering": (32.0, -115.0)},
}


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * r * np.arcsin(np.sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise from N."""
    p = np.pi / 180
    d_lon = (lon2 - lon1) * p
    y = np.sin(d_lon) * np.cos(lat2 * p)
    x = (np.cos(lat1 * p) * np.sin(lat2 * p)
         - np.sin(lat1 * p) * np.cos(lat2 * p) * np.cos(d_lon))
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def _conus(counties: pd.DataFrame) -> pd.DataFrame:
    return counties[~counties["fips"].str[:2].isin(["02", "15"])].dropna(subset=["lat", "lon"])


def migration_axis(counties: pd.DataFrame) -> pd.DataFrame:
    """Per-county flyway + bearings toward the breeding and wintering anchors.

    `counties` needs columns fips, state (USPS), lat, lon.
    """
    df = _conus(counties).copy()
    df["flyway"] = df["state"].map(STATE_FLYWAY)
    df = df[df["flyway"].notna()]

    def bearing_to(kind):
        anc = df["flyway"].map(lambda f: FLYWAY_ANCHORS[f][kind])
        alat = anc.map(lambda t: t[0]).to_numpy(float)
        alon = anc.map(lambda t: t[1]).to_numpy(float)
        return _bearing_deg(df["lat"].to_numpy(float), df["lon"].to_numpy(float), alat, alon)

    df["breeding_bearing_deg"] = bearing_to("breeding")
    df["wintering_bearing_deg"] = bearing_to("wintering")
    return df[["fips", "flyway", "breeding_bearing_deg", "wintering_bearing_deg"]].reset_index(drop=True)


def county_proximity(counties: pd.DataFrame, r_max_km: float) -> pd.DataFrame:
    """Directed county pairs within r_max_km, with great-circle distance and bearing."""
    df = _conus(counties).reset_index(drop=True)
    lat = df["lat"].to_numpy(float)
    lon = df["lon"].to_numpy(float)
    fips = df["fips"].to_numpy()

    out = []
    for i in range(len(df)):
        d = _haversine_km(lat[i], lon[i], lat, lon)
        sel = np.where((d <= r_max_km) & (d > 0))[0]  # exclude self
        if sel.size == 0:
            continue
        b = _bearing_deg(lat[i], lon[i], lat[sel], lon[sel])
        out.append(pd.DataFrame({
            "fips": fips[i],
            "neighbor_fips": fips[sel],
            "distance_km": d[sel],
            "bearing_deg": b,
        }))
    return pd.concat(out, ignore_index=True)


def angular_diff(a, b):
    """Smallest absolute difference between two compass bearings, in [0, 180]."""
    d = np.abs((a - b + 180) % 360 - 180)
    return d
