"""Two-stage `local_observation` labeling for the X topic subset.

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
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "X_derived"
LAB = OUT / "labels"
EMB_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TARGET_RECALL = 0.90
BATCH = 400


def embed(texts: list[str]) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(12)
    tok = AutoTokenizer.from_pretrained(EMB_MODEL)
    mdl = AutoModel.from_pretrained(EMB_MODEL).eval()
    order = np.argsort([len(t) for t in texts])
    out = np.zeros((len(texts), mdl.config.hidden_size), dtype=np.float32)
    for i in range(0, len(texts), 128):
        idx = order[i:i + 128]
        e = tok([texts[j] for j in idx], padding=True, truncation=True, max_length=128, return_tensors="pt")
        with torch.inference_mode():
            h = mdl(**e).last_hidden_state
        v = (h * e["attention_mask"][..., None]).sum(1) / e["attention_mask"].sum(1, keepdim=True)
        out[idx] = torch.nn.functional.normalize(v, dim=1).numpy()
    return out


def screen() -> None:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    gold = pd.read_parquet(LAB / "topic_gold.parquet")
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text"])
    Xg = embed(gold.text.astype(str).tolist())
    y = (gold.topic.astype(str) == "local_observation").to_numpy()
    w = gold.weight.to_numpy(dtype=float)
    clf = LogisticRegression(max_iter=3000, C=4, class_weight="balanced")
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
    for i, start in enumerate(range(0, len(cand), BATCH)):
        with open(LAB / f"lo_review_batch_{i}.jsonl", "w", encoding="utf-8") as f:
            for r in cand.iloc[start:start + BATCH].itertuples(index=False):
                f.write(json.dumps({"_id": r._0, "text": re.sub(r"\s+", " ", r.text)[:600]},
                                   ensure_ascii=False) + "\n")
    print(f"wrote {i + 1} review batches")


def rebatch(threshold: float, recall: float) -> None:
    """Tighten the review threshold after seeing the recall/volume curve, and rewrite batches.

    2026-09-28: the 0.90-recall threshold (0.275) flagged 6,807 posts at 7% precision. The CV
    curve has a knee: 0.55 keeps ~0.81 recall with 2,485 candidates; 0.65 drops to 0.61. So the
    review ran at 0.55 -- about 19% of true local observations are knowingly left unreviewed."""
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text"])
    cand = pd.read_parquet(LAB / "lo_candidates.parquet")
    use = cand[cand.score >= threshold].merge(sub, on="_id")
    for f in LAB.glob("lo_review_batch_*.jsonl"):
        f.unlink()
    for i, start in enumerate(range(0, len(use), 500)):
        with open(LAB / f"lo_review_batch_{i}.jsonl", "w", encoding="utf-8") as fh:
            for r in use.iloc[start:start + 500].itertuples(index=False):
                fh.write(json.dumps({"_id": r._0, "text": re.sub(r"\s+", " ", r.text)[:600]},
                                    ensure_ascii=False) + "\n")
    meta = json.load(open(LAB / "lo_screen.json"))
    meta.update({"review_threshold": threshold, "review_cv_recall": recall, "n_reviewed": len(use)})
    json.dump(meta, open(LAB / "lo_screen.json", "w"), indent=2)
    print(f"{len(use):,} candidates in {i + 1} batches")


def merge() -> None:
    gold = pd.read_parquet(LAB / "topic_gold.parquet", columns=["_id", "topic"])
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
    # only supplies the stricter illness_seen flag
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
    print(f"reviewer agreement with gold on gold positives: {gl.mean():.2f} (n={len(gl)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["screen", "rebatch", "merge"])
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--recall", type=float, default=0.81)
    a = ap.parse_args()
    if a.step == "rebatch":
        rebatch(a.threshold, a.recall)
    else:
        {"screen": screen, "merge": merge}[a.step]()
