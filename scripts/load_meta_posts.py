"""Load the Meta Content Library CSVs into meta_post.

29 files, 485,652 rows, 1.9 GB across five folders. All share one 36-column header,
so they stack without reconciliation. Verified 2026-10-25: zero malformed rows,
valid UTF-8 throughout -- no cleaning pass needed.

WHAT THE CSV DOES NOT CARRY. The search term and the language are in the FILENAME,
not in any column ("gripe-aviar-es-dec-1-2021-to-jul-31-2026.csv"). They are parsed
from it here rather than hand-typed per file: a typo in `corpus` would fail a CHECK
constraint, but a typo in `search_term` loads clean and silently corrupts provenance.

DATES. creation_time and modified_time are ISO 8601 carrying an explicit +00:00, so
they cast straight to timestamptz with no parse template and no timezone guessing.
statistics.views_date_last_refreshed is a plain DATE (no time) and is blank in about
10 percent of rows -- that means Meta never recomputed the view count, which is not
the same as zero views, so it stays NULL.

ENGAGEMENT COLUMNS stay text. Typing them integer at load means one odd value aborts
a 568 MB file; cast them in a view once the profile looks clean.

    cloud-sql-proxy harvard-capstone-499102:us-central1:h5n1-pg --port 5433  # terminal A
    export PGPASSWORD=...
    python scripts/load_meta_posts.py --dry-run       # show the plan
    python scripts/load_meta_posts.py --create        # create the table
    python scripts/load_meta_posts.py                 # load
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(
    "/Users/maxtang/Desktop/Chickencoop Capstone/H5N1 Meta Data "
)

PSQL = ["psql", "-h", "127.0.0.1", "-p", "5433", "-U", "h5n1_app", "-d", "h5n1",
        "-v", "ON_ERROR_STOP=1", "-q"]

# "gripe-aviar-es-dec-1-2021-to-jul-31-2026.csv" -> ("gripe-aviar", "es")
# "bird-flu-en-aug-1-2025-to-jul-31-2026.csv"    -> ("bird-flu", "en")
NAME_RE = re.compile(r"^(?P<term>.+?)-(?P<lang>en-es|en|es)-(?:dec|aug|jan|jul)-")

# The CSV's 36 columns, in file order. COPY is positional, so this order IS the
# contract between the file and the staging table.
RAW_COLUMNS = [
    "activities", "content_type", "creation_time", "id", "is_branded_content",
    "lang", "link_attachment.caption", "link_attachment.description",
    "link_attachment.link", "link_attachment.name", "match_type", "mcl_url",
    "modified_time", "multimedia", "post_owner.id", "post_owner.name",
    "post_owner.type", "post_owner.username", "shared_post_id",
    "statistics.angry_count", "statistics.care_count", "statistics.comment_count",
    "statistics.haha_count", "statistics.like_count", "statistics.love_count",
    "statistics.reaction_count", "statistics.sad_count", "statistics.share_count",
    "statistics.views", "statistics.views_date_last_refreshed",
    "statistics.wow_count", "surface.id", "surface.name", "surface.type",
    "surface.username", "text",
]

STAGING_DDL = "CREATE TABLE IF NOT EXISTS meta_stg (" + ", ".join(
    f"c{i} text" for i in range(1, len(RAW_COLUMNS) + 1)
) + ")"

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS meta_post (
    post_id              text,
    mcl_url              text,

    creation_time        timestamptz,
    modified_time        timestamptz,
    views_date_refreshed date,

    content_type         text,
    lang                 text,
    post_text            text,
    match_type           text,
    multimedia           text,
    is_branded_content   text,
    shared_post_id       text,
    activities           text,

    link_caption         text,
    link_description     text,
    link_url             text,
    link_name            text,

    owner_id             text,
    owner_name           text,
    owner_type           text,
    owner_username       text,
    surface_id           text,
    surface_name         text,
    surface_type         text,
    surface_username     text,

    angry_count text, care_count text, comment_count text, haha_count text,
    like_count text, love_count text, reaction_count text, sad_count text,
    share_count text, views text, wow_count text,

    platform             text NOT NULL DEFAULT 'meta',
    search_term          text NOT NULL,
    search_lang          text NOT NULL,
    source_folder        text NOT NULL,
    source_file          text NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_meta_created ON meta_post (creation_time);
CREATE INDEX IF NOT EXISTS ix_meta_term    ON meta_post (search_term);
CREATE INDEX IF NOT EXISTS ix_meta_lang    ON meta_post (lang);
CREATE INDEX IF NOT EXISTS ix_meta_owner   ON meta_post (owner_id);
"""

# Staging c1..c36 -> meta_post, in the target table's column order. Blank strings
# become NULL before casting: COPY CSV gives '' for an unquoted empty field, and
# ''::timestamptz raises rather than returning NULL.
INSERT_SQL = """
INSERT INTO meta_post (
    post_id, mcl_url, creation_time, modified_time, views_date_refreshed,
    content_type, lang, post_text, match_type, multimedia, is_branded_content,
    shared_post_id, activities,
    link_caption, link_description, link_url, link_name,
    owner_id, owner_name, owner_type, owner_username,
    surface_id, surface_name, surface_type, surface_username,
    angry_count, care_count, comment_count, haha_count, like_count, love_count,
    reaction_count, sad_count, share_count, views, wow_count,
    platform, search_term, search_lang, source_folder, source_file
)
SELECT
    NULLIF(c4,  ''),                      -- id
    NULLIF(c12, ''),                      -- mcl_url
    NULLIF(c3,  '')::timestamptz,         -- creation_time
    NULLIF(c13, '')::timestamptz,         -- modified_time
    NULLIF(c30, '')::date,                -- views_date_last_refreshed
    NULLIF(c2,  ''), NULLIF(c6, ''), NULLIF(c36, ''),
    NULLIF(c11, ''), NULLIF(c14, ''), NULLIF(c5, ''),
    NULLIF(c19, ''), NULLIF(c1, ''),
    NULLIF(c7,  ''), NULLIF(c8, ''), NULLIF(c9, ''), NULLIF(c10, ''),
    NULLIF(c15, ''), NULLIF(c16, ''), NULLIF(c17, ''), NULLIF(c18, ''),
    NULLIF(c32, ''), NULLIF(c33, ''), NULLIF(c34, ''), NULLIF(c35, ''),
    NULLIF(c20, ''), NULLIF(c21, ''), NULLIF(c22, ''), NULLIF(c23, ''),
    NULLIF(c24, ''), NULLIF(c25, ''), NULLIF(c26, ''), NULLIF(c27, ''),
    NULLIF(c28, ''), NULLIF(c29, ''), NULLIF(c31, ''),
    'meta', :term, :lang, :folder, :file
FROM meta_stg
"""


def psql(sql: str) -> str:
    r = subprocess.run(PSQL + ["-c", sql], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"psql failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def scalar(sql: str) -> int:
    """One number back. -t drops the header, -A drops the column padding; without
    both, psql returns 'count \n-------\n     6\n(1 row)' and int() chokes."""
    r = subprocess.run(PSQL + ["-t", "-A", "-c", sql], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"psql failed:\n{r.stderr.strip()}")
    return int(r.stdout.strip())


def plan() -> list[tuple[pathlib.Path, str, str]]:
    """(path, search_term, lang), skipping the clean/ copies."""
    items = []
    for f in sorted(ROOT.glob("*/*.csv")):
        if f.parent.name == "clean":        # rewritten copies; same rows, flat dir
            continue
        m = NAME_RE.match(f.name)
        if not m:
            print(f"skip   unparseable filename: {f.name}")
            continue
        items.append((f, m.group("term"), m.group("lang")))
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    ap.add_argument("--create", action="store_true", help="create the tables, then exit")
    ap.add_argument("--limit", type=int, help="load only the first N files (for timing)")
    args = ap.parse_args()

    if not os.environ.get("PGPASSWORD"):
        sys.exit("PGPASSWORD is not set. export it first (no quotes needed).")

    items = plan()
    if args.limit:
        items = sorted(items, key=lambda t: t[0].stat().st_size)[: args.limit]

    if args.dry_run:
        total = 0
        for f, term, lang in items:
            mb = f.stat().st_size / 1e6
            total += mb
            print(f"  {term:20s} {lang:5s} {mb:8.1f} MB  {f.parent.name[:34]}")
        print(f"\n{len(items)} files, {total/1000:.2f} GB")
        return

    if args.create:
        psql(TABLE_DDL)
        psql(STAGING_DDL)
        print("created meta_post and meta_stg")
        return

    psql(STAGING_DDL)
    for i, (f, term, lang) in enumerate(items, 1):
        psql("TRUNCATE meta_stg")
        cols = ", ".join(f"c{n}" for n in range(1, len(RAW_COLUMNS) + 1))
        r = subprocess.run(
            PSQL + ["-c", f"\\copy meta_stg ({cols}) FROM '{f}' "
                          f"WITH (FORMAT csv, HEADER true)"],
            capture_output=True, text=True,
        )
        if r.returncode:
            sys.exit(f"COPY failed on {f.name}:\n{r.stderr.strip()}")

        sql = (INSERT_SQL
               .replace(":term",   f"'{term}'")
               .replace(":lang",   f"'{lang}'")
               .replace(":folder", "'" + f.parent.name.replace("'", "''") + "'")
               .replace(":file",   "'" + f.name.replace("'", "''") + "'"))
        psql(sql)
        n = scalar("SELECT count(*) FROM meta_post")
        print(f"[{i:2d}/{len(items)}] {term:20s} {lang:5s} -> meta_post now {n:,}")

    psql("DROP TABLE meta_stg")
    print("\ndone. meta_stg dropped.")


if __name__ == "__main__":
    main()
