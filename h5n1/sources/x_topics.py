"""LLM review of the X filtered subset: the local-observation pass, then the exclusion screen.

Every filtered-subset post is read; there is no screening model. The readers are in-session
subagents working from the written guides in docs/x_data_funnel.md (Appendix B for the two
yes/no questions, Appendix C for the exclusion screen) -- no API, per Nick. Files live in
each stream's labels/full_review/ (directories as in x_posts.STREAMS):

    uv run python -m h5n1.sources.x_topics batch     --stream S   # batch_<n>.jsonl, 500 posts each
    (readers answer each batch in labels_<n>.csv: _id, local_obs, illness_seen as yes/no)
    uv run python -m h5n1.sources.x_topics merge     --stream S   # -> labels_all.parquet
    uv run python -m h5n1.sources.x_topics exclbatch --stream S   # illness_seen posts -> excl_batch_0.jsonl
    (one reader answers the whole file in excl_labels_<name>.csv: _id, exclude, event_state)
    uv run python -m h5n1.sources.x_topics exclmerge --stream S   # -> illness_posts_screened.parquet

The exclusion screen goes to a single reader in one file: split across readers, the
"secondhand" call came back inconsistent (27 / 0 / 5 per batch on the prior stream).

The labels' record of truth is the x_post_analysis table (local_obs, illness_seen,
exclusion_reason, event_state), which x_analysis_table.build reads back; these files are the
working copies for a fresh review or for posts the table doesn't cover yet.
"""
from __future__ import annotations

import argparse
import json
import re

import pandas as pd

from h5n1.sources.x_location import FIPS_STATE
from h5n1.sources.x_posts import STREAMS

BATCH = 500
SEED = 20261019
EXCLUDE_REASONS = {"", "outside_us", "retrospective", "not_hpai", "secondhand", "not_credible"}
FR = STREAMS["prior"]["out"] / "labels" / "full_review"  # set from --stream in __main__
OUT = STREAMS["prior"]["out"]


def batch() -> None:
    """Every subset post, shuffled, in batches of BATCH. Text only: the reader sees no author,
    location or date."""
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text"])
    FR.mkdir(parents=True, exist_ok=True)
    for f in FR.glob("batch_*.jsonl"):
        f.unlink()
    sub = sub.sample(frac=1, random_state=SEED)
    n = 0
    for n, start in enumerate(range(0, len(sub), BATCH), 1):
        with open(FR / f"batch_{n - 1}.jsonl", "w", encoding="utf-8") as fh:
            for r in sub.iloc[start:start + BATCH].itertuples(index=False):
                fh.write(json.dumps({"_id": r._0, "text": re.sub(r"\s+", " ", r.text)[:600]},
                                    ensure_ascii=False) + "\n")
    print(f"{len(sub):,} posts in {n} batches")


def merge() -> None:
    """labels_<n>.csv -> labels_all.parquet (_id, local_obs, illness_seen, batch).
    illness_seen implies local_obs; every subset post must be answered."""
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id"])
    files = [f for f in FR.glob("labels_*.csv") if re.fullmatch(r"labels_\d+\.csv", f.name)]
    lab = pd.concat(pd.read_csv(f, dtype=str).assign(batch=int(f.stem.split("_")[1])) for f in files)
    for c in ("local_obs", "illness_seen"):
        lab[c] = lab[c].str.strip().str.lower().map({"yes": True, "no": False})
    if lab[["local_obs", "illness_seen"]].isna().any().any() or lab._id.duplicated().any():
        raise ValueError("labels malformed or duplicated")
    if not set(sub._id) <= set(lab._id):
        raise ValueError(f"{len(set(sub._id) - set(lab._id)):,} subset posts have no label")
    lab["illness_seen"] &= lab.local_obs
    lab = lab[lab._id.isin(sub._id)].sort_values(["batch", "_id"])
    lab[["_id", "local_obs", "illness_seen", "batch"]].to_parquet(FR / "labels_all.parquet", index=False)
    print(f"{len(lab):,} posts: {lab.local_obs.sum():,} local_obs, {lab.illness_seen.sum():,} illness_seen")


def exclbatch() -> None:
    """Appendix C input: each illness_seen post with its text, author state and date -- nothing
    else, so the reader is blind to outcomes and to the author's other posts."""
    lo = pd.read_parquet(FR / "labels_all.parquet").query("illness_seen")
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text", "state", "ts"])
    ev = lo[["_id"]].merge(sub, on="_id").sort_values(["ts", "_id"])
    ev[["_id", "state", "ts"]].to_parquet(FR / "illness_posts_ordered.parquet", index=False)
    with open(FR / "excl_batch_0.jsonl", "w", encoding="utf-8") as fh:
        for r in ev.itertuples(index=False):
            fh.write(json.dumps({"_id": r._0, "author_state": r.state, "date": f"{r.ts:%Y-%m-%d}",
                                 "text": re.sub(r"\s+", " ", r.text)[:1000]}, ensure_ascii=False) + "\n")
    print(f"{len(ev):,} illness_seen posts for the exclusion screen")


def exclmerge() -> None:
    """excl_labels_*.csv (_id, exclude, event_state) -> illness_posts_screened.parquet.
    `exclude` is blank to keep the post, else one Appendix C reason; a blank event_state means
    the author's profile state. excl_labels_extra*.csv are read by x_analysis_table instead."""
    ev = pd.read_parquet(FR / "illness_posts_ordered.parquet")
    files = [f for f in sorted(FR.glob("excl_labels_*.csv")) if not f.name.startswith("excl_labels_extra")]
    lab = pd.concat(pd.read_csv(f, dtype=str, keep_default_na=False) for f in files).drop_duplicates("_id")
    lab["exclude"] = lab.exclude.str.strip().str.lower()
    lab["event_state"] = lab.event_state.str.strip().str.upper()
    bad = ~lab.exclude.isin(EXCLUDE_REASONS) | ~lab.event_state.isin(set(FIPS_STATE.values()) | {""})
    if bad.any() or not set(ev._id) <= set(lab._id):
        raise ValueError(f"exclusion labels incomplete or malformed: {lab[bad].head().to_dict('records')}")
    out = ev.merge(lab[["_id", "exclude", "event_state"]], on="_id", how="left")
    out["event_state"] = out.event_state.where(out.event_state != "", out.state)
    out.to_parquet(FR / "illness_posts_screened.parquet", index=False)
    print(out.exclude.replace("", "(kept)").value_counts().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["batch", "merge", "exclbatch", "exclmerge"])
    ap.add_argument("--stream", choices=list(STREAMS), default="prior")
    a = ap.parse_args()
    OUT = STREAMS[a.stream]["out"]
    FR = OUT / "labels" / "full_review"
    {"batch": batch, "merge": merge, "exclbatch": exclbatch, "exclmerge": exclmerge}[a.step]()
