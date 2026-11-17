"""Build meta_post_analysis: the Meta corpus with a disposition per post.

Mirrors the shape of Nick's X funnel (docs/x_data_funnel.md) so the two platforms
can be compared rather than merely pooled. One row per post, carrying the first
stage that removed it or 'Candidate' if it survived to the LLM step.

    Stage 0  rows in meta_post                         485,652
    Stage 1  Duplicate search result   -> one row per post_id, terms kept as array
    Stage 2  No keyword in text        -> the post never mentions avian flu
    Stage 3  Hashtag farm              -> 30+ hashtags; tag-stuffing spam
    Stage 4  Copypasta repeat          -> later copy of a mass-posted text
    Stage 5  No US state               -> text names no US state (h5n1.sources.meta_location)
    Stage 6  Foreign / multi-state     -> names a non-US place, or several states
             Candidate                 -> survives; goes to sentiment and the LLM role step

WHY THE STAGES ARE IN THIS ORDER. Each is cheaper than the next and removes the most
rows, so the expensive work runs on the smallest set. Deduplication first because it
is pure arithmetic; geocoding last because it is the only stage that parses text
closely.

WHAT THIS DOES NOT DECIDE. Whether a post is REPORTING AN EVENT in the state it
names. "Unlike Iowa, our state has had no cases" names Iowa and means the opposite.
That is an LLM judgement, run separately on the Candidate set, and kept out of here
so this half stays exactly reproducible.

    cloud-sql-proxy harvard-capstone-499102:us-central1:h5n1-pg --port 5433
    uv run python scripts/build_meta_analysis.py --dry-run
    uv run python scripts/build_meta_analysis.py
"""
from __future__ import annotations

import argparse
import re

import pandas as pd
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.meta_location import Gazetteer

# The 24 search terms actually used, from the Content Library filenames. An
# incomplete copy of this list silently under-counts: an earlier draft omitted
# duck/chicken/farm/poultry/zoonotic flu and reported 97.8% junk where the true
# figure was 33.4%.
KEYWORDS = re.compile(
    r"(avian flu|avian influenza|bird flu|chicken flu|duck flu|farm flu"
    r"|poultry flu|zoonotic flu|h5n1|h5n8|h7n9|h9n2|h10n3|hpai|lpai"
    r"|gripe aviar|gripe aviaria|gripe avicola|gripe de las aves|gripe de pollo"
    r"|gripe del pollo|influenza aviar|influenza aviaria|influenza avicola"
    r"|peste aviar|peste aviaria|iaap|iabp)", re.I)

MAX_HASHTAGS = 30          # 10k+ posts average 79.7; everything else averages 0.7-2.2
COPYPASTA_MIN_COPIES = 5   # matches Nick's X rule so the platforms are cleaned alike
COPYPASTA_MIN_AUTHORS = 2

SOURCE_SQL = """
    SELECT post_id, creation_time, lang, content_type, owner_type, owner_id,
           owner_name, surface_name, search_term, search_lang, post_text,
           like_count, comment_count, share_count, views
    FROM meta_post
"""


def norm_prefix(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())[:80]


def build(engine, limit: int | None = None) -> pd.DataFrame:
    sql = SOURCE_SQL + (f" LIMIT {int(limit)}" if limit else "")
    df = pd.read_sql(sql, engine)
    print(f"stage 0  loaded                     {len(df):>9,}")

    # --- stage 1: one row per post_id, every search term kept -----------------
    terms = (df.groupby("post_id")
               .agg(search_terms=("search_term", lambda s: sorted(set(s))),
                    times_matched=("search_term", "size")))
    df = (df.sort_values(["post_id", "creation_time"])
            .drop_duplicates("post_id")
            .join(terms, on="post_id"))
    df["disposition"] = "Candidate"
    dropped = terms.times_matched.sum() - len(df)
    print(f"stage 1  duplicate search result   -{dropped:>9,}  -> {len(df):,}")

    # --- stage 2: the post never mentions the subject -------------------------
    m = ~df.post_text.fillna("").str.contains(KEYWORDS)
    df.loc[m & (df.disposition == "Candidate"), "disposition"] = "No keyword in text"
    print(f"stage 2  no keyword in text        -{m.sum():>9,}")

    # --- stage 3: hashtag farms ----------------------------------------------
    df["hashtags"] = df.post_text.fillna("").str.count("#")
    m = df.hashtags >= MAX_HASHTAGS
    df.loc[m & (df.disposition == "Candidate"), "disposition"] = "Hashtag farm"
    print(f"stage 3  hashtag farm              -{(m & (df.disposition == 'Hashtag farm')).sum():>9,}")

    # --- stage 4: copypasta ---------------------------------------------------
    live = df.disposition == "Candidate"
    df["prefix"] = df.post_text.fillna("").map(norm_prefix)
    g = (df[live].groupby("prefix")
                 .agg(copies=("post_id", "size"), authors=("owner_id", "nunique")))
    mass = set(g[(g.copies >= COPYPASTA_MIN_COPIES)
                 & (g.authors >= COPYPASTA_MIN_AUTHORS)].index)
    is_mass = live & df.prefix.isin(mass)
    first = df[is_mass].sort_values("creation_time").drop_duplicates("prefix").index
    m = is_mass & ~df.index.isin(first)
    df.loc[m, "disposition"] = "Copypasta repeat"
    print(f"stage 4  copypasta repeat          -{m.sum():>9,}")

    # --- stages 5 and 6: geography -------------------------------------------
    gz = Gazetteer.from_db(engine)
    live = df.disposition == "Candidate"
    geo = gz.extract_series(df.loc[live, "post_text"].fillna(""),
                            df.loc[live, "lang"])
    df = df.join(geo)

    m = live & df.geo_tier.isin(["T5_multi", "T6_foreign"])
    df.loc[m, "disposition"] = "Foreign or multi-state"
    print(f"stage 6  foreign / multi-state     -{m.sum():>9,}")

    m = (df.disposition == "Candidate") & df.state.isna()
    df.loc[m, "disposition"] = "No US state"
    print(f"stage 5  no US state               -{m.sum():>9,}")

    df["use_sentiment"] = df.disposition == "Candidate"
    print(f"\n         CANDIDATES                 {df.use_sentiment.sum():>9,}")
    return df.drop(columns=["prefix"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, help="process only N rows (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="print the funnel, write nothing")
    args = ap.parse_args()

    engine = get_engine()
    df = build(engine, args.limit)

    print("\n--- disposition ---")
    print(df.disposition.value_counts().to_string())
    print("\n--- geo tier (candidates) ---")
    print(df[df.use_sentiment].geo_tier.value_counts().to_string())
    print("\n--- top states (candidates) ---")
    print(df[df.use_sentiment].state.value_counts().head(12).to_string())

    if args.dry_run:
        print("\ndry run: nothing written")
        return

    keep = ["post_id", "creation_time", "lang", "content_type", "owner_type",
            "owner_id", "owner_name", "surface_name", "search_terms",
            "times_matched", "post_text", "hashtags", "geo_tier", "state",
            "county_fips", "geo_matched", "disposition", "use_sentiment"]
    out = df[keep].copy()
    out["search_terms"] = out.search_terms.map(lambda x: "|".join(x))

    out.to_sql("meta_post_analysis", engine, if_exists="replace", index=False,
               method="multi", chunksize=500)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE meta_post_analysis ADD PRIMARY KEY (post_id)"))
        conn.execute(text("CREATE INDEX ON meta_post_analysis (state, creation_time)"))
        conn.execute(text("CREATE INDEX ON meta_post_analysis (disposition)"))
    print(f"\nwrote meta_post_analysis: {len(out):,} rows")


if __name__ == "__main__":
    main()
