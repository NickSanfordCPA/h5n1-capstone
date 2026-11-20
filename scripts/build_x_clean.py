"""Build x_post_clean: the X corpus put through the Meta cleaning funnel.

WHY. The Meta and X state signals should come from the same cleaning rules, so that a
difference between the platforms is a difference in what people posted, not in how
the data was filtered. This applies scripts/build_meta_analysis.py stage for stage to
x_post_unique and writes one row per post with the stage that removed it, or
'Candidate'.

NOT x_post_analysis. That table is Nick's X funnel (603,265 rows, author-location
based, with LLM sighting labels). This script neither reads nor touches it.

    Stage 0  rows in x_post_unique                     540,225
    Stage 1  Duplicate search result   -> already one row per post; counted, not removed
    Stage 2  No keyword in text        -> the post never mentions avian flu
    Stage 3  Hashtag farm              -> 30+ hashtags
    Stage 4  Copypasta repeat          -> later copy of a mass-posted text
    Stage 6  Foreign / multi-state     -> names a non-US place, or several states
    Stage 5  No US state               -> text names no US state
             Candidate                 -> survives

WHAT DIFFERS FROM META, AND WHY.
  * Keywords. Stage 2 keeps a post only if its text contains a search term. X was
    searched with different terms than Meta, including French ones, so the list is
    Meta's terms plus every term in scripts/pull_twitterapi_{en_gap,es,fr}.py. Using
    Meta's list alone would drop every French post.
  * Geography. French posts (lang='fr') also match French state and country names;
    see FRENCH_STATE in h5n1/sources/meta_location.py. Other languages are read
    exactly as for Meta.
  * Copypasta authors are counted by user_id (Meta: owner_id).
Everything else -- thresholds, stage order, the gazetteer -- is imported from the
Meta script, so the two cannot drift apart.

    uv run python -m scripts.build_x_clean --dry-run      # print the funnel only
    uv run python -m scripts.build_x_clean                # write x_post_clean
"""
from __future__ import annotations

import argparse
import re

import pandas as pd
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.meta_location import Gazetteer
from scripts.build_meta_analysis import (COPYPASTA_MIN_AUTHORS, COPYPASTA_MIN_COPIES,
                                         KEYWORDS as META_KEYWORDS, MAX_HASHTAGS,
                                         norm_prefix)

# Meta's 24 terms plus the X search terms not already in it. Accents are optional
# because tweets drop them freely ("gripe avicola", "elevages").
X_EXTRA = (r"gripe av[ií]cola|gripe de aves de corral|gripe de las gallinas|gripe del? pato"
           r"|gripe zoon[oó]tica|influenza zoon[oó]tica"
           r"|grippe aviaire|influenza aviaire|peste aviaire|grippe de la volaille"
           r"|grippe des volailles|grippe d[eu]s? poulets?|grippe d[eu]s? canards?"
           r"|grippe de ferme|grippe des [ée]levages|grippe zoonotique|influenza zoonotique"
           r"|iahp|iafp")
KEYWORDS = re.compile(META_KEYWORDS.pattern[:-1] + "|" + X_EXTRA + ")", re.I)

SOURCE_SQL = """
    SELECT post_id, created_at, lang, post_type, user_id, user_name, alias, corpus,
           array_to_string(source_queries, '|') AS search_terms, times_matched, post_text
    FROM x_post_unique
"""


def build(engine, limit: int | None = None) -> pd.DataFrame:
    sql = SOURCE_SQL + (f" ORDER BY md5(post_id) LIMIT {int(limit)}" if limit else "")
    df = pd.read_sql(sql, engine)
    print(f"stage 0  loaded                     {len(df):>9,}")

    # --- stage 1: x_post_unique is already one row per post ------------------
    dupes = int(df.times_matched.sum() - len(df))
    df["disposition"] = "Candidate"
    print(f"stage 1  duplicate search result   -{0:>9,}  ({dupes:,} extra copies removed upstream)")

    # --- stage 2: the post never mentions the subject -------------------------
    m = ~df.post_text.fillna("").str.contains(KEYWORDS)
    df.loc[m, "disposition"] = "No keyword in text"
    print(f"stage 2  no keyword in text        -{m.sum():>9,}")

    # --- stage 3: hashtag farms ----------------------------------------------
    df["hashtags"] = df.post_text.fillna("").str.count("#")
    m = (df.hashtags >= MAX_HASHTAGS) & (df.disposition == "Candidate")
    df.loc[m, "disposition"] = "Hashtag farm"
    print(f"stage 3  hashtag farm              -{m.sum():>9,}")

    # --- stage 4: copypasta ---------------------------------------------------
    live = df.disposition == "Candidate"
    df["prefix"] = df.post_text.fillna("").map(norm_prefix)
    g = (df[live].groupby("prefix")
                 .agg(copies=("post_id", "size"), authors=("user_id", "nunique")))
    mass = set(g[(g.copies >= COPYPASTA_MIN_COPIES)
                 & (g.authors >= COPYPASTA_MIN_AUTHORS)].index)
    is_mass = live & df.prefix.isin(mass)
    first = (df[is_mass].sort_values(["created_at", "post_id"])
                        .drop_duplicates("prefix").index)
    m = is_mass & ~df.index.isin(first)
    df.loc[m, "disposition"] = "Copypasta repeat"
    print(f"stage 4  copypasta repeat          -{m.sum():>9,}")

    # --- stages 5 and 6: geography -------------------------------------------
    gz = Gazetteer.from_db(engine)
    live = df.disposition == "Candidate"
    geo = gz.extract_series(df.loc[live, "post_text"].fillna(""), df.loc[live, "lang"])
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="process only N rows (a fixed random set)")
    ap.add_argument("--dry-run", action="store_true", help="print the funnel, write nothing")
    args = ap.parse_args()

    engine = get_engine()
    df = build(engine, args.limit)

    print("\n--- disposition by language ---")
    print(pd.crosstab(df.disposition, df.lang.where(df.lang.isin(["en", "es", "fr"]), "other"),
                      margins=True).to_string())
    print("\n--- geo tier (candidates) ---")
    print(df[df.use_sentiment].geo_tier.value_counts().to_string())
    print("\n--- top states (candidates) ---")
    print(df[df.use_sentiment].state.value_counts().head(12).to_string())

    if args.dry_run:
        print("\ndry run: nothing written")
        return

    keep = ["post_id", "created_at", "lang", "post_type", "user_id", "user_name", "alias",
            "corpus", "search_terms", "times_matched", "post_text", "hashtags", "geo_tier",
            "state", "county_fips", "geo_matched", "disposition", "use_sentiment"]
    out = df[keep]
    out.to_sql("x_post_clean", engine, if_exists="replace", index=False,
               method="multi", chunksize=500)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE x_post_clean ADD PRIMARY KEY (post_id)"))
        conn.execute(text("CREATE INDEX ON x_post_clean (state, created_at)"))
        conn.execute(text("CREATE INDEX ON x_post_clean (disposition)"))
    print(f"\nwrote x_post_clean: {len(out):,} rows")


if __name__ == "__main__":
    main()
