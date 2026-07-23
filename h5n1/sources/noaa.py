"""NOAA GHCN-Daily ingestion -> fact_weather (county x day temperature & precip).

NOAA publishes daily weather at stations, not counties. We assign each county its
nearest stations (by the centroid already in dim_county) and, per day and element,
take the value from the closest station that actually reported that day — so a
county falls back to its 2nd/3rd-nearest when its closest station has a gap.

Source: GHCN-Daily (Global Historical Climatology Network). Station observations
in the annual by_year files; TMAX/TMIN in tenths of degrees C, PRCP in tenths of mm.
humidity_pct is left NULL — GHCN-Daily does not carry relative humidity at this
resolution (a reanalysis product would be needed).

    python -m h5n1.sources.noaa 2020 2026     # load a year range into fact_weather

CONUS only: the nearest-station method needs a dense station field; Alaska/Hawaii
counties are skipped and left without weather rows.
"""
from __future__ import annotations

import io
import sys

import numpy as np
import pandas as pd
import requests
from sqlalchemy import text

from h5n1.db import get_engine

STATIONS_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
INVENTORY_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt"
BYYEAR_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_year/{year}.csv.gz"

ELEMENTS = ("TMAX", "TMIN", "PRCP")
K_NEAREST = 5  # stations ranked per county; gaps fall through to the next-nearest

WEATHER_UPSERT = text(
    """
    INSERT INTO fact_weather (fips, day, temp_min_c, temp_max_c, precip_mm, humidity_pct)
    VALUES (:fips, :day, :temp_min_c, :temp_max_c, :precip_mm, NULL)
    ON CONFLICT (fips, day) DO UPDATE SET
        temp_min_c = EXCLUDED.temp_min_c,
        temp_max_c = EXCLUDED.temp_max_c,
        precip_mm  = EXCLUDED.precip_mm
    """
)


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p = np.pi / 180
    a = (
        np.sin((lat2 - lat1) * p / 2) ** 2
        + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    )
    return 2 * r * np.arcsin(np.sqrt(a))


def station_index() -> pd.DataFrame:
    """US stations reporting TMAX, TMIN and PRCP with coverage through the study period."""
    txt = requests.get(STATIONS_URL, timeout=120).text
    rows = [
        (ln[0:11].strip(), float(ln[12:20]), float(ln[21:30]))
        for ln in txt.splitlines()
        if ln[0:2] == "US"
    ]
    st = pd.DataFrame(rows, columns=["station_id", "lat", "lon"])

    inv = pd.read_csv(
        io.StringIO(requests.get(INVENTORY_URL, timeout=120).text),
        sep=r"\s+", header=None,
        names=["station_id", "lat", "lon", "elem", "y0", "y1"],
    )
    inv = inv[inv["elem"].isin(ELEMENTS) & (inv["y1"] >= 2020) & (inv["y0"] <= 2026)]
    complete = inv.groupby("station_id")["elem"].apply(lambda s: set(ELEMENTS) <= set(s))
    keep = set(complete[complete].index)
    return st[st["station_id"].isin(keep)].reset_index(drop=True)


def nearest_stations(counties: pd.DataFrame, stations: pd.DataFrame, k: int = K_NEAREST) -> pd.DataFrame:
    """For each CONUS county centroid, the k nearest stations with a rank (0 = closest)."""
    slat, slon, sid = stations["lat"].values, stations["lon"].values, stations["station_id"].values
    out = []
    for row in counties.itertuples():
        d = _haversine_km(row.lat, row.lon, slat, slon)
        order = np.argsort(d)[:k]
        for rank, idx in enumerate(order):
            out.append((row.fips, sid[idx], rank))
    return pd.DataFrame(out, columns=["fips", "station_id", "rank"])


def _read_year(year: int, keep_ids: set[str]) -> pd.DataFrame:
    """Tidy (station_id, day, elem, value) for wanted stations/elements, unit-converted."""
    raw = requests.get(BYYEAR_URL.format(year=year), timeout=600).content
    cols = ["station_id", "date", "elem", "value", "mflag", "qflag", "sflag", "obstime"]
    df = pd.read_csv(io.BytesIO(raw), compression="gzip", header=None, names=cols,
                     dtype={"station_id": str, "date": str, "elem": str, "value": float, "qflag": str})
    df = df[df["station_id"].isin(keep_ids) & df["elem"].isin(ELEMENTS)]
    df = df[df["qflag"].isna()]  # drop rows that failed a NOAA quality check
    df["day"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.date
    df["value"] = df["value"] / 10.0  # tenths of degC / tenths of mm -> degC / mm
    return df[["station_id", "day", "elem", "value"]]


def county_day_weather(obs: pd.DataFrame, near: pd.DataFrame) -> pd.DataFrame:
    """Collapse station obs to county x day.

    Temperature is taken as a PAIR from the nearest station reporting both TMIN and
    TMAX that day — a station's own min/max are one internally-consistent observation,
    so sourcing them together avoids min > max artifacts from mixing stations.
    Precipitation is independent: the nearest station reporting PRCP.
    """
    merged = obs.merge(near, on="station_id")  # fips, day, elem, value, rank

    # Temperature pair: pivot per station-day, require both, then nearest rank wins.
    temp = merged[merged["elem"].isin(("TMIN", "TMAX"))]
    temp = temp.pivot_table(index=["fips", "day", "rank"], columns="elem", values="value")
    temp = temp.dropna(subset=["TMIN", "TMAX"]).reset_index()
    temp = temp.sort_values("rank").drop_duplicates(["fips", "day"])
    temp = temp.rename(columns={"TMIN": "temp_min_c", "TMAX": "temp_max_c"})

    # Precipitation: nearest reporting station per county-day.
    prcp = merged[merged["elem"] == "PRCP"].sort_values("rank").drop_duplicates(["fips", "day"])
    prcp = prcp[["fips", "day", "value"]].rename(columns={"value": "precip_mm"})

    wide = temp.merge(prcp, on=["fips", "day"], how="outer")
    for col in ("temp_min_c", "temp_max_c", "precip_mm"):
        if col not in wide:
            wide[col] = np.nan
    return wide[["fips", "day", "temp_min_c", "temp_max_c", "precip_mm"]]


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def main(start_year: int, end_year: int) -> None:
    stations = station_index()
    with get_engine().connect() as conn:
        cty = pd.DataFrame(
            conn.execute(text(
                "SELECT fips, centroid_lat AS lat, centroid_lon AS lon FROM dim_county"
            )).all(),
            columns=["fips", "lat", "lon"],
        )
    conus = cty[~cty["fips"].str[:2].isin(["02", "15"])].dropna(subset=["lat", "lon"])
    near = nearest_stations(conus, stations)
    keep_ids = set(near["station_id"])
    print(f"{len(stations)} candidate stations; {len(keep_ids)} used across {len(conus)} CONUS counties")

    engine = get_engine()
    for year in range(start_year, end_year + 1):
        obs = _read_year(year, keep_ids)
        wide = county_day_weather(obs, near)
        with engine.begin() as conn:
            conn.execute(WEATHER_UPSERT, _records(wide))
        print(f"  {year}: fact_weather {len(wide)} county-days upserted")
    print("done")


if __name__ == "__main__":
    a, b = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (2020, 2026)
    main(a, b)
