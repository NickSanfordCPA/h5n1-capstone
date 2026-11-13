"""The X data funnel (docs/x_data_funnel.md) applied to the Meta (Facebook) posts, for the Meta
utility analysis (docs/meta_utility_spec.md). Output: the table meta_post_funnel, one row per
post in Max's geocoded set, with an X-style disposition, use_sentiment and use_sightings.

    uv run python -m h5n1.sources.meta_funnel build        # steps 2, 3, 7 and author volumes
    uv run python -m h5n1.sources.meta_funnel namebatch    # authors that need an LLM account type
    (readers answer name_labels_<n>.csv: owner_id, account_type)
    uv run python -m h5n1.sources.meta_funnel accounts     # account types + hub rule (step 6)
    uv run python -m h5n1.sources.meta_funnel subset       # filtered subset + batch_<n>.jsonl
    (readers answer labels_<n>.csv: _id, local_obs, illness_seen)
    uv run python -m h5n1.sources.meta_funnel merge
    uv run python -m h5n1.sources.meta_funnel exclbatch
    (one reader per stream answers excl_labels_<stream>.csv: _id, exclude, event_state)
    uv run python -m h5n1.sources.meta_funnel table        # dispositions -> meta_post_funnel.parquet
    DB_PORT=5433 uv run python -m h5n1.sources.meta_funnel load

INPUT. The funnel starts from Max's 30,369 geocoded Meta posts (meta_post_analysis WHERE
use_sentiment): Max's own steps (dedup, geocoding from the post text, his foreign and
multi-state, keyword, copypasta and hashtag-farm cuts) come first and are taken as given.
Max's build code is not in the repo. Every other Meta post is still read, but only to form
copypasta groups and author volumes, which X also computed over all of a stream's posts.

DEPARTURES FROM X, all forced by the source:
  streams      en and es, split on `lang`. One collector throughout, so there is no collector
               break and the hub window is pre-2025 for both streams.
  step 3       Meta has no quote posts; reshares (content_type 'reshare' or a shared_post_id)
               are its analog, a comment on someone else's post, and are dropped.
  steps 4-5    the state is where the post's TEXT names a place (Max's geocoder), not where the
               author lives: Meta gives no author location. Already applied upstream.
  step 6       authors are Facebook Pages and profiles with no bio, so the Appendix A labeller
               reads the name, username and Page/profile type. The regex fallback reads the name.
  steps 8-9    guide v2 (data/Meta_derived/labels/GUIDE_local_obs.md, Nick 2026-11-11): Facebook
               Pages relay agency announcements, and an announced poultry-flock detection is the
               outcome itself, so it is never a local observation (poultry_announcement records
               it). Wild-bird and mammal die-offs at a named local place still count, as in X.
               The first wave under the unmodified X guide split on this point (7-24% sick or
               dead per batch) and was relabelled; it is kept in labels/wave1_xguide/.
  steps 10-14  the exclusion reader is given the post's geocoded state where X gave the author's
               profile state; "secondhand" means relayed from somewhere other than that place
               with no local link, as in X.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

import pandas as pd

from h5n1.sources.x_analysis_table import copypasta_key
from h5n1.sources.x_location import FIPS_STATE
from h5n1.sources.x_posts import ACCOUNT_TYPES, COPYPASTA_MIN, FARM, FARM_ES, INST, MEDIA_FALLBACK

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "Meta_derived"
LAB = OUT / "labels"
STREAMS = ["en", "es"]
START, HUB_END = "2022-06-01", "2025-01-01"
BATCH = 500
SEED = 20261111
EXCLUDE_REASONS = {"", "outside_us", "retrospective", "not_hpai", "secondhand", "not_credible"}
STEPS = {  # disposition: step number, matching docs/x_data_funnel.md where the step exists
    "Outside date window": 2, "Reshare": 3, "Hub author": 6, "Copypasta repeat": 7,
    "Not a local observation": 8, "No sick or dead animals": 9, "Retrospective": 10,
    "Outside the US": 11, "Not HPAI": 12, "Secondhand": 13, "Not credible": 14,
    "Same state-week": 15, "Sighting": 16}
REASON_STEP = {"retrospective": "Retrospective", "outside_us": "Outside the US", "not_hpai": "Not HPAI",
               "secondhand": "Secondhand", "not_credible": "Not credible"}


def _engine():
    from h5n1.db import get_engine
    return get_engine()


def load() -> pd.DataFrame:
    """Every unique Meta post, with Max's geocode for the ones in his geocoded set."""
    p = pd.read_sql("""
        SELECT u.post_id AS _id, u.creation_time AS ts, u.lang AS stream, u.content_type, u.shared_post_id,
               u.owner_id, u.owner_name, u.owner_username, u.owner_type, u.post_text AS text,
               u.search_terms, a.use_sentiment AS geocoded, a.geo_tier, a.geo_matched, a.state, a.county_fips
          FROM meta_post_unique u JOIN meta_post_analysis a USING (post_id)""", _engine())
    p["ts"] = pd.to_datetime(p.ts, utc=True)
    p["search_terms"] = p.search_terms.apply(lambda v: "|".join(v) if isinstance(v, (list, tuple)) else v)
    p["text"] = p.text.fillna("")
    p["owner_id"] = p.owner_id.fillna("unknown")
    assert set(p.stream) <= set(STREAMS), set(p.stream)
    assert p.loc[p.geocoded, "state"].notna().all()
    return p


def build() -> None:
    p = load()
    p["in_window"] = p.ts >= pd.Timestamp(START, tz="UTC")
    p["reshare"] = p.content_type.eq("reshare") | p.shared_post_id.notna()
    keep = p.in_window & ~p.reshare
    parts, authors = [], []
    for s in STREAMS:
        q = p[keep & (p.stream == s)].copy()
        key = copypasta_key(q.text)
        g = q.assign(key=key).groupby("key")
        n_posts, n_auth = g._id.transform("size"), g.owner_id.transform("nunique")
        q["copypasta"] = (n_posts >= COPYPASTA_MIN) & (n_auth >= 2) & (key.str.len() >= 20)
        first = q.assign(key=key).sort_values(["ts", "_id"], kind="stable").drop_duplicates("key")._id
        q["copypasta_first"] = q.copypasta & q._id.isin(first)
        a = (q.sort_values("ts").groupby("owner_id")
             .agg(owner_name=("owner_name", "last"), owner_username=("owner_username", "last"),
                  owner_type=("owner_type", "last"), n_posts=("_id", "size")))
        a["n_posts_train"] = q[q.ts < pd.Timestamp(HUB_END, tz="UTC")].groupby("owner_id").size().reindex(
            a.index).fillna(0).astype(int)
        a["volume_pctile"] = a.n_posts_train.rank(pct=True)
        a["in_geocoded"] = a.index.isin(q.loc[q.geocoded, "owner_id"])
        authors.append(a.reset_index().assign(stream=s))
        parts.append(q)
    q = pd.concat(parts)
    p = p.merge(q[["_id", "copypasta", "copypasta_first"]], on="_id", how="left")
    OUT.mkdir(parents=True, exist_ok=True)
    p.to_parquet(OUT / "posts.parquet", index=False)
    pd.concat(authors).to_parquet(OUT / "authors.parquet", index=False)
    g = p[p.geocoded]
    print(f"posts {len(p):,}; geocoded {len(g):,}: before {START} {(~g.in_window).sum():,}, "
          f"reshares {(g.in_window & g.reshare).sum():,}, copypasta repeats "
          f"{(g.copypasta.fillna(False) & ~g.copypasta_first.fillna(False)).sum():,}")


def _name_text(a: pd.DataFrame) -> pd.Series:
    return (a.owner_name.fillna("") + " " + a.owner_username.fillna("")).str.lower()


def namebatch(k: int = 4) -> None:
    """Authors with a post in the geocoded set whose account type decides the hub rule or the
    farm exemption: top 1% by hub-window volume, or a farm/ag word in the name."""
    a = pd.read_parquet(OUT / "authors.parquet")
    nm = _name_text(a)
    ag = nm.str.contains(FARM) | nm.str.contains(INST) | ((a.stream == "es") & nm.str.contains(FARM_ES))
    need = a[a.in_geocoded & ((a.volume_pctile >= 0.99) | ag)].drop_duplicates("owner_id")
    need = need.sample(frac=1, random_state=SEED)
    LAB.mkdir(parents=True, exist_ok=True)
    for i in range(k):
        with open(LAB / f"name_batch_{i}.jsonl", "w", encoding="utf-8") as fh:
            for r in need.iloc[i::k].itertuples(index=False):
                fh.write(json.dumps({"owner_id": r.owner_id, "name": r.owner_name, "username": r.owner_username,
                                     "type": r.owner_type}, ensure_ascii=False) + "\n")
    print(f"{len(need):,} authors to label in {k} batches")


def accounts() -> None:
    a = pd.read_parquet(OUT / "authors.parquet")
    files = sorted(LAB.glob("name_labels_*.csv"))
    lab = pd.concat(pd.read_csv(f, dtype=str) for f in files) if files else pd.DataFrame(
        columns=["owner_id", "account_type"])
    lab["account_type"] = lab.account_type.str.strip()
    if (~lab.account_type.isin(ACCOUNT_TYPES)).any():
        raise ValueError(f"unknown account types: {set(lab.account_type) - set(ACCOUNT_TYPES)}")
    lab = lab.drop_duplicates("owner_id").set_index("owner_id").account_type
    fallback = _name_text(a).str.contains(MEDIA_FALLBACK).map({True: "media_aggregator", False: "general_public"})
    a["type_source"] = a.owner_id.isin(lab.index).map({True: "llm_name", False: "regex"})
    a["account_type"] = a.owner_id.map(lab).fillna(fallback)
    a["hub"] = (a.volume_pctile >= 0.99) & ~a.account_type.isin(["farmer_keeper", "ag_institution"])
    unlabelled = a.in_geocoded & (a.volume_pctile >= 0.99) & (a.type_source == "regex")
    if unlabelled.any():
        raise ValueError(f"{unlabelled.sum()} top-1% authors have no LLM label; run namebatch")
    a.to_parquet(OUT / "authors.parquet", index=False)
    print(a[a.in_geocoded].groupby(["account_type", "type_source"]).size().unstack(fill_value=0))
    print(f"hubs: {a.hub.sum()} author-streams")


def _posts() -> pd.DataFrame:
    p = pd.read_parquet(OUT / "posts.parquet").query("geocoded")
    a = pd.read_parquet(OUT / "authors.parquet", columns=["owner_id", "stream", "account_type", "hub"])
    return p.merge(a, on=["owner_id", "stream"], how="left")


def _subset_mask(p: pd.DataFrame) -> pd.Series:
    return p.in_window & ~p.reshare & ~p.hub.fillna(False).astype(bool) & ~(
        p.copypasta.fillna(False).astype(bool) & ~p.copypasta_first.fillna(False).astype(bool))


def subset() -> None:
    """The filtered subset, shuffled into batches of BATCH. The reader sees only the text."""
    p = _posts()
    sub = p[_subset_mask(p)].sample(frac=1, random_state=SEED)
    sub.to_parquet(OUT / "topic_subset.parquet", index=False)
    for f in LAB.glob("batch_*.jsonl"):
        f.unlink()
    n = 0
    for n, start in enumerate(range(0, len(sub), BATCH), 1):
        with open(LAB / f"batch_{n - 1}.jsonl", "w", encoding="utf-8") as fh:
            for r in sub.iloc[start:start + BATCH].itertuples(index=False):
                fh.write(json.dumps({"_id": r._0, "text": re.sub(r"\s+", " ", r.text)[:600]},
                                    ensure_ascii=False) + "\n")
    print(f"subset {len(sub):,} posts ({sub.stream.value_counts().to_dict()}) in {n} batches")


def merge() -> None:
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id"])
    files = [f for f in LAB.glob("labels_*.csv") if re.fullmatch(r"labels_\d+\.csv", f.name)]
    lab = pd.concat(pd.read_csv(f, dtype=str).assign(batch=int(f.stem.split("_")[1])) for f in files)
    cols = ["local_obs", "illness_seen", "poultry_announcement"]
    for c in cols:
        lab[c] = lab[c].str.strip().str.lower().map({"yes": True, "no": False})
    if lab[cols].isna().any().any() or lab._id.duplicated().any():
        raise ValueError("labels malformed or duplicated")
    missing = set(sub._id) - set(lab._id)
    if missing:
        raise ValueError(f"{len(missing):,} subset posts have no label")
    # guide v2: a poultry-detection announcement is the outcome itself, never a local observation
    lab["local_obs"] &= ~lab.poultry_announcement
    lab["illness_seen"] &= lab.local_obs
    lab = lab[lab._id.isin(sub._id)]
    lab[["_id", *cols, "batch"]].to_parquet(OUT / "labels_all.parquet", index=False)
    print(f"{len(lab):,} posts: {lab.local_obs.sum():,} local_obs, {lab.illness_seen.sum():,} illness_seen, "
          f"{lab.poultry_announcement.sum():,} poultry announcements")


EXCL_CHUNK = 400


def exclbatch() -> None:
    """Appendix C input: text, the post's geocoded state and the date, in date order. X gave each
    stream to one reader; Meta's English set is too large for one context, so it is cut into
    contiguous date-ordered chunks of about EXCL_CHUNK, each reader working from the same guide."""
    lo = pd.read_parquet(OUT / "labels_all.parquet").query("illness_seen")
    sub = pd.read_parquet(OUT / "topic_subset.parquet", columns=["_id", "text", "state", "ts", "stream"])
    ev = lo[["_id"]].merge(sub, on="_id").sort_values(["ts", "_id"])
    for f in LAB.glob("excl_batch_*.jsonl"):
        f.unlink()
    for s, g in ev.groupby("stream"):
        n = max(1, round(len(g) / EXCL_CHUNK))
        for i in range(n):
            part = g.iloc[i * len(g) // n:(i + 1) * len(g) // n]
            with open(LAB / f"excl_batch_{s}_{i}.jsonl", "w", encoding="utf-8") as fh:
                for r in part.itertuples(index=False):
                    fh.write(json.dumps({"_id": r._0, "post_state": r.state, "date": f"{r.ts:%Y-%m-%d}",
                                         "text": re.sub(r"\s+", " ", r.text)[:1000]}, ensure_ascii=False) + "\n")
            print(f"excl_batch_{s}_{i}: {len(part)} posts")
    print(f"{len(ev):,} illness_seen posts: {ev.stream.value_counts().to_dict()}")


def _exclusions() -> pd.DataFrame:
    files = sorted(LAB.glob("excl_labels_*.csv"))
    if not files:
        return pd.DataFrame(columns=["_id", "exclude", "event_state"])
    lab = pd.concat(pd.read_csv(f, dtype=str, keep_default_na=False) for f in files).drop_duplicates("_id")
    lab["exclude"] = lab.exclude.str.strip().str.lower()
    lab["event_state"] = lab.event_state.str.strip().str.upper()
    bad = ~lab.exclude.isin(EXCLUDE_REASONS) | ~lab.event_state.isin(set(FIPS_STATE.values()) | {""})
    if bad.any():
        raise ValueError(f"exclusion labels malformed: {lab[bad].head().to_dict('records')}")
    return lab[["_id", "exclude", "event_state"]]


CORRECTABLE = {"local_obs", "illness_seen", "poultry_announcement", "exclude", "event_state"}


def _apply_corrections(p: pd.DataFrame) -> pd.DataFrame:
    """labels/corrections.csv (_id, field, value, reason): reviewed overrides of reader answers,
    kept apart so the readers' files stay as written. A poultry announcement or a no to
    illness_seen also clears the answers that depend on it."""
    f = LAB / "corrections.csv"
    if not f.exists():
        return p
    c = pd.read_csv(f, dtype=str, keep_default_na=False)
    bad = ~c.field.isin(CORRECTABLE) | ~c._id.isin(p._id) | c.reason.str.strip().eq("")
    if bad.any():
        raise ValueError(f"bad corrections: {c[bad].to_dict('records')}")
    p = p.set_index("_id")
    for pid, field, value in zip(c._id, c.field, c.value):
        v = value.strip()
        p.loc[pid, field] = {"yes": True, "no": False}.get(v.lower(), v) if field in (
            "local_obs", "illness_seen", "poultry_announcement") else v
    pa = p.poultry_announcement.eq(True)
    p.loc[pa, "local_obs"] = False
    p.loc[p.local_obs.eq(False), "illness_seen"] = False
    print(f"applied {len(c)} corrections: {c.field.value_counts().to_dict()}")
    return p.reset_index()


def table() -> None:
    """One row per geocoded post with its disposition: the first step that removes it."""
    p = _posts()
    lab = pd.read_parquet(OUT / "labels_all.parquet")[["_id", "local_obs", "illness_seen", "poultry_announcement"]]
    p = p.merge(lab, on="_id", how="left").merge(_exclusions(), on="_id", how="left")
    p = _apply_corrections(p)
    ill = p.illness_seen.fillna(False).astype(bool)
    if (ill & p.exclude.isna()).any():
        raise ValueError(f"{(ill & p.exclude.isna()).sum()} illness_seen posts have no exclusion answer")
    p["event_state"] = p.event_state.where(p.event_state.fillna("") != "", p.state).where(ill)
    p["use_sentiment"] = _subset_mask(p)
    d = pd.Series(pd.NA, index=p.index, dtype="object")

    def cut(mask, name):
        d.loc[mask & d.isna()] = name

    cut(~p.in_window, "Outside date window")
    cut(p.reshare, "Reshare")
    cut(p.hub.fillna(False).astype(bool), "Hub author")
    cut(~p.use_sentiment, "Copypasta repeat")
    cut(p.local_obs.eq(False), "Not a local observation")
    cut(p.illness_seen.eq(False), "No sick or dead animals")
    for reason, name in REASON_STEP.items():
        cut(p.exclude.eq(reason), name)
    event = d.isna()
    week = p.ts.dt.tz_convert(None).dt.to_period("W")
    first = (p[event].assign(week=week[event]).sort_values(["ts", "_id"], kind="stable")
             .drop_duplicates(["stream", "event_state", "week"])._id)
    cut(event & ~p._id.isin(first), "Same state-week")
    cut(event, "Sighting")
    p["disposition"] = d
    p["disposition_step"] = d.map(STEPS)
    p["use_sightings"] = d.eq("Sighting")
    p["sighting_week"] = week.dt.start_time.dt.date.where(p.use_sightings)
    cols = ["_id", "stream", "ts", "content_type", "owner_id", "owner_name", "owner_type", "account_type", "hub",
            "search_terms", "text", "geo_tier", "geo_matched", "state", "county_fips", "copypasta",
            "copypasta_first", "local_obs", "illness_seen", "poultry_announcement", "exclude", "event_state", "sighting_week",
            "disposition_step", "disposition", "use_sentiment", "use_sightings"]
    out = p[cols].rename(columns={"_id": "post_id", "ts": "created_at", "text": "post_text",
                                  "exclude": "exclusion_reason"})
    out.to_parquet(OUT / "meta_post_funnel.parquet", index=False)
    tab = out.groupby(["disposition_step", "disposition", "stream"]).size().unstack(fill_value=0)
    print(tab.to_string())
    print(f"use_sentiment {out.use_sentiment.sum():,}; use_sightings {out.use_sightings.sum():,}")


def load_db() -> None:
    """Replace meta_post_funnel with the parquet. Additive to the schema; Max's tables untouched."""
    from sqlalchemy import text as sql

    df = pd.read_parquet(OUT / "meta_post_funnel.parquet")
    eng = _engine()
    with eng.begin() as c:
        c.execute(sql("DROP TABLE IF EXISTS meta_post_funnel"))
    df.to_sql("meta_post_funnel", eng, index=False, chunksize=2000, method="multi")
    with eng.begin() as c:
        c.execute(sql("ALTER TABLE meta_post_funnel ADD PRIMARY KEY (post_id)"))
        c.execute(sql("COMMENT ON TABLE meta_post_funnel IS 'X funnel (docs/x_data_funnel.md) applied to "
                      "Max''s geocoded Meta posts; built by h5n1/sources/meta_funnel.py. Holds the LLM labels.'"))
    print(f"loaded {len(df):,} rows")


# ---- readers for the analysis, same shapes as x_analysis_table.sentiment_posts / sightings ----

def sentiment_posts(streams: list[str] | None = None) -> pd.DataFrame:
    """(_id, state, ts, account_type, sentiment, is_negative) for the Meta filtered subset, with
    Max's meta_post_sentiment scores. Unscored posts are left out, never counted as 0."""
    q = """SELECT f.post_id AS _id, f.state, f.created_at AS ts, f.account_type, f.stream,
                  s.positive - s.negative AS sentiment, s.negative >= GREATEST(s.neutral, s.positive) AS is_negative
             FROM meta_post_funnel f JOIN meta_post_sentiment s USING (post_id)
            WHERE f.use_sentiment"""
    p = pd.read_sql(q, _engine())
    p["ts"] = pd.to_datetime(p.ts, utc=True)
    return p[p.stream.isin(streams or STREAMS)].drop(columns="stream").reset_index(drop=True)


def sightings(streams: list[str] | None = None) -> pd.DataFrame:
    """(_id, state, ts) for the Meta Sighting rows; state is the event state. Pooling streams
    re-applies one per state-week (earliest, ties by smallest post ID)."""
    ev = pd.read_sql("SELECT post_id AS _id, event_state AS state, created_at AS ts, stream "
                     "FROM meta_post_funnel WHERE use_sightings", _engine())
    ev["ts"] = pd.to_datetime(ev.ts, utc=True)
    ev = ev[ev.stream.isin(streams or STREAMS)].drop(columns="stream")
    week = ev.ts.dt.tz_convert(None).dt.to_period("W")
    return ev.assign(week=week).sort_values(["ts", "_id"], kind="stable").drop_duplicates(
        ["state", "week"]).drop(columns="week").reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "namebatch", "accounts", "subset", "merge", "exclbatch", "table",
                                     "load"])
    a = ap.parse_args()
    {"build": build, "namebatch": namebatch, "accounts": accounts, "subset": subset, "merge": merge,
     "exclbatch": exclbatch, "table": table, "load": load_db}[a.step]()
