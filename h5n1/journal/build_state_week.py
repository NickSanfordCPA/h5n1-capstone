"""Rebuild only the state-week tables in the LOCAL journal database, under both wild-bird
datings, from sql/builds/model_state_week.sql.

    uv run python -m h5n1.journal.build_state_week

    model_state_week        wild-bird features dated by Date Detected   (primary)
    model_state_week_coll   dated by Collection Date                    (sensitivity)

The state-week SQL reads the wild-bird table directly and takes nothing wild-bird-related
from the feature matviews, so the collection-dated variant needs only two names swapped in
the SQL text: the wild-bird table and the output table (which also renames its indexes).
Unlike build_baseline.py --wild coll, this never renames a county table.

Builds target columns; fits nothing.
"""
from __future__ import annotations

import pathlib

import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import assert_local, local_engine

SQL = pathlib.Path(__file__).resolve().parents[2] / "sql" / "builds" / "model_state_week.sql"


def main() -> None:
    eng = local_engine()
    assert_local(eng)
    base = SQL.read_text(encoding="utf-8")
    variants = {
        "model_state_week": base,
        "model_state_week_coll": base.replace("fact_wild_bird_detection", "fact_wild_bird_detection_coll")
                                     .replace("model_state_week", "model_state_week_coll"),
    }
    for name, sql in variants.items():
        raw = eng.raw_connection()
        try:
            with raw.cursor() as cur:
                cur.execute(sql)
            raw.commit()
        finally:
            raw.close()
        print(f"built {name}", flush=True)
    with eng.connect() as cn:
        a = pd.read_sql(text("select * from model_state_week order by state, day"), cn)
        b = pd.read_sql(text("select * from model_state_week_coll order by state, day"), cn)
    assert list(a.columns) == list(b.columns) and len(a) == len(b)
    differ = [c for c in a.columns if not a[c].equals(b[c])]
    print(f"{len(a):,} rows x {a.shape[1]} columns; columns that differ between datings: {differ}")
    assert all("wild" in c for c in differ), "a non-wild-bird column differs between the datings"
    print("new-onset share: 28d", round(a.state_new_onset.mean(), 4),
          " 30d", round(a.state_new_onset_30d.mean(), 4),
          "; rows where they differ:", int((a.state_new_onset != a.state_new_onset_30d).sum()))


if __name__ == "__main__":
    main()
