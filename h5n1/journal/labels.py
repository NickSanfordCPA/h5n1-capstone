"""Reader batches and label handling for the unified guide (docs/journal_rebuild/GUIDE_reader.md).

    uv run python -m h5n1.journal.labels pilot              # pilot batches, 2022-2024 posts only
    uv run python -m h5n1.journal.labels check <labels.csv> <batch.jsonl>   # validate one CSV
    uv run python -m h5n1.journal.labels agree <dir>        # agreement between the two reads

Readers are in-session Sonnet subagents; no API. Each post is read twice, independently
(read A and read B). A reader sees id, date, lang and the first 1,500 characters of the
text: never the author, the platform, the geocoded state, or any outcome data.

Layout under data/journal/labels/<wave>/:
    batch_<n>.jsonl                the posts
    A/labels_<n>.csv, B/labels_<n>.csv     the two reads
    A/tmp_<n>/, B/tmp_<n>/         each reader's own scratch folder
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

import pandas as pd

from h5n1.journal.localdb import DATA_DIR
from h5n1.journal.posts import OUT

LAB = DATA_DIR / "labels"
MAX_CHARS = 1500
FIELDS = ["id", "relevant", "topic", "content_class", "local_place", "illness", "exclude",
          "place_state", "place_name"]
TOPICS = {"local_observation", "outbreak_report", "policy_response", "economic", "human_health",
          "science", "off_topic"}
CLASSES = {"domestic_detection", "wildlife_report", "first_hand", "other"}
EXCLUDE = {"", "outside_us", "retrospective", "not_hpai", "not_credible"}
STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
          "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
          "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
          "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP"}
PLACE = STATES | {"US", "FOREIGN", "NONE"}
PILOT_END = pd.Timestamp("2025-01-01", tz="UTC")


def write_batch(df: pd.DataFrame, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in df.itertuples(index=False):
            t = r.text or ""
            f.write(json.dumps({"id": f"{r.platform[0]}{r.post_id}", "date": str(r.day),
                                "lang": r.lang_detected, "text": t[:MAX_CHARS],
                                "truncated": len(t) > MAX_CHARS}, ensure_ascii=False) + "\n")


def pilot(n_per_cell: int = 75, seed: int = 20261117) -> None:
    """Pilot sample: reader-set posts dated before 2025 only, stratified by platform x
    language x geo tier (county/city vs state), so local posts are well represented."""
    p = pd.read_parquet(OUT / "funnel.parquet")
    p = p[p["reader_set"] & (p["created_at"] < PILOT_END)].copy()
    p["local_tier"] = p["geo_tier"].isin(["county", "city", "city_nostate"])
    parts = []
    for (plat, lang, tier), g in p.groupby(["platform", "lang_detected", "local_tier"]):
        parts.append(g.sample(min(n_per_cell, len(g)), random_state=seed))
    s = pd.concat(parts).sample(frac=1, random_state=seed)
    d = LAB / "pilot1"
    for sub in ("A", "B"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    k = 2
    for i in range(k):
        write_batch(s.iloc[i::k], d / f"batch_{i}.jsonl")
    s[["platform", "post_id", "lang_detected", "geo_tier", "state", "day"]].to_csv(
        d / "sample.csv", index=False)
    print(f"pilot: {len(s)} posts in {k} batches under {d}")
    print(s.groupby(["platform", "lang_detected", "local_tier"]).size().to_string())


def check(labels_csv: str, batch_jsonl: str) -> int:
    """Validate one reader CSV against its batch. Prints problems; exit code 1 if any."""
    ids = [json.loads(line)["id"] for line in open(batch_jsonl, encoding="utf-8")]
    with open(labels_csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    errs = []
    if rows and list(rows[0].keys()) != FIELDS:
        errs.append(f"header must be exactly {','.join(FIELDS)}")
    got = [r.get("id", "") for r in rows]
    if len(rows) != len(ids):
        errs.append(f"{len(rows)} rows, expected {len(ids)}")
    if set(got) != set(ids) or len(set(got)) != len(got):
        missing = [i for i in ids if i not in set(got)][:5]
        errs.append(f"ids do not match the batch (missing e.g. {missing})")
    for r in rows:
        i = r.get("id")
        yn = {"yes", "no"}
        if r.get("relevant") not in yn or r.get("local_place") not in yn or r.get("illness") not in yn:
            errs.append(f"{i}: relevant/local_place/illness must be yes or no")
        if r.get("topic") not in TOPICS:
            errs.append(f"{i}: bad topic {r.get('topic')!r}")
        if r.get("content_class") not in CLASSES:
            errs.append(f"{i}: bad content_class {r.get('content_class')!r}")
        if (r.get("exclude") or "") not in EXCLUDE:
            errs.append(f"{i}: bad exclude {r.get('exclude')!r}")
        if r.get("place_state") not in PLACE:
            errs.append(f"{i}: bad place_state {r.get('place_state')!r}")
        if r.get("illness") == "no" and (r.get("exclude") or ""):
            errs.append(f"{i}: exclude must be blank when illness is no")
        if r.get("relevant") == "no" and (r.get("topic") != "off_topic"
                                          or r.get("content_class") != "other"):
            errs.append(f"{i}: relevant=no needs topic=off_topic and content_class=other")
    for e in errs[:40]:
        print("PROBLEM:", e)
    print("OK" if not errs else f"{len(errs)} problems")
    return 1 if errs else 0


def load_reads(d) -> pd.DataFrame:
    frames = []
    for sub in ("A", "B"):
        for f in sorted((d / sub).glob("labels_*.csv")):
            x = pd.read_csv(f, dtype=str, keep_default_na=False)
            x["read"], x["batch"] = sub, f.stem.split("_")[1]
            frames.append(x)
    return pd.concat(frames, ignore_index=True)


def derive(x: pd.DataFrame) -> pd.DataFrame:
    """The analysis-relevant summaries of one read."""
    x = x.copy()
    x["us_state"] = x["place_state"].where(x["place_state"].isin(STATES), "")
    x["place_kind"] = x["place_state"].where(~x["place_state"].isin(STATES), "STATE")
    x["sighting_candidate"] = ((x["relevant"] == "yes") & (x["illness"] == "yes")
                               & (x["local_place"] == "yes") & (x["exclude"] == "")
                               & x["content_class"].isin(["wildlife_report", "first_hand"]))
    return x


def agree(wave: str) -> None:
    from sklearn.metrics import cohen_kappa_score

    r = derive(load_reads(LAB / wave))
    a = r[r["read"] == "A"].set_index("id")
    b = r[r["read"] == "B"].set_index("id")
    ids = a.index.intersection(b.index)
    a, b = a.loc[ids], b.loc[ids]
    print(f"{len(ids)} posts read twice")
    for col in ["relevant", "topic", "content_class", "local_place", "illness", "exclude",
                "place_kind", "place_state", "sighting_candidate"]:
        x, y = a[col].astype(str), b[col].astype(str)
        k = cohen_kappa_score(x, y) if (x.nunique() > 1 or y.nunique() > 1) else float("nan")
        print(f"  {col:20s} agreement {(x == y).mean():.3f}  kappa {k:.3f}")
    print("\ncontent_class, read A (rows) by read B (columns):")
    print(pd.crosstab(a["content_class"], b["content_class"]).to_string())
    print("\nsighting candidates: A", int(a["sighting_candidate"].sum()), " B",
          int(b["sighting_candidate"].sum()), " both",
          int((a["sighting_candidate"] & b["sighting_candidate"]).sum()))
    dis = pd.DataFrame({"A_class": a["content_class"], "B_class": b["content_class"],
                        "A_sight": a["sighting_candidate"], "B_sight": b["sighting_candidate"],
                        "A_state": a["place_state"], "B_state": b["place_state"],
                        "A_rel": a["relevant"], "B_rel": b["relevant"]})
    dis = dis[(dis.A_class != dis.B_class) | (dis.A_sight != dis.B_sight)
              | (dis.A_state != dis.B_state) | (dis.A_rel != dis.B_rel)]
    dis.to_csv(LAB / wave / "disagreements.csv")
    print(f"\n{len(dis)} posts differ on a field that feeds the analysis "
          f"({len(dis) / len(ids):.1%}); written to disagreements.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["pilot", "check", "agree"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    if a.step == "pilot":
        pilot()
    elif a.step == "check":
        sys.exit(check(*a.args))
    else:
        agree(a.args[0])
