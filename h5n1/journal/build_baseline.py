"""Rebuild the baseline modeling surfaces in the LOCAL journal database, twice:

  --wild coll      wild-bird features dated by Collection Date (the legacy definition).
                   Outputs are renamed to model_county_day_35_coll and model_state_week_coll.
                   Sensitivity only; also a check that the local chain reproduces the shared
                   database's tables.
  --wild detected  wild-bird features dated by Date Detected (PRIMARY). Outputs keep their
                   names: feature_* matviews, model_county_day, model_county_day_35,
                   model_state_week.

    uv run python -m h5n1.journal.build_baseline --wild coll
    uv run python -m h5n1.journal.build_baseline --wild detected

Run `coll` first: both variants build the feature_* matviews under the same names, so the
matviews left in place are those of whichever variant ran last, and that must be `detected`.

The SQL is the repository's own, unchanged, executed in the documented order
(008 spatial lag -> 009 weather lag -> 011 feature_county_day -> 012 -> rf35 -> state_week).
For `coll`, the only change is the wild-bird table name, substituted at execution time.
No model is fitted here; targets are built but nothing is scored against them.
"""
from __future__ import annotations

import argparse
import pathlib
import time

from sqlalchemy import text

from h5n1.journal.localdb import assert_local, local_engine

SQL = pathlib.Path(__file__).resolve().parents[2] / "sql"
CHAIN = [
    ("feature_spatial_lag", SQL / "008_spatial_lag.sql"),
    ("feature_weather_lag", SQL / "009_weather_lag.sql"),
    ("feature_county_day", SQL / "011_model_county_day.sql"),
    ("model_county_day", SQL / "012_model_county_day.sql"),
    ("model_county_day_35", SQL / "builds" / "model_county_day_35.sql"),
    ("model_state_week", SQL / "builds" / "model_state_week.sql"),
]
KEEP_COLL = ["model_county_day_35", "model_state_week"]
WILD = "fact_wild_bird_detection"


def _exists(cn, name: str) -> bool:
    return cn.execute(text("select to_regclass(:n)"), {"n": f"public.{name}"}).scalar() is not None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wild", choices=["coll", "detected"], required=True)
    ap.add_argument("--start", default="feature_spatial_lag", choices=[n for n, _ in CHAIN],
                    help="resume a failed run of the SAME variant from this step")
    args = ap.parse_args()
    eng = local_engine()
    assert_local(eng)

    with eng.connect() as cn:
        if args.wild == "coll" and all(_exists(cn, f"{t}_coll") for t in KEEP_COLL):
            print("collection-date outputs already exist; nothing to do")
            return

    names = [n for n, _ in CHAIN]
    for name, path in CHAIN[names.index(args.start):]:
        sql = path.read_text(encoding="utf-8")
        if args.wild == "coll":
            assert f"{WILD}_coll" not in sql
            sql = sql.replace(WILD, f"{WILD}_coll")
        t0 = time.time()
        print(f"[{args.wild}] build {name} from {path.name} ...", flush=True)
        # Raw psycopg with no parameters: SQLAlchemy would pass an empty parameter set, and
        # psycopg then reads the literal percent signs in 011 as placeholders.
        raw = eng.raw_connection()
        try:
            with raw.cursor() as cur:
                cur.execute(sql)
            raw.commit()
        finally:
            raw.close()
        with eng.connect() as cn:
            n = cn.execute(text(f"select count(*) from {name}")).scalar()
        print(f"[{args.wild}]   {name}: {n:,} rows in {time.time() - t0:,.0f}s", flush=True)

    if args.wild == "coll":
        with eng.begin() as cn:
            for t in KEEP_COLL:
                cn.execute(text(f"DROP TABLE IF EXISTS {t}_coll"))
                idx = [r[0] for r in cn.execute(text(
                    "select indexname from pg_indexes where schemaname='public' and tablename=:t"),
                    {"t": t})]
                cn.execute(text(f"ALTER TABLE {t} RENAME TO {t}_coll"))
                for i in idx:
                    cn.execute(text(f'ALTER INDEX "{i}" RENAME TO "{i}_coll"'))
                cn.execute(text(f"COMMENT ON TABLE {t}_coll IS 'Journal rebuild: wild-bird "
                                f"features dated by Collection Date (legacy). Sensitivity only.'"))
        print("[coll] renamed outputs to *_coll", flush=True)
    else:
        with eng.begin() as cn:
            for t in KEEP_COLL:
                cn.execute(text(f"COMMENT ON TABLE {t} IS 'Journal rebuild: wild-bird features "
                                f"dated by Date Detected (primary).'"))
    print("done", flush=True)


if __name__ == "__main__":
    main()
