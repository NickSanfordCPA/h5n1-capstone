"""The X utility analysis (docs/x_utility_spec.md) run on the Meta posts, under
docs/meta_utility_spec.md. Track A (sentiment and volume) and Track B (sightings) are the X code,
unchanged, pointed at meta_post_funnel: this module swaps the post and sighting readers, the output
folder, the spec file and the frozen pseudocount, and nothing else.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.meta_utility m          # pseudocount
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.meta_utility explore    # 2022-24 only
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.meta_utility smoke      # 2024 dry run
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.meta_utility final      # once, spec committed
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.meta_utility exploratory  # 2025+, no gate

`exploratory` (Nick, 2026-11-11) scores 2025+ with the final() code path but no frozen spec. Its
files are tagged explore2025_* and every result from them is exploratory, not a confirmatory test.

The feature columns keep their x_ names (x_share_7d, x_sent_7d, x_sent_chg) because the X code
keys on that prefix; in this analysis they are built from Meta posts.
"""
from __future__ import annotations

import argparse

import pandas as pd

from h5n1.db import get_engine
from h5n1.features.x_state import estimate_pseudocount
from h5n1.models import x_sightings, x_utility
from h5n1.models.x_baselines import EXPLORE_END, ROOT
from h5n1.sources import meta_funnel

OUT = ROOT / "data" / "Meta_derived" / "meta_utility"
SPEC = ROOT / "docs" / "meta_utility_spec.md"
META_M = 3.3342  # from `m` on 2026-11-11 (posts through 2024, no outcomes) and frozen in docs/meta_utility_spec.md


def use_meta() -> None:
    """Point the X Track A and Track B modules at the Meta posts."""
    x_utility.load_posts = meta_funnel.sentiment_posts
    x_utility.OUT = OUT
    x_utility.SPEC = SPEC
    if META_M is not None:
        x_utility.FROZEN_M = META_M
    x_sightings.load_posts = meta_funnel.sentiment_posts
    x_sightings.sightings = meta_funnel.sightings
    x_sightings.STREAMS = meta_funnel.STREAMS
    x_sightings.OUT = OUT
    OUT.mkdir(parents=True, exist_ok=True)


def pseudocount() -> float:
    posts = meta_funnel.sentiment_posts()
    return estimate_pseudocount(posts[posts.ts.dt.tz_convert(None) <= EXPLORE_END + pd.Timedelta(days=1)])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["m", "explore", "smoke", "final", "exploratory"])
    ap.add_argument("--track", choices=["A", "B", "both"], default="both")
    a = ap.parse_args()
    if a.what == "m":
        print(f"Meta pseudocount m = {pseudocount():.4f}")
        raise SystemExit
    use_meta()
    tracks = {"A": [x_utility], "B": [x_sightings], "both": [x_utility, x_sightings]}[a.track]
    if a.what == "explore":
        for t in tracks:
            t.explore()
    elif a.what == "smoke":
        if x_utility in tracks:
            x_utility.final(pd.Timestamp("2024-01-07"), EXPLORE_END, "smoke", "smoke", 100, 20)
        if x_sightings in tracks:
            x_sightings.final(pd.Timestamp("2024-01-07"), EXPLORE_END, "smoke", "smoke")
    elif a.what == "exploratory":
        last = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
        if x_utility in tracks:
            x_utility.final(x_utility.TEST_FIRST, last, "explore2025", "exploratory", x_utility.N_BOOT_FINAL,
                            x_utility.N_PERM)
        if x_sightings in tracks:
            x_sightings.final(x_utility.TEST_FIRST, last, "explore2025", "exploratory")
    else:
        if META_M is None:
            raise SystemExit("META_M must be frozen before the final run")
        commit = x_utility.require_frozen_spec()
        done = [f for f in ("final_lift.csv", "final_sightings.csv") if (OUT / f).exists()]
        if done:
            raise SystemExit(f"the one-shot run has already been scored ({done}); refusing to run it again")
        last = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
        if x_utility in tracks:
            x_utility.final(x_utility.TEST_FIRST, last, "final", commit, x_utility.N_BOOT_FINAL, x_utility.N_PERM)
        if x_sightings in tracks:
            x_sightings.final(x_utility.TEST_FIRST, last, "final", commit)
