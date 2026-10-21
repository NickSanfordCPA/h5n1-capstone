"""x_post_analysis: every collected X row (x_post) with its funnel disposition, location,
sentiment and labels -- the one X table the analysis reads.

    uv run python -m h5n1.sources.x_analysis_table build   # -> data/X_derived/x_post_analysis.parquet
    uv run python -m h5n1.sources.x_analysis_table load    # -> Postgres x_post_analysis

One row per x_post row (603,265). Each row gets the FIRST funnel step that removes it, named
exactly as in docs/x_data_funnel.md, or "Sighting" if it survives every step. The steps are
recomputed here from x_post and the stream rules (geocoder, word lists, hub rule, copypasta
key). LLM decisions are read back from this table itself, the labels' record of truth: a
rebuild needs only the database, the gs://h5n1-raw/X/ CSVs and each stream's x_posts
outputs. The label files under labels/full_review/ seed the first build, and *_extra*.csv
there add readings of posts the table doesn't have yet (see h5n1/sources/x_topics.py).

DETERMINISTIC TIE-BREAKS (every "keep one, drop the rest" step):
  1  Duplicate search result  rows of one collector sharing a post ID: keep the row whose search
                              term sorts first; repeat rows of one search term (fr only, some
                              with follower counts from a later fetch) are numbered by row_seq
                              in md5(raw) order, keep 1.
                              For the prior stream this is the row its CSV build kept.
  7  Copypasta repeat         keep the earliest post of the group, ties by smallest post ID.
  15 Same state-week          keep the earliest sighting post, ties by smallest post ID.
Steps 7 and 15 are applied within each stream, as the pipeline does.

use_sentiment = the row is in its stream's filtered subset (survived steps 1-7).
use_sightings = disposition is "Sighting".

Analysis code reads the table through sightings() and sentiment_posts() below.
"""
from __future__ import annotations

import argparse
import io
import re

import numpy as np
import pandas as pd

from h5n1.sources.x_location import geocode_series
from h5n1.sources.x_posts import COPYPASTA_MIN, FOREIGN_BY_STREAM, FR_US, STREAMS, XD

TABLE = "x_post_analysis"
OUT = XD / "x_post_analysis.parquet"
CORPUS_STREAM = {"prev_cohort_en": "prior", "twitterapi_en": "en_test", "twitterapi_es": "es",
                 "twitterapi_fr": "fr"}
STEPS = {
    1: "Duplicate search result", 2: "Outside date window", 3: "Quote post", 4: "No US state",
    5: "Foreign place name", 6: "Hub author", 7: "Copypasta repeat", 8: "Not a local observation",
    9: "No sick or dead animals", 10: "Retrospective", 11: "Outside the US", 12: "Not HPAI",
    13: "Secondhand", 14: "Not credible", 15: "Same state-week", 16: "Sighting",
}
EXCLUDE_STEP = {"retrospective": 10, "outside_us": 11, "not_hpai": 12, "secondhand": 13,
                "not_credible": 14}
COLS = ["corpus", "post_id", "source_query", "row_seq", "stream", "search_terms", "created_at",
        "post_type", "lang", "post_text", "url", "user_id", "user_name", "alias", "user_location",
        "followers", "verified", "retweets", "favorites", "replies", "impressions",
        "account_type", "account_type_source", "hub_author", "geo_level", "state", "county_fips",
        "copypasta", "copypasta_first", "negative_score", "neutral_score", "positive_score",
        "sentiment", "local_obs", "illness_seen", "exclusion_reason", "event_state",
        "sighting_week", "disposition_step", "disposition", "use_sentiment", "use_sightings"]


def _read(where: str, cols: str, streams: list[str]) -> pd.DataFrame:
    from sqlalchemy import bindparam, text

    from h5n1.db import get_engine

    q = text(f"SELECT {cols} FROM {TABLE} WHERE {where} AND stream IN :streams").bindparams(
        bindparam("streams", expanding=True))
    df = pd.read_sql(q, get_engine(), params={"streams": list(streams)})
    df["ts"] = pd.to_datetime(df.ts, utc=True)
    return df


def sightings(streams: list[str]) -> pd.DataFrame:
    """(_id, state, ts) for the Sighting rows of the given streams; state is the event state.
    Each stream is already one per state-week; pooling streams re-applies the rule (earliest,
    ties by smallest post ID), which matters only for a week straddling 2025-01-01."""
    ev = _read("use_sightings", "post_id AS _id, event_state AS state, created_at AS ts", streams)
    week = ev.ts.dt.tz_convert(None).dt.to_period("W")
    return ev.assign(week=week).sort_values(["ts", "_id"], kind="stable").drop_duplicates(
        ["state", "week"]).drop(columns="week").reset_index(drop=True)


def sentiment_posts(streams: list[str]) -> pd.DataFrame:
    """(_id, state, ts, account_type, sentiment) for the filtered subset of the given streams.
    Unscored posts are left out, never counted as 0 (every subset post is scored as of 2026-10-19)."""
    return _read("use_sentiment AND sentiment IS NOT NULL",
                 "post_id AS _id, state, created_at AS ts, account_type, sentiment", streams)


def copypasta_key(text: pd.Series) -> pd.Series:
    # same normalisation as x_posts.build
    return (text.str.lower().str.replace(r"https?://\S+|@\w+|\d+", "", regex=True)
            .str.replace(r"\W+", " ", regex=True).str.strip().str[:80])


def load_rows() -> pd.DataFrame:
    from h5n1.db import get_engine

    rows = pd.read_sql("""
        SELECT corpus, post_id, source_query,
               row_number() OVER (PARTITION BY corpus, post_id, source_query
                                  ORDER BY md5(raw::text)) AS row_seq,
               created_at, post_type, lang, post_text, url, user_id, user_name, alias, user_location,
               followers, verified, retweets, favorites, replies, impressions
          FROM x_post""", get_engine())
    rows["created_at"] = pd.to_datetime(rows.created_at, utc=True)
    rows["stream"] = rows.corpus.map(CORPUS_STREAM)
    if rows.stream.isna().any():
        raise ValueError(f"unknown corpus: {rows.corpus[rows.stream.isna()].unique()}")
    return rows


def locate(rows: pd.DataFrame) -> pd.DataFrame:
    """Geocode each row's own profile location with its stream's rules; flag step 5."""
    loc = rows.user_location.fillna("")
    fr = rows.stream == "fr"
    for pat, en in FR_US:
        loc = loc.mask(fr, loc.str.replace(pat, en, regex=True, flags=re.I))
    geo = geocode_series(loc)
    foreign = pd.Series(False, index=rows.index)
    for stream, pat in FOREIGN_BY_STREAM.items():
        s = rows.stream == stream
        foreign |= s & rows.user_location.fillna("").str.contains(pat)
    rows = rows.join(geo)
    rows["foreign_name"] = foreign & rows.state.notna()
    rows.loc[foreign, ["state", "county_fips"]] = None
    return rows


def table_labels(stream: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The stream's LLM labels as stored in x_post_analysis, shaped like the label files.
    The table is the labels' record of truth: the readings can't be reproduced exactly."""
    from sqlalchemy import inspect, text

    from h5n1.db import get_engine

    eng = get_engine()
    if not inspect(eng).has_table(TABLE):
        return pd.DataFrame(), pd.DataFrame()
    t = pd.read_sql(text(f"SELECT DISTINCT post_id AS _id, local_obs, illness_seen, exclusion_reason, "
                         f"event_state FROM {TABLE} WHERE stream = :s AND local_obs IS NOT NULL"),
                    eng, params={"s": stream})
    if t._id.duplicated().any():
        raise ValueError(f"{stream}: a post has two different labels in {TABLE}")
    ex = t[t.illness_seen].assign(exclude=lambda d: d.exclusion_reason.fillna(""))
    return t[["_id", "local_obs", "illness_seen"]], ex[["_id", "exclude", "event_state"]]


def labels(stream: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Labels from x_post_analysis; from the stream's label files only when the table has none
    (the first build). Either way, *_extra*.csv files add readings of posts not yet in it."""
    fr = STREAMS[stream]["out"] / "labels" / "full_review"
    lab, ex = table_labels(stream)
    if lab.empty:
        lab = pd.read_parquet(fr / "labels_all.parquet")[["_id", "local_obs", "illness_seen"]]
        ex_path = fr / "illness_posts_screened.parquet"
        ex = (pd.read_parquet(ex_path)[["_id", "exclude", "event_state"]] if ex_path.exists()
              else pd.DataFrame(columns=["_id", "exclude", "event_state"]))
    extra = sorted(fr.glob("labels_extra*.csv"))
    if extra:
        e = pd.concat(pd.read_csv(f, dtype=str) for f in extra)
        for c in ("local_obs", "illness_seen"):
            e[c] = e[c].str.strip().str.lower().map({"yes": True, "no": False})
        lab = pd.concat([lab, e[lab.columns]]).drop_duplicates("_id", keep="last")
    extra = sorted(fr.glob("excl_labels_extra*.csv"))
    if extra:
        e = pd.concat(pd.read_csv(f, dtype=str, keep_default_na=False) for f in extra)
        ex = pd.concat([ex, e[["_id", "exclude", "event_state"]]]).drop_duplicates("_id", keep="last")
    return lab.rename(columns={"_id": "post_id"}), ex.rename(columns={"_id": "post_id"})


def build() -> None:
    from h5n1.db import get_engine

    rows = locate(load_rows())
    rows["disposition_step"] = 16

    def drop(mask: pd.Series, step: int) -> None:
        rows.loc[mask & (rows.disposition_step == 16), "disposition_step"] = step

    # 1: keep the first search term per (corpus, post_id); terms are listed on every row
    rows = rows.sort_values(["corpus", "post_id", "source_query", "row_seq"], kind="stable")
    drop(rows.duplicated(["corpus", "post_id"]), 1)
    terms = rows.groupby(["corpus", "post_id"]).source_query.agg(lambda t: "|".join(sorted(set(t))))
    rows = rows.merge(terms.rename("search_terms"), on=["corpus", "post_id"])
    # 2, 3
    start = rows.stream.map({s: STREAMS[s]["start"] or "1900-01-01" for s in STREAMS})
    drop(rows.created_at < pd.to_datetime(start, utc=True), 2)
    drop(rows.post_type == "quote", 3)

    sent = pd.read_sql("SELECT post_id, negative_score, neutral_score, positive_score "
                       "FROM x_post_sentiment", get_engine())
    if sent.post_id.duplicated().any():
        raise ValueError("x_post_sentiment has duplicate post_ids")
    rows = rows.merge(sent, on="post_id", how="left")
    rows["sentiment"] = rows.positive_score - rows.negative_score

    parts, missing, missing_ex = [], [], []
    for stream, spec in STREAMS.items():
        r = rows[rows.stream == stream].copy()
        posts = pd.read_parquet(spec["out"] / "posts.parquet",
                                columns=["_id", "text", "state", "county_fips", "copypasta"])
        authors = pd.read_parquet(spec["out"] / "authors.parquet",
                                  columns=["userId", "account_type", "type_source", "hub"])
        live = r.disposition_step == 16  # one row per post, inside the stream's window
        # the stream's posts.parquet is the pipeline's own build: geocode must match it
        chk = r[live].merge(posts, left_on="post_id", right_on="_id", how="outer", suffixes=("", "_pq"),
                            indicator=True)
        if (chk._merge != "both").any():
            raise ValueError(f"{stream}: rows and posts.parquet disagree on which posts exist")
        for c in ("state", "county_fips"):
            if not chk[c].fillna("").eq(chk[f"{c}_pq"].fillna("")).all():
                raise ValueError(f"{stream}: {c} differs from posts.parquet")
        r = r.merge(authors.rename(columns={"userId": "user_id", "type_source": "account_type_source",
                                            "hub": "hub_author"}), on="user_id", how="left")
        live = r.disposition_step == 16
        # copypasta over the stream's posts (steps 1-3 survivors), as x_posts.build does. The key
        # uses the pipeline's text: the prior CSV export dropped line breaks that x_post keeps,
        # and "word\nword" vs "wordword" fingerprints differently.
        key = copypasta_key(r.post_id.map(posts.set_index("_id").text).fillna(""))
        g = r[live].assign(key=key[live]).groupby("key")
        n_posts, n_auth = g.post_id.transform("size"), g.user_id.transform("nunique")
        r["copypasta"] = False
        r.loc[live, "copypasta"] = (n_posts >= COPYPASTA_MIN) & (n_auth >= 2) & (key[live].str.len() >= 20)
        if not r[live].merge(posts, left_on="post_id", right_on="_id", suffixes=("", "_pq")).eval(
                "copypasta == copypasta_pq").all():
            raise ValueError(f"{stream}: copypasta flag differs from posts.parquet")
        cp = r[live & r.copypasta].assign(key=key)
        first = cp.sort_values(["created_at", "post_id"], kind="stable").drop_duplicates("key").post_id
        r["copypasta_first"] = r.copypasta & r.post_id.isin(first) & live

        r.loc[live & r.state.isna() & ~r.foreign_name, "disposition_step"] = 4
        r.loc[live & r.foreign_name, "disposition_step"] = 5
        live = r.disposition_step == 16
        r.loc[live & r.hub_author.fillna(False).astype(bool), "disposition_step"] = 6
        live = r.disposition_step == 16
        r.loc[live & r.copypasta & ~r.copypasta_first, "disposition_step"] = 7
        sub = r.disposition_step == 16
        r["use_sentiment"] = sub

        lab, ex = labels(stream)
        r = r.merge(lab, on="post_id", how="left")
        r.loc[~sub, ["local_obs", "illness_seen"]] = np.nan
        missing += [(stream, p, t) for p, t in r.loc[sub & r.local_obs.isna(), ["post_id", "post_text"]].values]
        r.loc[sub & (r.local_obs == False), "disposition_step"] = 8  # noqa: E712
        r.loc[sub & (r.local_obs == True) & (r.illness_seen == False), "disposition_step"] = 9  # noqa: E712
        ill = sub & (r.illness_seen == True)  # noqa: E712
        r = r.merge(ex, on="post_id", how="left")
        r.loc[~ill, ["exclude", "event_state"]] = np.nan
        missing_ex += [(stream, p) for p in r.loc[ill & r.exclude.isna(), "post_id"]]
        r["event_state"] = r.event_state.where(r.event_state.fillna("") != "", r.state).where(ill)
        r["exclusion_reason"] = r.exclude.replace("", np.nan).where(ill)
        r.loc[ill & r.exclusion_reason.notna(), "disposition_step"] = r.exclusion_reason.map(EXCLUDE_STEP)
        ev = r[ill & r.exclusion_reason.isna()]
        week = ev.created_at.dt.tz_convert(None).dt.to_period("W").dt.start_time.dt.date
        r["sighting_week"] = week.reindex(r.index)
        keep = ev.assign(w=week).sort_values(["created_at", "post_id"], kind="stable").drop_duplicates(
            ["event_state", "w"]).post_id
        r.loc[r.index.isin(ev.index) & ~r.post_id.isin(keep), "disposition_step"] = 15
        parts.append(r.drop(columns=["exclude", "foreign_name"]))

    if missing or missing_ex:
        pd.DataFrame(missing, columns=["stream", "post_id", "post_text"]).to_csv(
            XD / "x_post_analysis_unlabelled.csv", index=False)
        raise SystemExit(f"{len(missing)} subset posts have no local_obs label and {len(missing_ex)} "
                         f"illness posts have no exclusion label; see {XD / 'x_post_analysis_unlabelled.csv'}")
    out = pd.concat(parts, ignore_index=True)
    out["disposition_step"] = out.disposition_step.astype(int)
    out["disposition"] = out.disposition_step.map(STEPS)
    out["use_sightings"] = out.disposition_step == 16
    out = out[COLS].sort_values(["corpus", "post_id", "source_query", "row_seq"], ignore_index=True)
    out.to_parquet(OUT, index=False)
    print(f"{len(out):,} rows -> {OUT}")
    print(pd.crosstab([out.disposition_step, out.disposition], out.stream)[list(STREAMS)].to_string())
    print("use_sentiment:", out.groupby("stream").use_sentiment.sum().to_dict(),
          " scored:", out[out.use_sentiment].groupby("stream").sentiment.count().to_dict())


DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    corpus text NOT NULL, post_id text NOT NULL, source_query text NOT NULL, row_seq int NOT NULL,
    stream text NOT NULL, search_terms text, created_at timestamptz, post_type text, lang text,
    post_text text, url text, user_id text, user_name text, alias text, user_location text,
    followers int, verified boolean, retweets int, favorites int, replies int, impressions bigint,
    account_type text, account_type_source text, hub_author boolean,
    geo_level text, state char(2), county_fips char(5),
    copypasta boolean, copypasta_first boolean,
    negative_score double precision, neutral_score double precision, positive_score double precision,
    sentiment double precision,
    local_obs boolean, illness_seen boolean, exclusion_reason text, event_state char(2), sighting_week date,
    disposition_step smallint NOT NULL, disposition text NOT NULL,
    use_sentiment boolean NOT NULL, use_sightings boolean NOT NULL,
    PRIMARY KEY (corpus, post_id, source_query, row_seq)
)"""


def load() -> None:
    """COPY into a temp table, then one INSERT .. ON CONFLICT (the gdelt.load pattern)."""
    from h5n1.db import get_engine

    df = pd.read_parquet(OUT)
    for c in ("followers", "retweets", "favorites", "replies", "impressions"):
        df[c] = df[c].astype("Int64")
    conn = get_engine().raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute(f"CREATE TEMP TABLE tmp_{TABLE} (LIKE {TABLE} INCLUDING DEFAULTS) ON COMMIT DROP")
        with cur.copy(f"COPY tmp_{TABLE} ({', '.join(COLS)}) FROM STDIN (FORMAT csv)") as cp:
            for start in range(0, len(df), 100_000):
                buf = io.StringIO()
                df.iloc[start:start + 100_000][COLS].to_csv(buf, index=False, header=False)
                cp.write(buf.getvalue())
        key = ["corpus", "post_id", "source_query", "row_seq"]
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c not in key)
        cur.execute(f"INSERT INTO {TABLE} ({', '.join(COLS)}) SELECT {', '.join(COLS)} FROM tmp_{TABLE} "
                    f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}")
        print(f"{TABLE}: {cur.rowcount:,} rows upserted")
        for c, sql in [("state", "state"), ("disposition", "disposition"), ("stream", "stream")]:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_{c}_idx ON {TABLE} ({sql})")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "load"])
    {"build": build, "load": load}[ap.parse_args().step]()
