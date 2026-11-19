"""Reader batches and label handling for the unified guide (docs/journal_rebuild/GUIDE_reader.md).

    uv run python -m h5n1.journal.labels pilot              # pilot batches, 2022-2024 posts only
    uv run python -m h5n1.journal.labels batches            # fix the full batches (once)
    uv run python -m h5n1.journal.labels focus              # unread Meta, then twitterapi X (once)
    uv run python -m h5n1.journal.labels carry              # after the X re-pull: carry labels, cut wave2
    uv run python -m h5n1.journal.labels carry3             # after the X repair pull: keep labels, cut wave3
    uv run python -m h5n1.journal.labels status             # which reads are done
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
import html
import json
import re
import sys
import unicodedata

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


BATCH = 300
WAVE = "wave1"


def batches(seed: int = 20261117) -> None:
    """Fix the full labelling batches: every reader-set post, in one random order, BATCH posts
    each. The order is random so that a pause never leaves a platform or a period unread.
    Refuses to overwrite: the batches are fixed once, before any post is read."""
    d = LAB / WAVE
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    p = pd.read_parquet(OUT / "funnel.parquet")
    p = p[p["reader_set"]].sample(frac=1, random_state=seed).reset_index(drop=True)
    p["batch"] = p.index // BATCH
    for sub_ in ("A", "B"):
        (d / sub_).mkdir(parents=True, exist_ok=True)
    for n, g in p.groupby("batch"):
        write_batch(g, d / f"batch_{n}.jsonl")
    p[["batch", "platform", "post_id"]].to_csv(d / "manifest.csv", index=False)
    print(f"{len(p):,} posts in {p['batch'].nunique()} batches of {BATCH} under {d}")


FOCUS = "wave1f"


def focus(first_unread: int = 95) -> None:
    """Re-cut the unread wave1 posts (batches >= first_unread) into wave1f, Meta first and then
    the twitterapi X pulls; prior-cohort X (prev_cohort_en) is left out because Max is re-pulling
    that period. Within each group the wave1 random order is kept. Wave1 batches >= first_unread
    are then superseded and must not be read. Refuses to overwrite."""
    d = LAB / FOCUS
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    m = pd.read_csv(LAB / WAVE / "manifest.csv", dtype={"post_id": str})
    m["order"] = range(len(m))
    m = m[m["batch"] >= first_unread].rename(columns={"batch": "wave1_batch"})
    p = pd.read_parquet(OUT / "funnel.parquet")
    p = p[p["reader_set"]].assign(post_id=lambda x: x["post_id"].astype(str))
    p = m.merge(p, on=["platform", "post_id"], how="left", validate="1:1")
    if p["collector"].isna().any():
        raise SystemExit("manifest posts missing from the funnel")
    p = p[p["collector"] != "prev_cohort_en"]
    p["group"] = (p["platform"] == "x").astype(int)
    p = p.sort_values(["group", "order"]).reset_index(drop=True)
    p["batch"] = p.index // BATCH
    for sub_ in ("A", "B"):
        (d / sub_).mkdir(parents=True, exist_ok=True)
    for n, g in p.groupby("batch"):
        write_batch(g, d / f"batch_{n}.jsonl")
    p[["batch", "wave1_batch", "platform", "collector", "post_id"]].to_csv(
        d / "manifest.csv", index=False)
    ins = (LAB / WAVE / "READER_INSTRUCTIONS.md").read_text(encoding="utf-8")
    (d / "READER_INSTRUCTIONS.md").write_text(ins.replace(WAVE, FOCUS), encoding="utf-8")
    print(p.groupby(["platform", "collector"]).size().to_string())
    print(f"{len(p):,} posts in {p['batch'].nunique()} batches under {d}")
    print(p.groupby("batch")["platform"].agg(lambda s: "+".join(sorted(set(s)))).value_counts())


FOCUS_X = "wave1x"


def focusx(first_unread: int = 95) -> None:
    """Cut the prior-cohort X posts that wave1f left out (wave1 batches >= first_unread,
    collector prev_cohort_en) into wave1x, in the wave1 random order. Labels are keyed by post
    id, so they carry over to Max's re-pull wherever the same post returns with the same text.
    Refuses to overwrite."""
    d = LAB / FOCUS_X
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    m = pd.read_csv(LAB / WAVE / "manifest.csv", dtype={"post_id": str})
    m["order"] = range(len(m))
    m = m[m["batch"] >= first_unread].rename(columns={"batch": "wave1_batch"})
    p = pd.read_parquet(OUT / "funnel.parquet")
    p = p[p["reader_set"]].assign(post_id=lambda x: x["post_id"].astype(str))
    p = m.merge(p, on=["platform", "post_id"], how="left", validate="1:1")
    p = p[p["collector"] == "prev_cohort_en"].sort_values("order").reset_index(drop=True)
    done = pd.read_csv(LAB / FOCUS / "manifest.csv", dtype={"post_id": str})
    if len(p) + len(done) != len(m) or set(p["post_id"]) & set(done["post_id"]):
        raise SystemExit("wave1x and wave1f do not partition the unread wave1 posts")
    p["batch"] = p.index // BATCH
    for sub_ in ("A", "B"):
        (d / sub_).mkdir(parents=True, exist_ok=True)
    for n, g in p.groupby("batch"):
        write_batch(g, d / f"batch_{n}.jsonl")
    p[["batch", "wave1_batch", "platform", "collector", "post_id"]].to_csv(
        d / "manifest.csv", index=False)
    ins = (LAB / WAVE / "READER_INSTRUCTIONS.md").read_text(encoding="utf-8")
    (d / "READER_INSTRUCTIONS.md").write_text(ins.replace(WAVE, FOCUS_X), encoding="utf-8")
    print(f"{len(p):,} posts in {p['batch'].nunique()} batches under {d}")


CARRY_WAVE = "wave2"
MERGED_V1 = LAB / "merged_v1.parquet"        # the label table built on the prior-cohort X corpus
TEXT_V1 = LAB / "reader_text_v1.parquet"     # the full text of every post those labels were read from
CARRIED = LAB / "carried.parquet"
_URL = re.compile(r"https?://\S+")


def same_words_key(s: str) -> str:
    """The wording of a post, ignoring what the prior cohort's export altered: it had stripped
    commas and line breaks, and its link and leading-mention text differ from twitterapi's.
    HTML entities decoded, links and leading @mentions removed, NFKC, case-folded, letters and
    digits only."""
    s = unicodedata.normalize("NFKC", _URL.sub("", html.unescape(s or "")))
    s = re.sub(r"^(?:\s*@\w+)+", "", s.strip())
    return re.sub(r"[\W_]+", "", s.casefold())


def carry() -> None:
    """After the X re-pull (2026-11-18): give every reader-set post its existing label where it
    has one, and cut the rest into wave2.

    Labels are keyed by post id. A label carries over when the post is in the old label table
    and its wording is unchanged (`same_words_key`; Nick's decision 2026-11-19). A returning
    post whose wording changed, and a post never read before (a new id, or one an earlier
    funnel step used to remove), goes to wave2 and is read twice like every other post.
    Refuses to overwrite wave2."""
    d = LAB / CARRY_WAVE
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    p = pd.read_parquet(OUT / "funnel.parquet")
    p = p[p["reader_set"]].assign(post_id=lambda x: x["post_id"].astype(str))
    p["id"] = p["platform"].str[0] + p["post_id"]
    old = pd.read_parquet(MERGED_V1).assign(post_id=lambda x: x["post_id"].astype(str))
    old_text = pd.read_parquet(TEXT_V1).set_index("id")["text"]
    p = p.merge(old[["platform", "post_id"]].assign(labelled=True), on=["platform", "post_id"],
                how="left")
    p["labelled"] = p["labelled"].fillna(False).astype(bool)
    p["old_text"] = p["id"].map(old_text)
    if p.loc[p["labelled"], "old_text"].isna().any():
        raise SystemExit("a labelled post has no archived text")
    p["same_text"] = p["labelled"] & (p["old_text"] == p["text"])
    p["same_words"] = p["labelled"] & pd.Series(
        [same_words_key(a) == same_words_key(b)
         for a, b in zip(p["old_text"].fillna(""), p["text"])], index=p.index)
    p["status"] = "never read"
    p.loc[p["labelled"], "status"] = "wording changed"
    p.loc[p["same_words"], "status"] = "carried, same words"
    p.loc[p["same_text"], "status"] = "carried, identical text"
    print(pd.crosstab(p["status"], [p["platform"], p["collector"]], margins=True).to_string())
    meta = p[p["platform"] == "meta"]
    if not (meta["status"] == "carried, identical text").all():
        raise SystemExit("a Meta post changed; Meta must be untouched by the X re-pull")

    keep = p.loc[p["same_words"], ["platform", "post_id", "status"]]
    car = old.merge(keep, on=["platform", "post_id"], validate="1:1").rename(
        columns={"status": "carry"})
    car.to_parquet(CARRIED, index=False)
    todo = p[~p["same_words"]].sample(frac=1, random_state=20261119).reset_index(drop=True)
    todo["batch"] = todo.index // BATCH
    for sub_ in ("A", "B"):
        (d / sub_).mkdir(parents=True, exist_ok=True)
    for n, g in todo.groupby("batch"):
        write_batch(g, d / f"batch_{n}.jsonl")
    todo[["batch", "platform", "collector", "post_id", "status"]].to_csv(d / "manifest.csv",
                                                                         index=False)
    ins = (LAB / WAVE / "READER_INSTRUCTIONS.md").read_text(encoding="utf-8")
    (d / "READER_INSTRUCTIONS.md").write_text(ins.replace(WAVE, CARRY_WAVE), encoding="utf-8")
    print(f"carried {len(car):,} labels -> {CARRIED}")
    print(f"{len(todo):,} posts in {todo['batch'].nunique()} batches under {d}")


REPAIR_WAVE = "wave3"
MERGED_V2 = LAB / "merged_v2.parquet"        # the label table before the repair pull (carried + wave2)
TEXT_V2 = LAB / "reader_text_v2.parquet"     # the text of every reader-set post at that point
CARRIED3 = LAB / "carried3.parquet"


def carry3() -> None:
    """After Max's repair of the missing and cut-short query-days (x_raw_repair, 2026-11-19):
    every reader-set post that already has a label keeps it when its wording is unchanged
    (`same_words_key`, the same rule as `carry`); the rest are cut into wave3 and read twice.
    Writes labels/carried3.parquet (the kept rows of the earlier label table, with their
    original `wave`). Refuses to overwrite wave3."""
    d = LAB / REPAIR_WAVE
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    p = pd.read_parquet(OUT / "funnel.parquet")
    p = p[p["reader_set"]].assign(post_id=lambda x: x["post_id"].astype(str))
    p["id"] = p["platform"].str[0] + p["post_id"]
    old = pd.read_parquet(MERGED_V2).assign(post_id=lambda x: x["post_id"].astype(str))
    old_text = pd.read_parquet(TEXT_V2).set_index("id")["text"]
    p = p.merge(old[["platform", "post_id"]].assign(labelled=True), on=["platform", "post_id"],
                how="left")
    p["labelled"] = p["labelled"].fillna(False).astype(bool)
    p["old_text"] = p["id"].map(old_text)
    if p.loc[p["labelled"], "old_text"].isna().any():
        raise SystemExit("a labelled post has no archived text")
    p["same_text"] = p["labelled"] & (p["old_text"] == p["text"])
    p["same_words"] = p["labelled"] & pd.Series(
        [same_words_key(a) == same_words_key(b)
         for a, b in zip(p["old_text"].fillna(""), p["text"])], index=p.index)
    p["status"] = "never read"
    p.loc[p["labelled"], "status"] = "wording changed"
    p.loc[p["same_words"], "status"] = "kept, same words"
    p.loc[p["same_text"], "status"] = "kept, identical text"
    print(pd.crosstab(p["status"], [p["platform"], p["collector"]], margins=True).to_string())
    meta = p[p["platform"] == "meta"]
    if not (meta["status"] == "kept, identical text").all():
        raise SystemExit("a Meta post changed; Meta must be untouched by the X repair")
    gone = old.merge(p[["platform", "post_id"]], on=["platform", "post_id"], how="left",
                     indicator=True)
    print("labelled posts no longer in the reader set:",
          gone[gone["_merge"] == "left_only"].groupby("platform").size().to_dict())

    keep = p.loc[p["same_words"], ["platform", "post_id", "status"]]
    car = old.merge(keep, on=["platform", "post_id"], validate="1:1").rename(
        columns={"status": "carry"})
    car.to_parquet(CARRIED3, index=False)
    todo = p[~p["same_words"]].sample(frac=1, random_state=20261120).reset_index(drop=True)
    todo["batch"] = todo.index // BATCH
    for sub_ in ("A", "B"):
        (d / sub_).mkdir(parents=True, exist_ok=True)
    for n, g in todo.groupby("batch"):
        write_batch(g, d / f"batch_{n}.jsonl")
    todo[["batch", "platform", "collector", "post_id", "status"]].to_csv(d / "manifest.csv",
                                                                         index=False)
    ins = (LAB / WAVE / "READER_INSTRUCTIONS.md").read_text(encoding="utf-8")
    (d / "READER_INSTRUCTIONS.md").write_text(ins.replace(WAVE, REPAIR_WAVE), encoding="utf-8")
    print(f"kept {len(car):,} labels -> {CARRIED3}")
    print(f"{len(todo):,} posts in {todo['batch'].nunique()} batches under {d}")
    print(todo.groupby(["platform", "collector"]).size().to_string())


def status(wave: str = WAVE) -> None:
    """Which reads are done: a read counts only if its CSV exists and passes the checker."""
    d = LAB / wave
    n = len(list(d.glob("batch_*.jsonl")))
    for sub_ in ("A", "B"):
        have = sorted(int(f.stem.split("_")[1]) for f in (d / sub_).glob("labels_*.csv"))
        bad = [i for i in have if check(str(d / sub_ / f"labels_{i}.csv"),
                                        str(d / f"batch_{i}.jsonl"), quiet=True)]
        done = [i for i in have if i not in bad]
        todo = [i for i in range(n) if i not in set(done)]
        print(f"read {sub_}: {len(done)} of {n} valid; failing the checker: {bad}; "
              f"next to do: {todo[:12]}")


def check(labels_csv: str, batch_jsonl: str, quiet: bool = False) -> int:
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
    if not quiet:
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


ADJ_BATCH = 100
ADJ_INSTRUCTIONS = """# Adjudicator instructions ({wave})

You are adjudicating social-media posts that two independent readers labelled differently.
Do not spawn any subagents. You are given a BATCH number n.

1. Read the guide in full first:
   `C:\\Users\\NickS\\Desktop\\Harvard\\Capstone\\docs\\journal_rebuild\\GUIDE_reader.md`
2. Your batch file (one post per line, JSON):
   `{d}\\batch_<n>.jsonl`
   Each line has the post (`id`, `date`, `lang`, `text`, `truncated`) and the two readers'
   answers, `read_1` and `read_2`. Which reader is 1 and which is 2 was shuffled per post, so
   neither is more trustworthy. The readers differ on at least one of: `relevant`,
   `content_class`, `place_state`, or whether the post is a sighting candidate (relevant=yes,
   illness=yes, local_place=yes, exclude blank, content_class wildlife_report or first_hand).
3. Your own scratch folder (create it; use ONLY this folder for scratch files):
   `{d}\\tmp_<n>`
4. Write your output CSV to:
   `{d}\\labels_<n>.csv`
   Header exactly: `id,relevant,topic,content_class,local_place,illness,exclude,place_state,place_name`
   One row per post: the correct label under the guide, in the guide's own format.
5. Then run the checker from `C:\\Users\\NickS\\Desktop\\Harvard\\Capstone` and fix anything it
   reports until it prints OK:
   `uv run python -m h5n1.journal.labels check "{rel}/labels_<n>.csv" "{rel}/batch_<n>.jsonl"`

Rules:

- Read every post yourself, in chunks of 20-30 lines with the Read tool, and decide each field
  by applying the guide to the text. The readers' answers are evidence, not a menu: usually
  one of them is right on each field, but if the guide gives a third answer, give that.
- Decide every field, not only the ones that differ, and keep the row internally consistent
  under the guide (the checker enforces some of this).
- Do NOT decide with keyword or regex scripts, and do not default to either reader. A script
  may only write out the answers you decided (record decisions chunk by chunk in your scratch
  folder, then assemble the CSV with Python's csv module via `uv run python`).
- Do not read any other file in the labels folder, and never another adjudicator's folder or
  output.
- If the checker cannot be made to print OK, say so plainly in your reply.

Final reply, nothing else: the single word `OK` if the checker printed OK, otherwise one line
saying what failed.
"""


def adjbatches(wave: str, seed: int = 20261118) -> None:
    """Fix the adjudication batches for one wave: every post in disagreements.csv, with its text
    and both reads in a per-post shuffled order (read_1, read_2), so the adjudicator cannot tell
    read A from read B. The shuffle key is kept outside the adj folder. Refuses to overwrite."""
    import numpy as np

    w = LAB / wave
    d = w / "adj"
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    dis = pd.read_csv(w / "disagreements.csv", dtype=str, keep_default_na=False)
    r = load_reads(w)
    a = r[r["read"] == "A"].set_index("id")
    b = r[r["read"] == "B"].set_index("id")
    posts = {}
    for n in sorted(set(a["batch"])):
        for line in open(w / f"batch_{n}.jsonl", encoding="utf-8"):
            p = json.loads(line)
            posts[p["id"]] = p
    ids = sorted(dis["id"])
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(ids))
    a_first = rng.random(len(ids)) < 0.5
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for k, j in enumerate(order):
        i = ids[j]
        ra = {f: a.at[i, f] for f in FIELDS[1:]}
        rb = {f: b.at[i, f] for f in FIELDS[1:]}
        r1, r2 = (ra, rb) if a_first[j] else (rb, ra)
        rows.append({"batch": k // ADJ_BATCH, "id": i, "read_1": "A" if a_first[j] else "B",
                     "line": json.dumps({**posts[i], "read_1": r1, "read_2": r2},
                                        ensure_ascii=False)})
    m = pd.DataFrame(rows)
    for n, g in m.groupby("batch"):
        (d / f"batch_{n}.jsonl").write_text("\n".join(g["line"]) + "\n", encoding="utf-8")
    m[["batch", "id"]].to_csv(d / "manifest.csv", index=False)
    m[["id", "read_1"]].to_csv(w / "adj_key.csv", index=False)
    rel = f"data/journal/labels/{wave}/adj"
    (d / "ADJ_INSTRUCTIONS.md").write_text(
        ADJ_INSTRUCTIONS.format(wave=wave, d=str(d), rel=rel), encoding="utf-8")
    print(f"{len(m):,} posts in {m['batch'].nunique()} adjudication batches under {d}")


def adjstatus(wave: str) -> None:
    """Which adjudication batches are done; every CSV present is run through the checker."""
    d = LAB / wave / "adj"
    n = len(list(d.glob("batch_*.jsonl")))
    ok, bad = [], []
    for i in range(n):
        f = d / f"labels_{i}.csv"
        if f.exists():
            (bad if check(str(f), str(d / f"batch_{i}.jsonl"), quiet=True) else ok).append(i)
    todo = [i for i in range(n) if i not in ok and i not in bad]
    print(f"adjudication {wave}: {len(ok)} of {n} valid; failing: {bad}; not written: {todo}")


def adjsummary(wave: str) -> None:
    """How the adjudicator decided, per analysis field: with read A, with read B, or neither."""
    w = LAB / wave
    adj = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False)
                     for f in sorted((w / "adj").glob("labels_*.csv"))], ignore_index=True)
    adj = derive(adj).set_index("id")
    r = derive(load_reads(w))
    a = r[r["read"] == "A"].set_index("id").loc[adj.index]
    b = r[r["read"] == "B"].set_index("id").loc[adj.index]
    print(f"{len(adj):,} adjudicated posts in {wave}")
    for col in ["relevant", "content_class", "place_state", "sighting_candidate"]:
        x, ya, yb = adj[col].astype(str), a[col].astype(str), b[col].astype(str)
        diff = ya != yb
        print(f"  {col:20s} readers differed on {int(diff.sum()):5d}: with A "
              f"{int((diff & (x == ya)).sum()):5d}, with B {int((diff & (x == yb)).sum()):5d}, "
              f"neither {int((diff & (x != ya) & (x != yb)).sum()):4d}; "
              f"readers agreed but overruled {int((~diff & (x != ya)).sum()):4d}")
    print("  sighting candidates: A", int(a["sighting_candidate"].sum()), " B",
          int(b["sighting_candidate"].sum()), " adjudicated", int(adj["sighting_candidate"].sum()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["pilot", "batches", "focus", "focusx", "carry", "carry3", "status", "check",
                                     "agree",
                                     "adjbatches", "adjstatus", "adjsummary"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    if a.step == "pilot":
        pilot()
    elif a.step == "batches":
        batches()
    elif a.step == "focus":
        focus()
    elif a.step == "focusx":
        focusx()
    elif a.step == "carry":
        carry()
    elif a.step == "carry3":
        carry3()
    elif a.step == "status":
        status(*a.args)
    elif a.step == "check":
        sys.exit(check(*a.args))
    elif a.step in ("adjbatches", "adjstatus", "adjsummary"):
        globals()[a.step](a.args[0])
    else:
        agree(a.args[0])
