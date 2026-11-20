"""Sentiment scores for the reader set after a change to the X corpus.

    uv run python -m h5n1.journal.sentiment todo    # posts that need a score -> x3_in.parquet
    uv run --extra ml python h5n1/journal/xlmr_score.py --in data/journal/sentiment/x3_in.parquet \
        --out data/journal/sentiment/x3_scores --device cpu \
        --model data/reference/models/twitter-xlm-roberta-base-sentiment
    uv run python -m h5n1.journal.sentiment load    # -> journal.post_sentiment

A score depends only on the post's text, so a post whose text is character-identical to one
already scored keeps that score and its `scored` stamp. Every other reader-set post (a new id,
or a returning id whose text differs in any character) is scored from its current text on
primary's CPU. GPU and CPU scores differ by at most about 2e-6 (PROGRESS.md, section 6.5).

History of the score table:
    2026-11-17  every reader-set post scored on cuda2R
                (labels/reader_text_v1.parquet, sentiment/xlmr_scores_v1.parquet)
    2026-11-19  X re-pull: 7,999 posts rescored on CPU (sentiment/x2_in.parquet, x2_scores/)
                (labels/reader_text_v2.parquet, sentiment/xlmr_scores_v2.parquet)
    2026-11-19  repair of the missing query-days: the posts new to the reader set scored on
                CPU (sentiment/x3_in.parquet, x3_scores/)
"""
from __future__ import annotations

import argparse

import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import DATA_DIR, assert_local, local_engine
from h5n1.journal.posts import OUT

SENT = DATA_DIR / "sentiment"
TEXT_PREV = DATA_DIR / "labels" / "reader_text_v2.parquet"   # id, text of the last scored build
SCORES_PREV = SENT / "xlmr_scores_v2.parquet"                # platform, post_id, scores, scored
TODO = SENT / "x3_in.parquet"
NEW = SENT / "x3_scores"
NEW_STAMP = "2026-11-19 primary cpu, repair pull"


def _reader_set() -> pd.DataFrame:
    p = pd.read_parquet(OUT / "funnel.parquet", columns=["platform", "post_id", "reader_set", "text"])
    p = p[p["reader_set"]].assign(post_id=lambda d: d["post_id"].astype(str))
    p["id"] = p["platform"].str[0] + p["post_id"]
    p["same"] = p["id"].map(pd.read_parquet(TEXT_PREV).set_index("id")["text"]) == p["text"]
    return p


def _prev() -> pd.DataFrame:
    s = pd.read_parquet(SCORES_PREV).assign(post_id=lambda d: d["post_id"].astype(str))
    s["id"] = s["platform"].str[0] + s["post_id"]
    return s[["id", "p_neg", "p_neu", "p_pos", "scored"]]


def todo() -> None:
    p = _reader_set()
    p.loc[~p["same"], ["id", "text"]].to_parquet(TODO, index=False)
    print(f"reader set {len(p):,}; text identical to a scored post {int(p['same'].sum()):,}; "
          f"to score {int((~p['same']).sum()):,} -> {TODO}")


def load() -> None:
    p = _reader_set()
    old = _prev()
    new = pd.concat([pd.read_parquet(f) for f in sorted(NEW.glob("scores_*.parquet"))])
    want_new = set(p.loc[~p["same"], "id"])
    if set(new["id"]) != want_new or new["id"].duplicated().any():
        raise SystemExit("the new scores are not exactly the posts that needed one")
    s = pd.concat([old[old["id"].isin(set(p.loc[p["same"], "id"]))],
                   new[["id", "p_neg", "p_neu", "p_pos"]].assign(scored=NEW_STAMP)],
                  ignore_index=True)
    s = p[["platform", "post_id", "id"]].merge(s, on="id", how="left", validate="1:1")
    if s[["p_neg", "p_neu", "p_pos"]].isna().any().any():
        raise SystemExit("a reader-set post has no score")
    s["sentiment"] = s["p_pos"] - s["p_neg"]
    s = s[["platform", "post_id", "p_neg", "p_neu", "p_pos", "sentiment", "scored"]]
    s.to_parquet(SENT / "xlmr_scores.parquet", index=False)
    eng = local_engine()
    assert_local(eng)
    with eng.begin() as cn:
        cn.execute(text("DROP TABLE IF EXISTS journal.post_sentiment"))
        s.astype({c: "float32" for c in ("p_neg", "p_neu", "p_pos", "sentiment")}).to_sql(
            "post_sentiment", cn, schema="journal", index=False, chunksize=5000, method="multi")
        cn.execute(text("ALTER TABLE journal.post_sentiment ADD PRIMARY KEY (platform, post_id)"))
        cn.execute(text(
            "COMMENT ON TABLE journal.post_sentiment IS 'XLM-R cardiffnlp/twitter-xlm-roberta-base-"
            "sentiment, fp32, 512 tokens, by h5n1/journal/xlmr_score.py. One row per reader-set "
            "post; `scored` says which run. sentiment = p_pos - p_neg.'"))
    print(s.groupby(["platform", "scored"]).size().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["todo", "load"])
    {"todo": todo, "load": load}[ap.parse_args().step]()
