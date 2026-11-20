"""Load the X / Twitter post corpora into fact_x_post (sql/012_x_posts.sql).

Two cohorts, one vendor export shape:

    current   data/twitterapi/{en_gap,es,fr}/x_*.csv   33 cols, header ends source_query
    previous  <prev-dir>/*.csv                         32 cols, EN only, one file per
                                                       query term, query name in the FILENAME

The headers are byte-identical in the same order; the current pull just appends
source_query. So one staging table serves both: 32-column files are COPYed with an
explicit c1..c32 column list, leaving c33 NULL, and source_query falls back to the
name parsed out of the filename.

WHY THIS IS A SCRIPT AND NOT 17 HAND-TYPED INSERTS. Every file needs its own
source_query literal. corpus is protected by a CHECK constraint, so a typo there
fails loudly -- but a typo in source_query loads clean and silently corrupts
provenance. Here it is parsed from the filename instead of typed, which removes
the failure mode rather than hoping to catch it.

Idempotent. Staging is TEMP and truncated per file; the insert is
ON CONFLICT (post_id) DO NOTHING, so re-running loads nothing new.

    cloud-sql-proxy harvard-capstone-499102:us-central1:h5n1-pg --port 5433   # terminal A
    uv run python sql/run_migrations.py                                       # create table
    uv run python scripts/load_x_posts.py --dry-run                           # show the plan
    uv run python scripts/load_x_posts.py                                     # load
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

from sqlalchemy import text

from h5n1.db import get_engine

REPO = pathlib.Path(__file__).resolve().parent.parent

# Current cohort: corpus value -> CSV, relative to the repo root. FR has no CSV until
# `python scripts/pull_twitterapi_fr.py export` has been run; it is skipped with a
# notice rather than treated as an error.
CURRENT = {
    "twitterapi_es": REPO / "data/twitterapi/es/x_es_2021_present.csv",
    "twitterapi_en": REPO / "data/twitterapi/en_gap/x_en_2025_present.csv",
    "twitterapi_fr": REPO / "data/twitterapi/fr/x_fr_2021_present.csv",
}

PREV_DIR_DEFAULT = pathlib.Path(
    "/Users/maxtang/Desktop/Chickencoop Capstone/Previous Cohort X dataset"
)

# "_HPAI_ -is_retweet since_2022-06-01 until_2025-01-01 lang_en.csv" -> "HPAI"
PREV_QUERY_RE = re.compile(r"^_(?P<q>.+?)_ -is_retweet ")

# Not avian flu -- mpox, left over from the previous cohort's other project. Excluded
# by default; --include-mpox loads it (e.g. as a negative control).
MPOX_QUERY = "viruela del mono"

# Raw CSV column order, shared by both cohorts. Index + 1 is the staging column
# number, which is how the INSERT below maps positionally. Kept here as the single
# written record of that order.
RAW_COLUMNS = [
    "_id", "userId", "userName", "alias", "profilePicture", "userCreatedAt", "bio",
    "followers", "following", "lists", "statusesCount", "verified", "userLocation",
    "lang", "type", "text", "createdAt", "mentions", "totalRetweets", "favorites",
    "replies", "links", "firstLink", "images", "impressions", "isImage", "firstImage",
    "location", "url", "noteTweetText", "retweetedUserId", "reportName", "source_query",
]

STAGING_DDL = "CREATE TEMP TABLE stg_x (" + ", ".join(
    f"c{i} TEXT" for i in range(1, len(RAW_COLUMNS) + 1)
) + ")"

# c32 (reportName) and c31 (retweetedUserId) are deliberately not selected: empty in
# every row of both cohorts. See sql/012_x_posts.sql.
#
# WHERE clause: a row with no id or no timestamp cannot be stored (PK / NOT NULL) and
# is not worth aborting a 300k-row load over, so it is counted and skipped instead.
INSERT_SQL = """
INSERT INTO fact_x_post (
    post_id, user_id, user_name, alias, profile_picture, user_created_at, bio,
    followers, following, lists, statuses_count, verified, user_location, lang,
    post_type, post_text, created_at, mentions, total_retweets, favorites,
    replies, links, first_link, images, impressions, is_image, first_image,
    location, url, note_tweet_text, corpus, source_query
)
SELECT DISTINCT ON (c1)
    c1,
    NULLIF(c2, ''), NULLIF(c3, ''), NULLIF(c4, ''), NULLIF(c5, ''),
    NULLIF(c6, '')::timestamptz,
    NULLIF(c7, ''),
    NULLIF(c8, '')::int, NULLIF(c9, '')::int, NULLIF(c10, '')::int,
    NULLIF(c11, '')::int,
    NULLIF(c12, '')::boolean,
    NULLIF(c13, ''), NULLIF(c14, ''), NULLIF(c15, ''),
    COALESCE(c16, ''),
    c17::timestamptz,
    NULLIF(c18, '')::jsonb,
    NULLIF(c19, '')::int, NULLIF(c20, '')::int, NULLIF(c21, '')::int,
    NULLIF(c22, '')::jsonb, NULLIF(c23, ''), NULLIF(c24, '')::jsonb,
    NULLIF(c25, '')::bigint,
    NULLIF(c26, '')::boolean,
    NULLIF(c27, ''), NULLIF(c28, ''), NULLIF(c29, ''), NULLIF(c30, ''),
    :corpus,
    COALESCE(NULLIF(c33, ''), :fallback_query)
FROM stg_x
WHERE NULLIF(c1, '') IS NOT NULL
  AND NULLIF(c17, '') IS NOT NULL
ON CONFLICT (post_id) DO NOTHING
"""


def header_width(path: pathlib.Path) -> int:
    """Column count from the header line, so a shape change is caught before COPY."""
    import csv

    with path.open(encoding="utf-8", newline="") as fh:
        return len(next(csv.reader(fh)))


def digest(path: pathlib.Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def plan(prev_dir: pathlib.Path, include_mpox: bool) -> list[tuple[pathlib.Path, str, str | None]]:
    """Build the load list as (path, corpus, fallback_source_query).

    Byte-identical files are collapsed: the previous cohort ships
    "_H5N8_ ... lang_en (1).csv" as an exact copy of "_H5N8_ ... lang_en.csv".
    ON CONFLICT would absorb it anyway, but reading 1 MB twice to discard it is
    pointless and the skip line is a useful thing to see.
    """
    items: list[tuple[pathlib.Path, str, str | None]] = []

    for corpus, path in CURRENT.items():
        if not path.exists():
            print(f"skip   {corpus:14s} {path.name} -- not exported yet")
            continue
        items.append((path, corpus, None))

    if not prev_dir.is_dir():
        print(f"warn   previous-cohort dir not found: {prev_dir}")
        return items

    seen: dict[str, pathlib.Path] = {}
    for path in sorted(prev_dir.glob("*.csv")):
        m = PREV_QUERY_RE.match(path.name)
        if not m:
            print(f"skip   unparseable filename: {path.name}")
            continue
        query = m.group("q")
        if query == MPOX_QUERY and not include_mpox:
            print(f"skip   {query!r} -- mpox, not avian flu (--include-mpox to load)")
            continue
        d = digest(path)
        if d in seen:
            print(f"skip   {path.name} -- byte-identical to {seen[d].name}")
            continue
        seen[d] = path
        items.append((path, "prev_cohort_en", query))

    return items


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prev-dir", type=pathlib.Path, default=PREV_DIR_DEFAULT)
    ap.add_argument("--include-mpox", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    args = ap.parse_args()

    items = plan(args.prev_dir, args.include_mpox)
    if not items:
        sys.exit("nothing to load")

    print(f"\n{len(items)} file(s) to load")
    if args.dry_run:
        for path, corpus, fallback in items:
            print(f"  {corpus:14s} {fallback or '(from CSV)':18s} {path.name}")
        return

    engine = get_engine()
    total = 0
    # One connection for the whole run: TEMP tables are session-scoped, and a
    # per-file transaction means a failure on file 9 keeps files 1-8.
    with engine.connect() as conn:
        conn.exec_driver_sql(STAGING_DDL)
        conn.commit()
        raw = conn.connection.driver_connection

        for path, corpus, fallback in items:
            width = header_width(path)
            if width not in (32, 33):
                print(f"FAIL   {path.name}: {width} columns, expected 32 or 33")
                continue
            cols = ", ".join(f"c{i}" for i in range(1, width + 1))

            conn.exec_driver_sql("TRUNCATE stg_x")
            copy_sql = f"COPY stg_x ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)"
            with raw.cursor() as cur:
                with cur.copy(copy_sql) as cp, path.open("rb") as fh:
                    while block := fh.read(1 << 20):
                        cp.write(block)
                staged = cur.execute("SELECT count(*) FROM stg_x").fetchone()[0]

            n = conn.execute(
                text(INSERT_SQL), {"corpus": corpus, "fallback_query": fallback}
            ).rowcount
            conn.commit()
            total += n
            dropped = staged - n
            note = f"  ({dropped:,} dup/unusable)" if dropped else ""
            print(f"load   {corpus:14s} {path.name[:46]:48s} staged {staged:7,}  "
                  f"inserted {n:7,}{note}")

    with engine.connect() as conn:
        grand = conn.execute(text("SELECT count(*) FROM fact_x_post")).scalar_one()
    print(f"\ninserted {total:,} new rows; fact_x_post now holds {grand:,}")


if __name__ == "__main__":
    main()
