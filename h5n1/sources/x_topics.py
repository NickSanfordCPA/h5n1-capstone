"""Two-stage `local_observation` labeling for the X topic subset, then the exclusion screen.

Stage 1 (`screen`): a high-recall screen. MiniLM sentence embeddings + logistic regression,
trained on the 2,000-post hand-labeled gold set (labels/topic_gold.parquet), local_observation
vs everything else. The threshold is picked from 5-fold cross-validated scores on the gold set
so that screen recall >= TARGET_RECALL (population-weighted), then applied to every
unlabeled post in topic_subset.parquet. Candidates are written as review batches.

Stage 2: the candidates are reviewed in-session (subagents, same guide, binary yes/no) --
no API, per Nick. Their answers land in labels/lo_review_labels_*.csv.

`merge` combines gold labels and reviewed positives into local_observation.parquet
(_id, local_obs, source). Posts the screen did not flag are local_obs = False; the measured
screen recall says how many true observations that loses.

Why not the classifier alone: cross-validated, it reaches only 0.24 population-weighted
precision on local_observation (0.62 recall) -- 3 in 4 flagged posts would be wrong.

STREAMS (Amendment 1 of docs/x_event_preregistration.md; directories as in x_posts):
  prior    as above; frozen.
  en_test  `screen` uses the frozen screen, never refit on new posts: the LR is fit on the
           stored gold embeddings (labels/gold_minilm.npy) and posts are flagged at the
           prior stream's review threshold (0.55). Every flagged post is reviewed.
  es, fr   no screen -- the gold set and MiniLM are English-only. `allbatch` sends every
           subset post to review.
Then, every stream: `exclbatch` writes each illness_seen post (text, author state, date only)
for the Appendix C exclusion screen, and `exclmerge` turns the reviewer's
labels/excl_labels_*.csv into labels/illness_posts_screened.parquet, the event file that
x_event_study and x_lift read.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

import numpy as np
import pandas as pd

from h5n1.sources.x_location import FIPS_STATE
from h5n1.sources.x_posts import STREAMS

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOLD_LAB = ROOT / "data" / "X_derived" / "labels"  # gold set + frozen screen, every stream
OUT = ROOT / "data" / "X_derived"  # set from --stream in __main__
LAB = OUT / "labels"
EMB_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TARGET_RECALL = 0.90
BATCH = 400
EXCLUDE_REASONS = {"", "outside_us", "retrospective", "not_hpai", "secondhand", "not_credible"}


def embed(texts: list[str]) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(12)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(EMB_MODEL)
    mdl = AutoModel.from_pretrained(EMB_MODEL).eval().to(dev)
    order = np.argsort([len(t) for t in texts])
    out = np.zeros((len(texts), mdl.config.hidden_size), dtype=np.float32)
    for i in range(0, len(texts), 128):
        idx = order[i:i + 128]
        e = tok([texts[j] for j in idx], padding=True, truncation=True, max_length=128,
                return_tensors="pt").to(dev)
        with torch.inference_mode():
            h = mdl(**e).last_hidden_state
        v = (h * e["attention_mask"][..., None]).sum(1) / e["attention_mask"].sum(1, keepdim=True)
        out[idx] = torch.nn.functional.normalize(v, dim=1).cpu().numpy()
    return out


def _write_batches(df: pd.DataFrame, size: int) -> int:
    for f in LAB.glob("lo_review_batch_*.jsonl"):
        f.unlink()
    n = 0
    for n, start in enumerate(range(0, len(df), size), 1):
        with open(LAB / f"lo_review_batch_{n - 1}.jsonl", "w", encoding="utf-8") as fh:
            for r in df.iloc[start:start + size].itertuples(index=False):
                fh.write(json.dumps({"_id": r._0, "text": re.sub(r"\s+", " ", r.text)[:600]},
                                    ensure_ascii=False) + "\n")
    return n


def screen(stream: str = "prior") -> None:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    gold = pd.read_parquet(GOLD_LAB / "topic_gold.parquet")
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text"])
    y = (gold.topic.astype(str) == "local_observation").to_numpy()
    clf = LogisticRegression(max_iter=3000, C=4, class_weight="balanced")
    LAB.mkdir(parents=True, exist_ok=True)

    if stream != "prior":
        # frozen screen: stored gold embeddings, prior review threshold, no refit on new posts
        frozen = json.load(open(GOLD_LAB / "lo_screen.json"))
        thr = frozen["review_threshold"]
        clf.fit(np.load(GOLD_LAB / "gold_minilm.npy"), y)
        rest = sub[~sub._id.isin(gold._id)].reset_index(drop=True)
        p = clf.predict_proba(embed(rest.text.astype(str).tolist()))[:, 1]
        cand = rest[p >= thr].assign(score=p[p >= thr]).sample(frac=1, random_state=0)
        cand[["_id", "score"]].to_parquet(LAB / "lo_candidates.parquet", index=False)
        json.dump({"screen": "frozen prior-stream LR on gold_minilm.npy", "review_threshold": thr,
                   "n_unlabeled": len(rest), "n_flagged": len(cand), "n_reviewed": len(cand)},
                  open(LAB / "lo_screen.json", "w"), indent=2)
        n = _write_batches(cand, 500)
        print(f"unlabeled posts {len(rest):,}; flagged {len(cand):,} ({len(cand) / len(rest):.3%}) "
              f"at {thr}; {n} review batches")
        return

    Xg = embed(gold.text.astype(str).tolist())
    w = gold.weight.to_numpy(dtype=float)
    cv = cross_val_predict(clf, Xg, y, cv=StratifiedKFold(5, shuffle=True, random_state=0),
                           method="predict_proba")[:, 1]
    # lowest threshold that still keeps population-weighted recall >= target
    pos = np.sort(cv[y])[::-1]
    pos_w = w[y][np.argsort(cv[y])[::-1]]
    k = np.searchsorted(np.cumsum(pos_w) / pos_w.sum(), TARGET_RECALL)
    thr = float(pos[min(k, len(pos) - 1)])
    rec = w[y & (cv >= thr)].sum() / w[y].sum()
    prec = w[y & (cv >= thr)].sum() / w[cv >= thr].sum()
    print(f"CV threshold {thr:.3f}: weighted recall {rec:.2f}, weighted precision {prec:.2f}")

    clf.fit(Xg, y)
    rest = sub[~sub._id.isin(gold._id)].reset_index(drop=True)
    p = clf.predict_proba(embed(rest.text.astype(str).tolist()))[:, 1]
    cand = rest[p >= thr].assign(score=p[p >= thr]).sample(frac=1, random_state=0)
    print(f"unlabeled posts {len(rest):,}; flagged {len(cand):,} ({len(cand) / len(rest):.3%})")
    cand[["_id", "score"]].to_parquet(LAB / "lo_candidates.parquet", index=False)
    json.dump({"threshold": thr, "cv_recall": rec, "cv_precision": prec, "target_recall": TARGET_RECALL,
               "n_unlabeled": len(rest), "n_flagged": len(cand)},
              open(LAB / "lo_screen.json", "w"), indent=2)
    n = _write_batches(cand, BATCH)
    print(f"wrote {n} review batches")


def allbatch() -> None:
    """No screen (the es and fr streams): every subset post is a candidate and is reviewed."""
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text"])
    LAB.mkdir(parents=True, exist_ok=True)
    cand = sub.sample(frac=1, random_state=0).assign(score=1.0)
    cand[["_id", "score"]].to_parquet(LAB / "lo_candidates.parquet", index=False)
    json.dump({"screen": "none (every subset post reviewed)", "review_threshold": 0.0,
               "n_unlabeled": len(sub), "n_flagged": len(cand), "n_reviewed": len(cand)},
              open(LAB / "lo_screen.json", "w"), indent=2)
    print(f"{len(cand):,} posts in {_write_batches(cand, 500)} review batches")


def rebatch(threshold: float, recall: float) -> None:
    """Tighten the review threshold after seeing the recall/volume curve, and rewrite batches.

    2026-09-28: the 0.90-recall threshold (0.275) flagged 6,807 posts at 7% precision. The CV
    curve has a knee: 0.55 keeps ~0.81 recall with 2,485 candidates; 0.65 drops to 0.61. So the
    review ran at 0.55 -- about 19% of true local observations are knowingly left unreviewed."""
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text"])
    cand = pd.read_parquet(LAB / "lo_candidates.parquet")
    use = cand[cand.score >= threshold].merge(sub, on="_id")
    n = _write_batches(use, 500)
    meta = json.load(open(LAB / "lo_screen.json"))
    meta.update({"review_threshold": threshold, "review_cv_recall": recall, "n_reviewed": len(use)})
    json.dump(meta, open(LAB / "lo_screen.json", "w"), indent=2)
    print(f"{len(use):,} candidates in {n} batches")


def merge() -> None:
    gold = pd.read_parquet(GOLD_LAB / "topic_gold.parquet", columns=["_id", "topic"])
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id"])
    thr = json.load(open(LAB / "lo_screen.json")).get("review_threshold", 0.0)
    cand = pd.read_parquet(LAB / "lo_candidates.parquet").query("score >= @thr")
    rev = pd.concat(pd.read_csv(f, dtype=str) for f in sorted(LAB.glob("lo_review_labels_*.csv")))
    for col in ("local_obs", "illness_seen"):
        rev[col] = rev[col].str.strip().str.lower().map({"yes": True, "no": False})
    if rev[["local_obs", "illness_seen"]].isna().any().any() or not set(cand._id) <= set(rev._id):
        raise ValueError("review labels incomplete or malformed")
    rev = rev.drop_duplicates("_id").set_index("_id")
    rev["illness_seen"] &= rev.local_obs
    # gold keeps its own local_observation call; the re-review of gold positives (batch 5)
    # only supplies the stricter illness_seen flag. Gold posts exist only in the prior stream.
    gold_lo = gold.set_index("_id").topic.astype(str).eq("local_observation")
    out = sub.assign(local_obs=False, illness_seen=False, source="screened_out")
    g = out._id.isin(gold._id)
    out.loc[g, "local_obs"] = out.loc[g, "_id"].map(gold_lo)
    out.loc[g, "source"] = "gold"
    c = out._id.isin(cand._id)
    out.loc[c, "local_obs"] = out.loc[c, "_id"].map(rev.local_obs)
    out.loc[c, "source"] = "reviewed"
    has = out._id.isin(rev.index) & out.local_obs
    out.loc[has, "illness_seen"] = out.loc[has, "_id"].map(rev.illness_seen)
    out.to_parquet(OUT / "local_observation.parquet", index=False)
    print(out.groupby("source")[["local_obs", "illness_seen"]].sum().assign(
        n=out.groupby("source").size()).to_string())
    print(f"screen precision after review: {rev.loc[rev.index.isin(cand._id), 'local_obs'].mean():.2f}")
    gl = rev.loc[rev.index.isin(gold_lo[gold_lo].index), "local_obs"]
    if len(gl):
        print(f"reviewer agreement with gold on gold positives: {gl.mean():.2f} (n={len(gl)})")


def exclbatch() -> None:
    """Appendix C input: each illness_seen post with its text, author state and date -- nothing
    else, so the reviewer is blind to outcomes and to the author's other posts."""
    lo = pd.read_parquet(OUT / "local_observation.parquet").query("illness_seen")
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text", "state", "ts"])
    ev = lo[["_id"]].merge(sub, on="_id").sort_values("ts")
    ev[["_id", "state", "ts"]].to_parquet(LAB / "illness_posts_ordered.parquet", index=False)
    with open(LAB / "excl_batch_0.jsonl", "w", encoding="utf-8") as fh:
        for r in ev.itertuples(index=False):
            fh.write(json.dumps({"_id": r._0, "author_state": r.state, "date": f"{r.ts:%Y-%m-%d}",
                                 "text": re.sub(r"\s+", " ", r.text)[:1000]}, ensure_ascii=False) + "\n")
    print(f"{len(ev):,} illness_seen posts for the exclusion screen")


def exclmerge() -> None:
    """labels/excl_labels_*.csv (_id, exclude, event_state) -> illness_posts_screened.parquet.
    `exclude` is blank to keep the post, else one Appendix C reason; a blank event_state means
    the author's profile state."""
    ev = pd.read_parquet(LAB / "illness_posts_ordered.parquet")
    lab = pd.concat(pd.read_csv(f, dtype=str, keep_default_na=False)
                    for f in sorted(LAB.glob("excl_labels_*.csv"))).drop_duplicates("_id")
    lab["exclude"] = lab.exclude.str.strip().str.lower()
    lab["event_state"] = lab.event_state.str.strip().str.upper()
    bad = ~lab.exclude.isin(EXCLUDE_REASONS) | ~lab.event_state.isin(set(FIPS_STATE.values()) | {""})
    if bad.any() or not set(ev._id) <= set(lab._id):
        raise ValueError(f"exclusion labels incomplete or malformed: {lab[bad].head().to_dict('records')}")
    out = ev.merge(lab[["_id", "exclude", "event_state"]], on="_id", how="left")
    out["event_state"] = out.event_state.where(out.event_state != "", out.state)
    out.to_parquet(LAB / "illness_posts_screened.parquet", index=False)
    print(out.exclude.replace("", "(kept)").value_counts().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["screen", "allbatch", "rebatch", "merge", "exclbatch", "exclmerge"])
    ap.add_argument("--stream", choices=list(STREAMS), default="prior")
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--recall", type=float, default=0.81)
    a = ap.parse_args()
    OUT = STREAMS[a.stream]["out"]
    LAB = OUT / "labels"
    if a.stream == "prior" and a.step in ("exclbatch", "exclmerge"):
        raise SystemExit("the prior stream's exclusion screen is frozen (labels/illness_posts_screened.parquet)")
    if a.step == "rebatch":
        rebatch(a.threshold, a.recall)
    elif a.step == "screen":
        screen(a.stream)
    else:
        {"allbatch": allbatch, "merge": merge, "exclbatch": exclbatch, "exclmerge": exclmerge}[a.step]()
