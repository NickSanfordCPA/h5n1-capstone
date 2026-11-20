"""USGS WHISPers wildlife morbidity/mortality events -> local tables.

SOURCE. Wildlife Health Information Sharing Partnership event reporting system, USGS
National Wildlife Health Center. Public REST API at https://whispers.usgs.gov/api/
(endpoint `eventsummaries`), the same records as the web app's export. Data release
doi:10.5066/P9B0A3IM. USGS data are US Government works in the public domain.
WHISPers relies on voluntary reporting by partner agencies; it is not a census.

    uv run python -m h5n1.journal.whispers download   # raw JSON pages -> data/journal/raw/whispers/
    uv run python -m h5n1.journal.whispers load       # -> public.whispers_event*, local DB

THE DATE PROBLEM. The public API exposes an event's start_date and end_date but NOT the date
its record was created or published. An event dated by start_date is look-ahead, like the
wild-bird Collection Date. Two facts make a bound possible:
  * event ids are assigned in creation order;
  * a record cannot be created before the event it describes has started.
So record `id` was created no earlier than the latest start_date among all records with an
id at or below it:

    known_date_lb = running maximum of start_date over events ordered by id

This is a LOWER BOUND on the date the record existed, so some look-ahead can remain; the
protocol says how it is used. Start dates in the future relative to the download, or
obviously mistyped (later than the download date), are excluded from the running maximum.

A diagnosis (e.g. HPAI) is usually added after the record is created, at an unknown date.
`whispers_event.hpai` therefore must not be used as a dated predictor.

Tables (local database, schema public):
    whispers_event          one row per event
    whispers_event_county   one row per (event, county FIPS); 2021-vintage FIPS only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time

import pandas as pd
import requests
from sqlalchemy import text

from h5n1.journal.localdb import DATA_DIR, assert_local, local_engine

RAW = DATA_DIR / "raw" / "whispers"
API = "https://whispers.usgs.gov/api/eventsummaries/"
PAGE = 200
HPAI_RE = r"avian influenza|hpai|h5n1|h5 "


def download() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "research-client"
    page, total = 1, None
    while True:
        dest = RAW / f"eventsummaries_{page:04d}.json"
        if dest.exists():
            body = json.loads(dest.read_text(encoding="utf-8"))
        else:
            for attempt in range(5):
                r = s.get(API, params={"format": "json", "page_size": PAGE, "page": page},
                          timeout=180)
                if r.status_code == 200:
                    break
                time.sleep(10 * (attempt + 1))
            r.raise_for_status()
            body = r.json()
            for ev in body["results"]:                     # county polygons are not needed
                for a in ev.get("administrativeleveltwos", []):
                    a.pop("points", None)
            dest.write_text(json.dumps(body), encoding="utf-8")
            time.sleep(1)
        total = body["count"]
        print(f"page {page}: {len(body['results'])} events (of {total})", flush=True)
        if not body.get("next"):
            break
        page += 1
    (RAW / "_retrieved.txt").write_text(
        f"retrieved {dt.datetime.now(dt.timezone.utc).isoformat()} from {API}; count {total}\n")


def load() -> None:
    eng = local_engine()
    assert_local(eng)
    retrieved = pd.Timestamp((RAW / "_retrieved.txt").read_text().split()[1]).tz_localize(None)
    events, counties = [], []
    for f in sorted(RAW.glob("eventsummaries_*.json")):
        for ev in json.loads(f.read_text(encoding="utf-8"))["results"]:
            dx = [d.get("diagnosis_string") or "" for d in ev.get("eventdiagnoses", [])]
            sp = ev.get("species", [])
            events.append({
                "event_id": ev["id"], "event_type": ev.get("event_type_string"),
                "start_date": ev.get("start_date"), "end_date": ev.get("end_date"),
                "complete": ev.get("complete"), "affected_count": ev.get("affected_count"),
                "diagnoses": " | ".join(dx),
                "n_species": len(sp),
                "avian": any((s.get("class_name") or "") == "Aves" for s in sp),
                "mammal": any((s.get("class_name") or "") == "Mammalia" for s in sp),
                "species": " | ".join(s.get("name") or "" for s in sp)[:500],
                "states": "|".join(sorted({a.get("abbreviation") or ""
                                           for a in ev.get("administrativelevelones", [])})),
                "us": any((a.get("country_string") or "") == "United States"
                          for a in ev.get("administrativeleveltwos", [])),
            })
            for a in ev.get("administrativeleveltwos", []):
                if (a.get("country_string") or "") == "United States" and a.get("fips_code"):
                    counties.append({"event_id": ev["id"], "fips": str(a["fips_code"]).zfill(5),
                                     "county_name": a.get("name")})
    e = pd.DataFrame(events).drop_duplicates("event_id").sort_values("event_id")
    e["start_date"] = pd.to_datetime(e["start_date"], errors="coerce")
    e["end_date"] = pd.to_datetime(e["end_date"], errors="coerce")
    e["hpai"] = e["diagnoses"].str.lower().str.contains(HPAI_RE)
    ok = e["start_date"].notna() & (e["start_date"] <= retrieved)
    e["known_date_lb"] = e["start_date"].where(ok).cummax()
    e["lb_minus_start_days"] = (e["known_date_lb"] - e["start_date"]).dt.days
    c = pd.DataFrame(counties).drop_duplicates(["event_id", "fips"])
    with eng.connect() as cn:
        dim = set(pd.read_sql(text("select fips from dim_county"), cn)["fips"])
    orphan = c[~c["fips"].isin(dim)]
    if orphan["fips"].str.match(r"^091\d\d$").any():
        print(f"WARNING: {orphan['fips'].str.match(r'^091').sum()} rows use 091xx Connecticut "
              f"planning regions; dropped (not mappable to 2021 counties)")
    print(f"county rows {len(c):,}; not in dim_county (dropped) {len(orphan):,}: "
          f"{orphan['fips'].value_counts().head(8).to_dict()}")
    c = c[c["fips"].isin(dim)]
    for col in ("start_date", "end_date", "known_date_lb"):
        e[col] = e[col].dt.date
    with eng.begin() as cn:
        cn.execute(text("DROP TABLE IF EXISTS whispers_event_county"))
        cn.execute(text("DROP TABLE IF EXISTS whispers_event"))
    e.to_sql("whispers_event", eng, index=False, chunksize=2000, method="multi")
    c.to_sql("whispers_event_county", eng, index=False, chunksize=5000, method="multi")
    with eng.begin() as cn:
        cn.execute(text("ALTER TABLE whispers_event ADD PRIMARY KEY (event_id)"))
        cn.execute(text("ALTER TABLE whispers_event_county ADD PRIMARY KEY (event_id, fips)"))
        cn.execute(text(
            "COMMENT ON TABLE whispers_event IS 'USGS WHISPers events (public API, "
            "eventsummaries). known_date_lb = running max of start_date by event_id: a LOWER "
            "BOUND on when the record existed. hpai has no date; never use it as a dated "
            "predictor. Voluntary reporting, not a census.'"))
    w = e[(e["start_date"] >= dt.date(2022, 1, 1)) & e["us"]]
    print(f"events {len(e):,}; US 2022+ {len(w):,}; avian {int(w['avian'].sum()):,}; "
          f"HPAI-diagnosed {int(w['hpai'].sum()):,}; avian & HPAI {int((w['avian'] & w['hpai']).sum()):,}")
    print("known_date_lb - start_date, days, US 2022+ events:")
    print(w["lb_minus_start_days"].describe(percentiles=[.1, .25, .5, .75, .9, .95]).round(1).to_string())
    print(w.groupby(pd.to_datetime(w["start_date"]).dt.year).agg(
        events=("event_id", "size"), avian=("avian", "sum"), hpai=("hpai", "sum")).to_string())
    print(w["diagnoses"].value_counts().head(12).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "load"])
    {"download": download, "load": load}[ap.parse_args().step]()
