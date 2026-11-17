"""Copy source tables from the shared Cloud SQL database (read-only, proxy on 5433)
into the local journal database (5432 / h5n1_journal, schema public), then reconcile
exact row counts.

    uv run python -m h5n1.journal.copy_source            # copy what is missing, then verify
    uv run python -m h5n1.journal.copy_source --verify   # counts only

Resumable: a table that already exists locally with the same row count is skipped; one
that exists with a different count is dropped (locally) and copied again.

Only base/source tables are copied. Matviews and model tables are rebuilt locally from
SQL; Max's meta_post* tables are deliberately not copied (cross-check only, section 6.10).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import (DATA_DIR, LOCAL_DB, LOCAL_HOST, LOCAL_PORT, LOCAL_USER, PG_BIN,
                                  local_engine, source_engine, source_settings)

# Order matters only for readability: pg_dump of one table carries its own FKs, so the
# dimensions go first.
TABLES = [
    "dim_county", "dim_date", "county_adjacency", "county_proximity", "county_migration_axis",
    "county_poultry_census",
    "fact_h5n1_outbreak", "fact_wild_bird_detection", "fact_weather", "fact_bird_density",
    "fact_egg_economics", "feature_migration_pressure",
    "fact_social_sentiment", "fact_x_post",   # empty in the shared DB, but 011 joins them
    "fact_gdelt_article", "fact_gdelt_location", "fact_gdelt_denominator", "fact_gdelt_gcam",
    "x_raw", "x_csv_raw",
    "schema_migrations", "model_build_log",
]


def count(engine, table: str) -> int | None:
    with engine.connect() as c:
        if c.execute(text("select to_regclass(:t)"), {"t": f"public.{table}"}).scalar() is None:
            return None
        return c.execute(text(f'select count(*) from public."{table}"')).scalar()


def copy_table(table: str) -> None:
    s = source_settings()
    dump = [str(PG_BIN / "pg_dump"), "-h", s["host"], "-p", str(s["port"]), "-U", s["user"],
            "-d", s["db"], "-t", f"public.{table}", "--no-owner", "--no-privileges", "-w"]
    load = [str(PG_BIN / "psql"), "-h", LOCAL_HOST, "-p", str(LOCAL_PORT), "-U", LOCAL_USER,
            "-d", LOCAL_DB, "-w", "-q", "-v", "ON_ERROR_STOP=1", "-o", os.devnull]
    assert LOCAL_PORT == 5432 and s["port"] == 5433
    src_env = {**os.environ, "PGPASSWORD": s["password"],
               "PGOPTIONS": "-c default_transaction_read_only=on"}
    dst_env = {k: v for k, v in os.environ.items() if k not in ("PGPASSWORD", "PGOPTIONS")}
    p1 = subprocess.Popen(dump, stdout=subprocess.PIPE, env=src_env)
    p2 = subprocess.Popen(load, stdin=p1.stdout, env=dst_env)
    p1.stdout.close()
    rc2, rc1 = p2.wait(), p1.wait()
    if rc1 or rc2:
        raise RuntimeError(f"{table}: pg_dump rc={rc1}, psql rc={rc2}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    src, dst = source_engine(), local_engine()
    rows = []
    for t in TABLES:
        # A table the rebuild has since replaced keeps its untouched copy as <t>_shared
        # (wild_birds.py does this). Compare against that copy and never overwrite the
        # rebuilt table: re-copying it once silently undid the wild-bird re-dating.
        if count(dst, f"{t}_shared") is not None:
            n_src, n_dst = count(src, t), count(dst, f"{t}_shared")
            ok = n_src == n_dst
            print(f"{t}: source {n_src:,}  local copy {t}_shared {n_dst:,}  "
                  f"{'OK' if ok else 'MISMATCH'} (rebuilt table left alone)", flush=True)
            rows.append({"table": t, "source_rows": n_src, "local_rows": n_dst, "match": ok})
            continue
        n_src, n_dst = count(src, t), count(dst, t)
        if t == "model_build_log" and n_dst is not None:
            # local builds append to it, so it only has to hold at least the copied rows
            print(f"{t}: source {n_src:,}  local {n_dst:,}  (grows with local builds)", flush=True)
            rows.append({"table": t, "source_rows": n_src, "local_rows": n_dst,
                         "match": n_dst >= n_src})
            continue
        if n_src is None:
            print(f"{t}: not in source, skipped", flush=True)
            continue
        if not args.verify and n_dst != n_src:
            if n_dst is not None:
                with dst.begin() as c:
                    c.execute(text(f'drop table public."{t}" cascade'))
            print(f"{t}: copying {n_src:,} rows ...", flush=True)
            copy_table(t)
            n_dst = count(dst, t)
        ok = n_src == n_dst
        print(f"{t}: source {n_src:,}  local {n_dst if n_dst is None else format(n_dst, ',')}  "
              f"{'OK' if ok else 'MISMATCH'}", flush=True)
        rows.append({"table": t, "source_rows": n_src, "local_rows": n_dst, "match": ok})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(DATA_DIR / "source_row_counts.csv", index=False)
    return 0 if df.match.all() else 1


if __name__ == "__main__":
    sys.exit(main())
