"""X Track B rerun with the Meta guide v2 sighting definition. Exploratory.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_sightings_v2

Meta guide v2 (2026-11-11) never counts an announcement of a poultry-flock detection as a local
observation, because it is the outcome being predicted. The X sightings were labelled under the v1
guide, which had no such rule. Here every X post the v1 readers marked a local observation (1,156)
was re-read for that one question by Sonnet subagents (data/X_derived/labels/v2_poultry/, guide
GUIDE_poultry.md), with reviewed overrides in corrections.csv. A post marked yes moves to step 8.

Sightings are then rebuilt from the event posts (dispositions Sighting and Same state-week) that
survive, one per state-week within each stream (earliest, ties by smallest post ID), exactly as the
x_post_analysis build does, so a dropped sighting can be replaced by a later post in its week.
x_post_analysis itself is not modified.

Outputs in data/X_derived/x_utility/: explore2025_v2guide_sightings*.csv (the Track B tables) and
v2guide_funnel.csv (the funnel counts by stream under v2).
"""
from __future__ import annotations

import pandas as pd

from h5n1.db import get_engine
from h5n1.models import x_sightings
from h5n1.models.x_baselines import OUT, ROOT
from h5n1.models.x_utility import TEST_FIRST

LABELS = ROOT / "data" / "X_derived" / "labels" / "v2_poultry"
STEP8 = "Not a local observation"


def poultry_ids() -> set[str]:
    lab = pd.concat([pd.read_csv(LABELS / f"labels_{i}.csv", dtype=str) for i in range(3)]).set_index("_id")
    fix = pd.read_csv(LABELS / "corrections.csv", dtype=str).set_index("_id")
    lab.loc[fix.index, "poultry_announcement"] = fix.poultry_announcement
    return set(lab.index[lab.poultry_announcement == "yes"])


def table() -> pd.DataFrame:
    t = pd.read_sql("SELECT post_id, stream, disposition, disposition_step, event_state, created_at "
                    "FROM x_post_analysis", get_engine())
    t["post_id"] = t.post_id.astype(str)
    t["ts"] = pd.to_datetime(t.created_at, utc=True)
    return t


def v2_dispositions(t: pd.DataFrame, poultry: set[str]) -> pd.DataFrame:
    t = t.copy()
    moved = t.post_id.isin(poultry) & (t.disposition_step >= 9)
    t.loc[moved, "disposition"] = STEP8
    ev = t.disposition.isin(["Sighting", "Same state-week"])
    t.loc[ev, "disposition"] = "Same state-week"
    week = t.ts.dt.tz_convert(None).dt.to_period("W")
    keep = (t[ev].assign(week=week[ev]).sort_values(["ts", "post_id"], kind="stable")
            .drop_duplicates(["stream", "event_state", "week"]).index)
    t.loc[keep, "disposition"] = "Sighting"
    return t


def sightings_v2(t: pd.DataFrame):
    def read(streams):
        ev = t[(t.disposition == "Sighting") & t.stream.isin(streams)]
        ev = ev.rename(columns={"post_id": "_id", "event_state": "state"})[["_id", "state", "ts"]]
        week = ev.ts.dt.tz_convert(None).dt.to_period("W")
        return ev.assign(week=week).sort_values(["ts", "_id"], kind="stable").drop_duplicates(
            ["state", "week"]).drop(columns="week").reset_index(drop=True)
    return read


def main() -> None:
    t = table()
    v1 = t.pivot_table(index="disposition", columns="stream", values="post_id", aggfunc="count", fill_value=0)
    v2t = v2_dispositions(t, poultry_ids())
    v2 = v2t.pivot_table(index="disposition", columns="stream", values="post_id", aggfunc="count", fill_value=0)
    funnel = v1.add_suffix("_v1").join(v2.add_suffix("_v2"), how="outer").fillna(0).astype(int)
    funnel.to_csv(OUT / "v2guide_funnel.csv")
    print(funnel.to_string(), flush=True)
    x_sightings.sightings = sightings_v2(v2t)
    last = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
    x_sightings.final(TEST_FIRST, last, "explore2025_v2guide", "exploratory")


if __name__ == "__main__":
    main()
