"""The label half of the funnel (steps 9-16, protocol section 6.4), identical for Meta and X.

    uv run python -m h5n1.journal.label_funnel merge    # one label row per reader-set post
    uv run python -m h5n1.journal.label_funnel build    # steps 9-16 -> journal.post_funnel
    uv run python -m h5n1.journal.label_funnel report   # docs/journal_rebuild/funnel.md

merge: one row per reader-set post. Since the X re-pull (2026-11-19) the rows come from two
places: labels carried over from the table built on the earlier corpus (labels/carried.parquet,
written by `labels.py carry`; those rows were merged from wave1 batches 0-94, wave1f and wave1x
by this same rule), wave2, the posts read after the re-pull, and wave3, the posts read after
the repair of the missing query-days (labels/carried3.parquet holds the kept rows).

  * Where the two reads differed on an analysis field the post was adjudicated, and the
    adjudicator's whole row is used. Its topic is not: protocol 6.1 keeps read A's topic.
    The one exception is a topic the adjudicated `relevant` makes invalid (relevant=no needs
    off_topic; relevant=yes with read A's off_topic takes the adjudicator's topic).
  * Otherwise the reads agree on relevant, content_class, place_state and sighting status,
    and read A's row is used (so read A also supplies topic, place_name and any of
    local_place / illness / exclude on which the reads differ without changing sighting
    status).

build: each step removes a post for ONE reason; `step` is the first step that removes it
(0 = a Track B sighting). The sentiment set (Track A) is every post that passes step 10.
A post's week is the week ending Sunday that contains its UTC date.

Nothing here reads outcome data.
"""
from __future__ import annotations

import argparse
import re
import unicodedata

import pandas as pd
from sqlalchemy import text

from h5n1.journal.funnel import STEPS as STEPS_1_8
from h5n1.journal.labels import FIELDS, LAB, STATES, derive
from h5n1.journal.localdb import REPO, assert_local, local_engine
from h5n1.journal.posts import OUT

# wave -> first superseded batch. Earlier waves arrive in the kept-label file, which
# labels.py carry3 cut from the previous label table (it keeps each row's original wave).
WAVES = {"wave3": None}
CARRIED = LAB / "carried3.parquet"
MERGED = LAB / "merged.parquet"
FUNNEL = OUT / "label_funnel.parquet"
DOC = REPO / "docs" / "journal_rebuild" / "funnel.md"
NOT_CONTIGUOUS = {"AK", "HI", "PR", "GU", "VI", "AS", "MP"}
EXCLUDE_NAMES = {"outside_us": "Outside the US", "retrospective": "Retrospective",
                 "not_hpai": "Not HPAI", "not_credible": "Not credible"}
STEPS = {9: "Not about avian influenza (reader)", 10: "Not about one US state (reader)",
         11: "No sick or dead animals at a local place", 12: "Announced domestic detection",
         13: "Other content", 14: "Excluded by the reader", 15: "Outside the contiguous states",
         16: "Same state-week as an earlier sighting", 0: "Sighting"}
PERIOD_SPLIT = pd.Timestamp("2025-01-01", tz="UTC")


def _read_csvs(folder, batches) -> pd.DataFrame:
    return pd.concat([pd.read_csv(folder / f"labels_{n}.csv", dtype=str, keep_default_na=False)
                      for n in batches], ignore_index=True)


def merge() -> None:
    if list(LAB.rglob("corrections.csv")):
        raise SystemExit("a corrections.csv exists; merge does not apply corrections yet")
    parts = []
    for wave, first_superseded in WAVES.items():
        w = LAB / wave
        m = pd.read_csv(w / "manifest.csv", dtype={"post_id": str})
        if first_superseded is not None:
            m = m[m["batch"] < first_superseded]
        m["id"] = m["platform"].str[0] + m["post_id"]
        batches = sorted(m["batch"].unique())
        a = _read_csvs(w / "A", batches).set_index("id")
        b = _read_csvs(w / "B", batches).set_index("id")
        if set(a.index) != set(m["id"]) or set(b.index) != set(m["id"]) \
                or a.index.has_duplicates or b.index.has_duplicates:
            raise SystemExit(f"{wave}: reads do not match the manifest")
        b = b.loc[a.index]
        da, db = derive(a.reset_index()).set_index("id"), derive(b.reset_index()).set_index("id")
        differ = ((a["relevant"] != b["relevant"]) | (a["content_class"] != b["content_class"])
                  | (a["place_state"] != b["place_state"])
                  | (da["sighting_candidate"] != db["sighting_candidate"]))
        adj_n = sorted(int(f.stem.split("_")[1]) for f in (w / "adj").glob("labels_*.csv"))
        adj = _read_csvs(w / "adj", adj_n).set_index("id")
        if set(adj.index) != set(differ[differ].index) or adj.index.has_duplicates:
            raise SystemExit(f"{wave}: adjudicated posts are not exactly the posts that differ")
        out = a.copy()
        out.loc[adj.index, FIELDS[1:]] = adj[FIELDS[1:]]
        # topic: read A's, unless the adjudicated `relevant` makes it invalid
        a_topic = a.loc[adj.index, "topic"]
        topic = a_topic.where(~((adj["relevant"] == "yes") & (a_topic == "off_topic")), adj["topic"])
        topic = topic.where(adj["relevant"] == "yes", "off_topic")
        out.loc[adj.index, "topic"] = topic
        out["label_source"] = "reads"
        out.loc[adj.index, "label_source"] = "adjudicated"
        out["wave"] = wave
        out["topic_reads_agree"] = a["topic"] == b["topic"]
        out = out.reset_index().merge(m[["id", "platform", "post_id"]], on="id", validate="1:1")
        parts.append(out)
        print(f"{wave}: {len(out):,} posts, {len(adj):,} adjudicated")
    keep = ["platform", "post_id", "wave", "label_source", "topic_reads_agree"] + FIELDS[1:]
    car = pd.read_parquet(CARRIED).assign(post_id=lambda d: d["post_id"].astype(str))
    print(f"carried: {len(car):,} posts ({car['carry'].value_counts().to_dict()})")
    lab = pd.concat([car[keep]] + [q[keep] for q in parts], ignore_index=True)

    rs = pd.read_parquet(OUT / "funnel.parquet", columns=["platform", "post_id", "reader_set"])
    rs = rs[rs["reader_set"]]
    key = lambda d: set(zip(d["platform"], d["post_id"].astype(str)))  # noqa: E731
    if lab.duplicated(["platform", "post_id"]).any() or key(lab) != key(rs):
        raise SystemExit("merged labels are not exactly one row per reader-set post")
    bad = (lab["relevant"] == "no") & ((lab["topic"] != "off_topic") | (lab["content_class"] != "other"))
    bad |= (lab["illness"] == "no") & (lab["exclude"] != "")
    if bad.any():
        raise SystemExit(f"{int(bad.sum())} merged rows break the checker's consistency rules")
    lab = lab[keep]
    lab.to_parquet(MERGED, index=False)
    eng = local_engine()
    assert_local(eng)
    with eng.begin() as cn:
        cn.execute(text("DROP TABLE IF EXISTS journal.post_label"))
        lab.to_sql("post_label", cn, schema="journal", index=False, chunksize=5000, method="multi")
        cn.execute(text("ALTER TABLE journal.post_label ADD PRIMARY KEY (platform, post_id)"))
        cn.execute(text(
            "COMMENT ON TABLE journal.post_label IS 'Journal rebuild: one reader label row per "
            "reader-set post. Adjudicated row where the two reads differed on an analysis field, "
            "otherwise read A (reads agree on those fields). After the X re-pull: carried labels "
            "plus wave2 and wave3. Built by label_funnel.py merge.'"))
    print(f"{len(lab):,} posts -> {MERGED} and journal.post_label")
    print(lab.groupby(["platform", "label_source"]).size().unstack().to_string())


def _place_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"[^\w]+", " ", s).strip()


def build() -> None:
    p = pd.read_parquet(OUT / "funnel.parquet", columns=[
        "platform", "post_id", "collector", "created_at", "lang_detected", "state", "geo_tier",
        "author_id", "reader_set"])
    p = p[p["reader_set"]].drop(columns="reader_set").rename(columns={"state": "geo_state"})
    lab = pd.read_parquet(MERGED)
    d = p.merge(lab, on=["platform", "post_id"], validate="1:1")
    assert len(d) == len(p) == len(lab)
    day = d["created_at"].dt.tz_convert("UTC").dt.normalize().dt.tz_localize(None)
    d["day"] = day.dt.date
    d["week"] = (day + pd.to_timedelta(6 - day.dt.weekday, unit="D")).dt.date   # week-ending Sunday
    d["period"] = (d["created_at"] < PERIOD_SPLIT).map({True: "2022-24", False: "2025+"})

    step = pd.Series(-1, index=d.index)
    reason = pd.Series("", index=d.index)

    def cut(n: int, mask: pd.Series, name: str | None = None) -> None:
        m = (step == -1) & mask
        step.loc[m] = n
        reason.loc[m] = name or STEPS[n]

    is_state = d["place_state"].isin(STATES)
    cut(9, d["relevant"] == "no")
    cut(10, ~is_state)
    d["sentiment_set"] = step == -1
    d["state"] = d["place_state"].where(d["sentiment_set"])
    d["state_moved"] = d["sentiment_set"] & (d["state"] != d["geo_state"])
    cut(11, (d["illness"] == "no") | (d["local_place"] == "no"))
    cut(12, d["content_class"] == "domestic_detection")
    cut(13, d["content_class"] == "other")
    for code, name in EXCLUDE_NAMES.items():
        cut(14, d["exclude"] == code, name)
    cand = derive(d)["sighting_candidate"] & is_state
    if not (cand == (step == -1)).all():
        raise SystemExit("steps 9-14 do not reproduce the sighting-candidate definition")
    cut(15, d["state"].isin(NOT_CONTIGUOUS))
    d["sighting_post"] = step == -1          # every post that is a sighting before step 16

    s = d[d["sighting_post"]].sort_values(["created_at", "post_id"], kind="stable")
    first = s.drop_duplicates(["platform", "state", "week"]).index
    cut(16, ~d.index.isin(first))
    step.loc[step == -1] = 0
    reason.loc[step == 0] = STEPS[0]
    d["step"], d["disposition"] = step, reason

    g = s.assign(place=s["place_name"].map(_place_key)).groupby(["platform", "state", "week"])
    counts = g.agg(n_sighting_posts=("post_id", "size"),
                   n_places=("place", lambda x: max(1, x[x != ""].nunique()))).reset_index()
    d = d.merge(counts, on=["platform", "state", "week"], how="left", validate="m:1")
    d.loc[d["step"] != 0, ["n_sighting_posts", "n_places"]] = pd.NA
    for c in ("n_sighting_posts", "n_places"):
        d[c] = d[c].astype("Int64")

    cols = ["platform", "post_id", "collector", "author_id", "created_at", "day", "week", "period",
            "lang_detected", "geo_tier", "geo_state", "state", "state_moved", "label_source",
            "relevant", "topic", "content_class", "local_place", "illness", "exclude",
            "place_state", "place_name", "sentiment_set", "sighting_post", "step", "disposition",
            "n_sighting_posts", "n_places"]
    d = d[cols]
    d.to_parquet(FUNNEL, index=False)
    eng = local_engine()
    assert_local(eng)
    with eng.begin() as cn:
        cn.execute(text("DROP TABLE IF EXISTS journal.post_funnel"))
        d.to_sql("post_funnel", cn, schema="journal", index=False, chunksize=2000, method="multi")
        cn.execute(text("ALTER TABLE journal.post_funnel ADD PRIMARY KEY (platform, post_id)"))
        cn.execute(text("CREATE INDEX ix_post_funnel_sw ON journal.post_funnel (platform, state, week)"))
        cn.execute(text(
            "COMMENT ON TABLE journal.post_funnel IS 'Journal rebuild: every reader-set post with "
            "its label-step disposition (steps 9-16; step 0 = Track B sighting, one per platform "
            "state-week), sentiment_set (Track A) and the reader state. Built by label_funnel.py "
            "build. No outcome data.'"))
    print(f"{len(d):,} posts -> {FUNNEL} and journal.post_funnel")
    print(pd.crosstab([d["step"], d["disposition"]], d["platform"], margins=True).to_string())
    print("sentiment set:", d.groupby("platform")["sentiment_set"].sum().to_dict(),
          " moved state:", d.groupby("platform")["state_moved"].sum().to_dict())


def _row(label: str, vals, bold: bool = False) -> str:
    cells = [f"{int(v):,}" for v in vals]
    if bold:
        label, cells = f"**{label}**", [f"**{c}**" for c in cells]
    return f"| {label} | " + " | ".join(cells) + " |"


def report() -> None:
    d = pd.read_parquet(FUNNEL)
    allp = pd.read_parquet(OUT / "funnel.parquet", columns=["platform", "disposition_step"])
    raw = pd.read_parquet(OUT / "raw_rows.parquet", columns=["platform"])["platform"].value_counts()
    plats = ["meta", "x"]
    n = lambda mask, df=d: [int((mask & (df["platform"] == q)).sum()) for q in plats]  # noqa: E731
    L = []
    w = L.append
    w("# Post funnel: Meta and X side by side\n")
    w("This document counts what happens to every collected post on its way to the two analysis "
      "sets. One code path handles both platforms. Steps 1 to 8 are fixed rules "
      "(`h5n1/journal/funnel.py`); steps 9 to 16 use the reader labels "
      "(`h5n1/journal/label_funnel.py`). Each post is removed by the first step that applies to "
      "it, so the rows of the table sum to the total. Two sets come out: the **sentiment set** "
      "(Track A), every post a reader judged to be about avian influenza in one US state, and "
      "the **sightings** (Track B), posts about sick or dead animals at a named local place "
      "that are wildlife reports or first-hand accounts, kept as one per state-week per "
      "platform. No outcome data is used anywhere in the funnel.\n")
    w("Generated by `uv run python -m h5n1.journal.label_funnel report`; do not edit by hand.\n")
    w("## The funnel\n")
    w("| # | Disposition | Meta | X |")
    w("|---|---|---:|---:|")
    w("| " + _row("", [raw.get(q, 0) for q in plats], True)[2:].replace("**** | ", "| **Rows collected** | ", 1))
    w(f"| 1 | {STEPS_1_8[1]} | " + " | ".join(
        f"{int(raw.get(q, 0) - (allp['platform'] == q).sum()):,}" for q in plats) + " |")
    w("| " + _row("Unique posts", [(allp["platform"] == q).sum() for q in plats], True))
    for k in range(2, 9):
        w(f"| {k} " + _row(STEPS_1_8[k], n(allp["disposition_step"] == k, allp)))
    w("| " + _row("Reader set", n(d["step"] >= 0), True))
    for k in (9, 10):
        w(f"| {k} " + _row(STEPS[k], n(d["step"] == k)))
    w("| " + _row("Sentiment set (Track A)", n(d["sentiment_set"]), True))
    for k in (11, 12, 13):
        w(f"| {k} " + _row(STEPS[k], n(d["step"] == k)))
    for name in EXCLUDE_NAMES.values():
        w("| 14 " + _row(name, n((d["step"] == 14) & (d["disposition"] == name))))
    w(f"| 15 " + _row(STEPS[15], n(d["step"] == 15)))
    w("| " + _row("Sighting posts", n(d["sighting_post"]), True))
    w(f"| 16 " + _row(STEPS[16], n(d["step"] == 16)))
    w("| " + _row("Sightings (Track B), one per state-week", n(d["step"] == 0), True))
    w("")

    w("## Step rules for the label steps\n")
    w("| # | Rule |")
    w("|---|---|")
    w("| 9 | The reader's `relevant` is no |")
    w("| 10 | The reader's `place_state` is `US`, `FOREIGN` or `NONE` |")
    w("| 11 | `illness` is no, or `local_place` is no |")
    w("| 12 | Content class `domestic_detection`: the outcome itself, or its dairy analogue |")
    w("| 13 | Content class `other` |")
    w("| 14 | The reader's `exclude` reason |")
    w("| 15 | Alaska, Hawaii or a territory |")
    w("| 16 | A later post in the same state and week (ending Sunday) as an earlier sighting, per platform |")
    w("")
    w("Labels: every reader-set post was read twice by independent Sonnet readers. Where the "
      "reads differed on `relevant`, `content_class`, `place_state` or sighting status, an Opus "
      "adjudicator's row is used; otherwise read A's row (the reads agree on those fields).\n")
    w("| Label source | Meta | X |")
    w("|---|---:|---:|")
    w(_row("Two reads agree on the analysis fields", n(d["label_source"] == "reads")))
    w(_row("Adjudicated", n(d["label_source"] == "adjudicated")))
    w("")

    ss = d[d["sentiment_set"]]
    w("## The sentiment set\n")
    w("| | Meta | X |")
    w("|---|---:|---:|")
    w(_row("Posts", n(d["sentiment_set"])))
    for per in ("2022-24", "2025+"):
        w(_row(f"dated {per}", n(d["sentiment_set"] & (d["period"] == per))))
    for lg in ("en", "es"):
        w(_row(f"language {lg}", n(d["sentiment_set"] & (d["lang_detected"] == lg))))
    w(_row("in the 48 contiguous states or DC", n(d["sentiment_set"] & ~d["state"].isin(NOT_CONTIGUOUS))))
    w(_row("reader state differs from the geocoder's state", n(d["state_moved"])))
    for c in ("domestic_detection", "wildlife_report", "first_hand", "other"):
        w(_row(f"class `{c}`", n(d["sentiment_set"] & (d["content_class"] == c))))
    w("")
    w("The sentiment set keeps posts about Alaska, Hawaii and the territories; the state-week "
      "grid (48 states and DC) does not use them. A post whose reader state differs from the "
      "geocoder's state is placed in the reader's state.\n")
    share = (ss["state"] != ss["geo_state"]).groupby(ss["platform"]).mean()
    w("Geocoder check on the sentiment set: the reader's state equals the geocoder's state for "
      + " and ".join(f"{1 - share[q]:.1%} of {'Meta' if q == 'meta' else 'X'} posts" for q in plats)
      + ". The geocoder is loose about *whether* a post is about one state: step 10 removes "
      + " and ".join(f"{(d[d.platform == q]['step'] == 10).sum() / (d[d.platform == q]['step'] != 9).sum():.1%}"
                     f" of relevant {'Meta' if q == 'meta' else 'X'} posts" for q in plats) + ".\n")

    sg = d[d["step"] == 0]
    w("## The sightings\n")
    w("| | Meta | X |")
    w("|---|---:|---:|")
    w(_row("Sighting posts (before step 16)", n(d["sighting_post"])))
    w(_row("Sightings: sighted state-weeks", n(d["step"] == 0)))
    for per in ("2022-24", "2025+"):
        w(_row(f"sighted state-weeks, {per}", n((d["step"] == 0) & (d["period"] == per))))
    for c in ("wildlife_report", "first_hand"):
        w(_row(f"sighting posts of class `{c}`", n(d["sighting_post"] & (d["content_class"] == c))))
    for lab_, m in (("1 sighting post", sg["n_sighting_posts"] == 1),
                    ("2-3 sighting posts", sg["n_sighting_posts"].between(2, 3)),
                    ("4 or more sighting posts", sg["n_sighting_posts"] >= 4),
                    ("1 distinct named place", sg["n_places"] == 1),
                    ("2 distinct named places", sg["n_places"] == 2),
                    ("3 or more distinct named places", sg["n_places"] >= 3)):
        w(_row(f"state-weeks with {lab_}", [int((m & (sg["platform"] == q)).sum()) for q in plats]))
    w(_row("distinct states with a sighting", [sg[sg.platform == q]["state"].nunique() for q in plats]))
    w("")
    w("A sighting's class is the class of the first sighting post in its state-week. Distinct "
      "named places are counted from the readers' free-text `place_name` after lower-casing and "
      "removing punctuation, so two spellings of one place count twice.\n")
    w("## Labels after the X re-pull\n")
    w("The English X posts of 2022-06 to 2024-12 were first collected by an earlier scraper and "
      "were replaced on 2026-11-18 by a pull with the same collector and queries as the later "
      "period. Labels are keyed by post id. A post keeps its label when it returns with the "
      "same wording (the earlier export had lost commas and line breaks, so wording is compared "
      "on letters and digits only, without links); every other reader-set post was read twice "
      "after the re-pull. On 2026-11-19 the collector re-ran the query-days that had returned "
      "nothing or had been cut short; posts that entered the reader set then were read twice in "
      "the same way.\n")
    w("| Label origin | Meta | X |")
    w("|---|---:|---:|")
    wv = d[["platform", "post_id"]].merge(pd.read_parquet(MERGED, columns=["platform", "post_id", "wave"]),
                                          on=["platform", "post_id"], validate="1:1")["wave"]
    w(_row("Read before the re-pull, label carried", n((~wv.isin(["wave2", "wave3"])).to_numpy())))
    w(_row("Read after the re-pull", n((wv == "wave2").to_numpy())))
    w(_row("Read after the repair of missing query-days", n((wv == "wave3").to_numpy())))
    w("")
    DOC.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {DOC}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["merge", "build", "report"])
    {"merge": merge, "build": build, "report": report}[ap.parse_args().step]()
