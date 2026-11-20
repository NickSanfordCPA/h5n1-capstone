"""Capstone supplement (brief 6.9): language and emoji comparisons. Not for the journal paper.

    uv run python -m h5n1.journal.supplement emoji-inputs
    uv run --extra ml python h5n1/journal/xlmr_score.py --in data/journal/supplement/emoji_in.parquet \
        --out data/journal/supplement/emoji_scores --device cpu \
        --model data/reference/models/twitter-xlm-roberta-base-sentiment
    uv run --extra modeling python -m h5n1.journal.supplement run

Language. Both primaries rerun with English posts only and with Spanish posts only (the
state-week table, forest and expected risk are unchanged; only the posts change).

Emoji. Sentiment-set posts that contain an emoji are rescored with the emoji removed, same
model and preprocessing. Reported: how many posts have an emoji, how the score and the
predicted label move, and Track A with the no-emoji scores in place of the originals.

Outputs in data/journal/supplement/: language_a.csv, language_b.csv, emoji_posts.csv,
emoji_transitions.csv, emoji_a.csv.
"""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd
from sqlalchemy import text

from h5n1.journal import models as M
from h5n1.journal.localdb import DATA_DIR, local_engine

OUT = DATA_DIR / "supplement"
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]+")
LABELS = np.array(["negative", "neutral", "positive"])


def load_posts(with_text: bool = False) -> pd.DataFrame:
    q = text(
        "SELECT f.platform, f.post_id, f.author_id, f.state, f.created_at AS ts, f.week, f.step, "
        "f.sentiment_set, f.lang_detected AS lang, s.sentiment, s.p_neg, s.p_neu, s.p_pos"
        + (", p.text" if with_text else "") +
        " FROM journal.post_funnel f JOIN journal.post p USING (platform, post_id) "
        "LEFT JOIN journal.post_sentiment s USING (platform, post_id) "
        "WHERE f.sentiment_set OR f.step IN (0, 15, 16)")
    with local_engine().connect() as cn:
        d = pd.read_sql(q, cn, parse_dates=["week"])
    d["ts"] = pd.to_datetime(d["ts"], utc=True)
    return d


def emoji_inputs() -> None:
    d = load_posts(with_text=True)
    d = d[d.sentiment_set & d.text.fillna("").str.contains(EMOJI_RE)]
    out = pd.DataFrame({"id": d.platform.str[0] + d.post_id,
                        "text": d.text.map(lambda s: re.sub(r"[ \t]{2,}", " ", EMOJI_RE.sub(" ", s)).strip())})
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT / "emoji_in.parquet", index=False)
    print(f"{len(out):,} sentiment-set posts contain an emoji "
          f"({d.groupby('platform').size().to_dict()})")


def run() -> None:
    from h5n1.journal import sensitivity as S

    OUT.mkdir(parents=True, exist_ok=True)
    params = M.chosen_params("state")
    sw = M.load_state_week("detected")
    posts = load_posts()
    key = ("detected", "searched", "S3", 14)

    a_rows, b_rows = [], []
    for lang in ("en", "es"):
        p = posts[posts.lang == lang]
        a_rows += [{"language": lang, **r} for r in S.track_a(sw, p, params, M.S3, 14, ("meta", "x"))]
        b_rows += [{"language": lang, **r} for r in S.track_b(sw, p, params, M.S3, 14, ("meta", "x"), key)]
        print(f"language {lang} done", flush=True)
    A, B = pd.DataFrame(a_rows), pd.DataFrame(b_rows)
    A.to_csv(OUT / "language_a.csv", index=False)
    B.to_csv(OUT / "language_b.csv", index=False)

    sc = pd.concat([pd.read_parquet(f) for f in sorted((OUT / "emoji_scores").glob("scores_*.parquet"))])
    sc["platform"] = sc.id.str[0].map({"m": "meta", "x": "x"})
    sc["post_id"] = sc.id.str[1:]
    sc["sent_noemoji"] = sc.p_pos - sc.p_neg
    sc["label_noemoji"] = LABELS[sc[["p_neg", "p_neu", "p_pos"]].to_numpy().argmax(axis=1)]
    sent = posts[posts.sentiment_set].merge(sc[["platform", "post_id", "sent_noemoji", "label_noemoji"]],
                                            on=["platform", "post_id"], how="left")
    n_in = len(pd.read_parquet(OUT / "emoji_in.parquet"))
    assert sent.sent_noemoji.notna().sum() == n_in, "not every emoji post was rescored"
    sent["has_emoji"] = sent.sent_noemoji.notna()
    sent["label"] = LABELS[sent[["p_neg", "p_neu", "p_pos"]].to_numpy().argmax(axis=1)]
    e = sent[sent.has_emoji]
    summ = (sent.groupby(["platform", "lang"])
            .agg(posts=("post_id", "size"), with_emoji=("has_emoji", "sum"),
                 mean_sentiment_all=("sentiment", "mean")).reset_index())
    g = e.assign(shift=e.sentiment - e.sent_noemoji).groupby(["platform", "lang"])
    summ = summ.merge(g.agg(mean_with_emoji=("sentiment", "mean"), mean_emoji_removed=("sent_noemoji", "mean"),
                            mean_abs_shift=("shift", lambda s: s.abs().mean()),
                            label_changed=("label", lambda s: (s != e.loc[s.index, "label_noemoji"]).mean()))
                      .reset_index(), on=["platform", "lang"], how="left")
    summ.to_csv(OUT / "emoji_posts.csv", index=False)
    trans = (e.groupby(["platform", "label", "label_noemoji"]).size().rename("posts").reset_index()
             .rename(columns={"label": "with_emoji", "label_noemoji": "emoji_removed"}))
    trans.to_csv(OUT / "emoji_transitions.csv", index=False)

    swapped = posts.merge(sc[["platform", "post_id", "sent_noemoji"]], on=["platform", "post_id"], how="left")
    swapped["sentiment"] = swapped.sent_noemoji.fillna(swapped.sentiment)
    variants = {"original scores": posts, "emoji removed": swapped.drop(columns="sent_noemoji")}
    ea = pd.DataFrame([{"sentiment": name, **r} for name, p in variants.items()
                       for r in S.track_a(sw, p, params, M.S3, 14, ("meta", "x"))])
    ea.to_csv(OUT / "emoji_a.csv", index=False)

    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200)
    cols = ["platform", "variant", "slice", "rows", "events", "pr_auc_ref", "pr_auc_new", "delta", "lo", "hi"]
    print(A[A.comparison == "S4 - S3"][["language"] + cols].round(4).to_string(index=False))
    print(B[["language", "platform", "gap_days", "slice", "sighting_posts", "sighted_state_weeks", "sightings",
             "followed", "expected", "oe_ratio", "lo", "hi"]].round(3).to_string(index=False))
    print(summ.round(3).to_string(index=False))
    print(trans.to_string(index=False))
    print(ea[ea.comparison == "S4 - S3"][["sentiment"] + cols].round(4).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["emoji-inputs", "run"])
    {"emoji-inputs": emoji_inputs, "run": run}[ap.parse_args().what]()
