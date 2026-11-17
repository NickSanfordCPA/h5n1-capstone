"""The deterministic half of the funnel, identical for Meta and X (steps 2-8).

    uv run python -m h5n1.journal.funnel build     # rules -> data/journal/posts/funnel.parquet
    uv run python -m h5n1.journal.funnel load      # -> journal.post in the local database
    uv run python -m h5n1.journal.funnel report    # counts by platform / language / step

Each step removes a post for ONE reason. A post's `disposition` is the first step that
removes it; `disposition_step` is that step's number. Posts that survive step 8 are the
READER SET: every one is read by the LLM readers (steps 9 on, see labels.py).

  1  Duplicate search result       ID match          (row level; done in posts.py)
  2  Not English or Spanish        language detector (posts.py lang)
  3  Outside the analysis window   metadata rule     WINDOW_START..WINDOW_END, UTC dates
  4  Quote or reshare              metadata rule     X quote posts, Meta reshares
  5  Not about avian influenza     word rule         RELEVANT_RE below
  6  No US state named             word mapping      textgeo: class none / foreign
  7  Names more than one US state  word mapping      textgeo: class multi
  8  Copypasta repeat              text fingerprint  later copies of a mass-posted text

Nothing here reads outcome data. The window end is the last APHIS confirmation date in the
outcome file, which is a property of the file, not of any model result.
"""
from __future__ import annotations

import argparse
import io
import re
import unicodedata

import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import assert_local, local_engine
from h5n1.journal.posts import LANGS_KEPT, OUT
from h5n1.journal.textgeo import geocode_text
from h5n1.sources.x_location import geocode_series
from h5n1.sources.x_posts import ES_FOREIGN

WINDOW_START = pd.Timestamp("2022-06-01", tz="UTC")   # first day of the prior-cohort X pull
WINDOW_END = pd.Timestamp("2026-07-13", tz="UTC")     # last APHIS poultry confirmation loaded
COPYPASTA_MIN = 5

# A post is "about avian influenza" if its text names the disease. Phrases, not co-occurring
# single words: the Meta export matched tokens independently, so "duck ... flu" hits are
# mostly off-topic. Subtypes are the avian ones (H5, H7, H9, H10N3/N8, H3N8); H1N1 and H3N2
# alone do not qualify.
# Matched on NFKC-normalised text. A subtype is matched without a trailing word boundary
# because scraped X text often runs into the next word ("#H5N1Let's").
RELEVANT_RE = re.compile(
    r"bird[\s-]?flu|avian[\s-]?(?:flu|influenza)|(?:poultry|chicken|duck|fowl)[\s-]flu\b|"
    r"fowl plague|\bhpai\b|\blpai\b|highly pathogenic|"
    r"\bh5n\d|\bh5(?=\s+(?:avian|bird|virus|flu|influenza))|\bh7n\d|\bh9n2|\bh10n[38]|\bh3n8|"
    r"(?:gripe|gripa|influenza|peste|fiebre)[\s-]?(?:aviar|aviaria|av[ií]cola)\b|"
    r"(?:gripe|gripa|influenza) (?:de|en) (?:las )?aves|grip[ea] del? pollo|\biaap\b|\biabp\b|"
    r"altamente pat[óo]gena",
    re.I)

STEPS = {1: "Duplicate search result", 2: "Not English or Spanish",
         3: "Outside the analysis window", 4: "Quote or reshare",
         5: "Not about avian influenza", 6: "No US state named",
         7: "Names more than one US state", 8: "Copypasta repeat", 0: "Reader set"}


def build() -> None:
    posts = pd.read_parquet(OUT / "posts.parquet")
    posts = posts.merge(pd.read_parquet(OUT / "lang.parquet"), on=["platform", "post_id"])
    posts["day"] = posts["created_at"].dt.tz_convert("UTC").dt.date
    step = pd.Series(0, index=posts.index)

    def cut(n: int, mask: pd.Series) -> None:
        step.loc[(step == 0) & mask] = n
        print(f"step {n} {STEPS[n]}: removed {(step == n).sum():,}; left {(step == 0).sum():,}",
              flush=True)

    cut(2, ~posts["lang_detected"].isin(LANGS_KEPT))
    cut(3, (posts["created_at"] < WINDOW_START)
        | (posts["created_at"] >= WINDOW_END + pd.Timedelta(days=1)))
    cut(4, posts["post_type"].isin(["quote", "reshare"]))
    # styled Unicode capitals must read as plain letters for every word rule
    norm = posts["text"].map(lambda s: unicodedata.normalize("NFKC", s))
    posts["relevant_rule"] = norm.str.contains(RELEVANT_RE)
    cut(5, ~posts["relevant_rule"])

    # geocode only what is still in the funnel
    live = posts.index[step == 0]
    print(f"geocoding {len(live):,} posts ...", flush=True)
    geo = pd.DataFrame([geocode_text(t) for t in posts.loc[live, "text"]], index=live)
    posts = posts.join(geo)
    cut(6, posts["geo_class"].isin(["none", "foreign"]))
    cut(7, posts["geo_class"] == "multi")

    # copypasta: groups are formed within platform x language over every in-window,
    # non-quote post (located or not), as in the earlier funnels; the earliest copy stays
    pool = posts[step.isin([0, 5, 6, 7])]
    key = (norm.loc[pool.index].str.lower().str.replace(r"https?://\S+|@\w+|\d+", "", regex=True)
           .str.replace(r"\W+", " ", regex=True).str.strip().str[:80])
    g = pool.assign(key=key).groupby(["platform", "lang_detected", "key"])
    n_posts, n_auth = g["post_id"].transform("size"), g["author_id"].transform("nunique")
    cp = (n_posts >= COPYPASTA_MIN) & (n_auth >= 2) & (key.str.len() >= 20)
    first = (pool.assign(key=key)[cp].sort_values(["created_at", "post_id"], kind="stable")
             .drop_duplicates(["platform", "lang_detected", "key"]).index)
    posts["copypasta"] = False
    posts.loc[cp[cp].index, "copypasta"] = True
    posts["copypasta_first"] = posts.index.isin(first)
    cut(8, posts["copypasta"] & ~posts["copypasta_first"])

    posts["disposition_step"] = step
    posts["disposition"] = step.map(STEPS)
    posts["reader_set"] = step == 0

    # X only: the profile-location tier, kept for the sensitivity check
    isx = (posts["platform"] == "x") & step.isin([0, 6, 7, 8])
    ul = geocode_series(posts.loc[isx, "user_location"])
    foreign = posts.loc[isx, "user_location"].str.contains(ES_FOREIGN) & (
        posts.loc[isx, "lang_detected"] == "es")
    ul.loc[foreign, ["state", "county_fips"]] = None
    posts["ul_level"], posts["ul_state"], posts["ul_county_fips"] = None, None, None
    posts.loc[isx, "ul_level"] = ul["geo_level"]
    posts.loc[isx, "ul_state"] = ul["state"]
    posts.loc[isx, "ul_county_fips"] = ul["county_fips"]

    bad = posts["county_fips"].dropna().astype(str).str.match(r"^091\d\d$")
    if bad.any():
        raise RuntimeError("091xx Connecticut planning-region FIPS in the geocoder output")
    posts.to_parquet(OUT / "funnel.parquet", index=False)
    report(posts)


def report(posts: pd.DataFrame | None = None) -> None:
    if posts is None:
        posts = pd.read_parquet(OUT / "funnel.parquet")
    raw = pd.read_parquet(OUT / "raw_rows.parquet", columns=["platform"])
    print("\nrows collected:", raw["platform"].value_counts().to_dict())
    print("unique posts:  ", posts["platform"].value_counts().to_dict())
    lang = posts["lang_detected"].where(posts["lang_detected"].isin(LANGS_KEPT), "other")
    tab = pd.crosstab([posts["disposition_step"], posts["disposition"]],
                      [posts["platform"], lang], margins=True)
    print(tab.to_string())
    rs = posts[posts["reader_set"]]
    print("\nreader set by platform x collector x period:")
    period = (rs["created_at"] < pd.Timestamp("2025-01-01", tz="UTC")).map(
        {True: "2022-24", False: "2025+"})
    print(pd.crosstab([rs["platform"], rs["collector"]], period, margins=True).to_string())
    print("\nreader set geo tier:")
    print(pd.crosstab(rs["platform"], rs["geo_tier"], margins=True).to_string())
    print("\nflags in reader set:", rs[["ambiguous", "foreign_marker", "copypasta_first"]]
          .sum().to_dict())
    xs = rs[rs["platform"] == "x"]
    both = xs["ul_state"].notna()
    print(f"X reader set: profile state known for {both.mean():.1%}; of those, "
          f"equal to the text state {(xs.loc[both, 'ul_state'] == xs.loc[both, 'state']).mean():.1%}")


def load() -> None:
    """Write journal.post (every unique post, both platforms) to the local database."""
    eng = local_engine()
    assert_local(eng)
    posts = pd.read_parquet(OUT / "funnel.parquet")
    cols = ["platform", "post_id", "collector", "collectors", "terms", "n_rows", "created_at",
            "day", "lang_platform", "lang_detected", "lang_source", "post_type", "text",
            "author_id", "author_name", "author_handle", "owner_type", "surface_type",
            "verified", "verified_type", "followers", "bio", "user_location",
            "relevant_rule", "geo_class", "state", "county_fips", "geo_tier", "geo_matched",
            "n_states", "foreign_marker", "ambiguous", "copypasta", "copypasta_first",
            "ul_level", "ul_state", "ul_county_fips",
            "disposition_step", "disposition", "reader_set"]
    df = posts[cols].copy()
    for c in ("text", "bio", "user_location", "author_name", "geo_matched"):
        df[c] = df[c].astype("string").str.replace("\x00", "", regex=False)
    # posts removed before the geocoder ran have no geocoder output: keep those NULL
    df["n_states"] = df["n_states"].astype("Int64")
    for c in ("foreign_marker", "ambiguous", "relevant_rule"):
        df[c] = df[c].astype("boolean")
    ddl = """
    DROP TABLE IF EXISTS journal.post CASCADE;
    CREATE TABLE journal.post (
        platform text NOT NULL, post_id text NOT NULL, collector text, collectors text,
        terms text, n_rows integer, created_at timestamptz, day date,
        lang_platform text, lang_detected text, lang_source text, post_type text, text text,
        author_id text, author_name text, author_handle text, owner_type text,
        surface_type text, verified boolean, verified_type text, followers bigint, bio text,
        user_location text, relevant_rule boolean, geo_class text, state char(2),
        county_fips char(5), geo_tier text, geo_matched text, n_states integer,
        foreign_marker boolean, ambiguous boolean, copypasta boolean, copypasta_first boolean,
        ul_level text, ul_state char(2), ul_county_fips char(5),
        disposition_step integer NOT NULL, disposition text NOT NULL, reader_set boolean NOT NULL,
        PRIMARY KEY (platform, post_id)
    );
    COMMENT ON TABLE journal.post IS
      'Journal rebuild: every unique post on both platforms with its funnel disposition '
      '(steps 1-8, deterministic). Built by h5n1/journal/funnel.py. No outcome data.';
    """
    raw = eng.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(ddl)
            # 50k rows at a time: the whole table as one CSV string does not fit in memory
            with cur.copy(f"COPY journal.post ({', '.join(cols)}) FROM STDIN "
                          f"WITH (FORMAT csv, NULL '\\N')") as cp:
                for i in range(0, len(df), 50_000):
                    buf = io.StringIO()
                    df.iloc[i:i + 50_000].to_csv(buf, index=False, header=False, na_rep="\\N")
                    cp.write(buf.getvalue())
            cur.execute("CREATE INDEX ix_post_reader ON journal.post (platform, reader_set)")
            cur.execute("CREATE INDEX ix_post_state_day ON journal.post (state, day)")
            cur.execute("ANALYZE journal.post")
        raw.commit()
    finally:
        raw.close()
    with eng.connect() as cn:
        print(pd.read_sql(text("select platform, count(*) posts, count(*) filter (where "
                               "reader_set) reader_set from journal.post group by 1"), cn))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "load", "report"])
    {"build": build, "load": load, "report": report}[ap.parse_args().step]()
