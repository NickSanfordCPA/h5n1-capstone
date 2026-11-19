"""One post pipeline for both platforms: raw rows -> unique posts -> language.

Funnel steps 1-3 of docs/journal_rebuild/PROTOCOL.md. Nothing here reads outcome data.

    uv run python -m h5n1.journal.posts raw      # every collected row, both platforms
    uv run python -m h5n1.journal.posts dedup    # one row per (platform, post_id)
    uv run python -m h5n1.journal.posts lang     # the same language detector on both platforms

Inputs
    Meta  data/Meta Public/*.csv   Muthu's Meta Content Library UI export, 29 files
    X     public.x_raw             twitterapi.io pulls (en / es / fr), one JSON line per hit
          public.x_raw_en_hist     the same English pull for 2022-06..2024-12 (Max, 2026-11-18)
          public.x_raw_repair      Max's re-run of the missing and cut-short (query, day) pairs
                                   of all three pulls (2026-11-19); lines carry `_repair`
          (all copied unchanged from the shared database by copy_source.py)
          Until 2026-11-18 the 2022-06..2024-12 English posts came from the prior cohort's
          scraper export (public.x_csv_raw, collector 'prev_cohort_en'). It is no longer read:
          the corpus is now one collector on both sides of 2025-01-01 and equals the shared
          table x_post_unique_ss.

Outputs (parquet under data/journal/posts/, gitignored)
    raw_rows.parquet   one row per collected row, with `raw_ok` and the collector
    posts.parquet      one row per unique post
    lang.parquet       platform, post_id, lang_detected, lang_source

The same column names are used for both platforms, so every later step is one code path:
    platform        'meta' | 'x'
    collector       'meta_ui_export' | 'twitterapi_en_hist' | 'twitterapi_en' | 'twitterapi_es'
                    | 'twitterapi_fr'
    post_type       'original' | 'reply' | 'quote' | 'reshare'
    owner_type      Meta's post_owner.type ('page' | 'profile'); NULL on X
"""
from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata

import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import DATA_DIR, REPO, local_engine

OUT = DATA_DIR / "posts"
META_DIR = REPO / "data" / "Meta Public"
COLS = ["platform", "collector", "source", "term", "post_id", "created_at", "lang_platform",
        "post_type", "text", "author_id", "author_name", "author_handle", "owner_type",
        "surface_type", "verified", "verified_type", "followers", "bio", "user_location"]
COLLECTOR_ORDER = ["meta_ui_export", "twitterapi_en_hist", "twitterapi_en", "twitterapi_es",
                   "twitterapi_fr"]
HIST_END = "2025-01-01"   # English query days before this belong to the 2022-06..2024-12 pull
LANGS_KEPT = ("en", "es")
MIN_LETTERS = 20      # below this the detector is not trusted and the platform tag is used


# ---------------------------------------------------------------- step 1a: raw rows
def _meta_term(name: str) -> str:
    """'avian-flu-en-dec-1-2021-to-jul-31-2026.csv' -> 'avian flu'."""
    m = re.match(r"(.+?)-(?:en-es|en|es)-", name)
    return (m.group(1) if m else name).replace("-", " ")


def _meta_raw() -> pd.DataFrame:
    use = ["id", "creation_time", "lang", "content_type", "shared_post_id", "text",
           "post_owner.id", "post_owner.name", "post_owner.username", "post_owner.type",
           "surface.type"]
    frames = []
    for f in sorted(META_DIR.glob("*.csv")):
        df = pd.read_csv(f, dtype=str, keep_default_na=False, usecols=use)
        reshare = (df["content_type"] == "reshare") | (df["shared_post_id"] != "")
        frames.append(pd.DataFrame({
            "platform": "meta", "collector": "meta_ui_export", "source": f.name,
            "term": _meta_term(f.name), "post_id": df["id"],
            "created_at": pd.to_datetime(df["creation_time"], errors="coerce", utc=True),
            "lang_platform": df["lang"],
            "post_type": reshare.map({True: "reshare", False: "original"}),
            "text": df["text"], "author_id": df["post_owner.id"],
            "author_name": df["post_owner.name"], "author_handle": df["post_owner.username"],
            "owner_type": df["post_owner.type"], "surface_type": df["surface.type"],
            "verified": None, "verified_type": None, "followers": None, "bio": "",
            "user_location": "",
        }))
        print(f"  meta {f.name}: {len(df):,}", flush=True)
    return pd.concat(frames, ignore_index=True)


def _x_json_raw(cn, table: str = "x_raw") -> pd.DataFrame:
    """One twitterapi.io JSON-lines table. x_raw holds the en / es / fr pulls; x_raw_en_hist is
    the English pull of 2022-06..2024-12 with the same two query groups (lines carry `_g`
    only, so the collector is named by the table)."""
    hist = table == "x_raw_en_hist"
    df = pd.read_sql(text(f"""
        SELECT t->>'id' AS post_id, t->>'createdAt' AS created_at, t->>'lang' AS lang,
               CASE WHEN (t->>'isReply')::boolean OR (t->>'inReplyToId') IS NOT NULL THEN 'reply'
                    WHEN jsonb_typeof(t->'quoted_tweet') = 'object' THEN 'quote'
                    ELSE 'original' END AS post_type,
               t->>'text' AS text,
               a->>'id' AS user_id, a->>'name' AS user_name, a->>'userName' AS alias,
               COALESCE((a->>'isVerified')::boolean, FALSE)
                 OR COALESCE((a->>'isBlueVerified')::boolean, FALSE) AS verified,
               NULLIF(a->>'verifiedType', '') AS verified_type,
               (a->>'followers')::bigint AS followers,
               COALESCE(a->'profile_bio'->>'description', a->>'description', '') AS bio,
               COALESCE(a->>'location', '') AS user_location,
               CASE WHEN line ? '_kw' THEN 'twitterapi_es'
                    WHEN line ? '_tier' THEN 'twitterapi_fr'
                    ELSE 'twitterapi_en' END AS collector,
               COALESCE(line->>'_kw', line->>'_g') AS term,
               line->>'_tier' AS tier, line->>'_day' AS query_day
        FROM {table}, LATERAL (SELECT line->'t' AS t) j, LATERAL (SELECT t->'author' AS a) u"""), cn)
    if hist:
        if not (df["collector"] == "twitterapi_en").all():
            raise RuntimeError("x_raw_en_hist holds lines that are not the English pull")
        df["collector"] = "twitterapi_en_hist"
    if table == "x_raw_repair":
        # the repair re-ran single (query, day) pairs of every pull; an English line belongs
        # to the 2022-06..2024-12 pull when its query day is before 2025-01-01
        if df["query_day"].isna().any():
            raise RuntimeError("x_raw_repair holds lines without a query day")
        early = (df["collector"] == "twitterapi_en") & (df["query_day"] < HIST_END)
        df.loc[early, "collector"] = "twitterapi_en_hist"
    return pd.DataFrame({
        "platform": "x", "collector": df["collector"],
        "source": df["tier"].fillna("twitterapi"), "term": df["term"], "post_id": df["post_id"],
        "created_at": pd.to_datetime(df["created_at"], format="%a %b %d %H:%M:%S %z %Y",
                                     errors="coerce", utc=True),
        "lang_platform": df["lang"], "post_type": df["post_type"], "text": df["text"],
        "author_id": df["user_id"], "author_name": df["user_name"], "author_handle": df["alias"],
        "owner_type": None, "surface_type": None, "verified": df["verified"],
        "verified_type": df["verified_type"], "followers": df["followers"],
        "bio": df["bio"], "user_location": df["user_location"],
    })


def raw() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with local_engine().connect() as cn:
        df = pd.concat([_meta_raw(), _x_json_raw(cn, "x_raw_en_hist"), _x_json_raw(cn),
                        _x_json_raw(cn, "x_raw_repair")], ignore_index=True)[COLS]
    df["text"] = df["text"].fillna("")
    types = {"original", "reply", "quote", "reshare"}
    df["raw_ok"] = (df["created_at"].notna() & df["post_type"].isin(types)
                    & df["post_id"].fillna("").str.fullmatch(r"\d+"))
    df["verified"] = df["verified"].astype("boolean")
    df["followers"] = df["followers"].astype("Int64")
    df.to_parquet(OUT / "raw_rows.parquet", index=False)
    print(df.groupby(["platform", "collector"]).agg(rows=("post_id", "size"),
                                                    malformed=("raw_ok", lambda s: (~s).sum())))


# ---------------------------------------------------------------- step 1b: dedup
def dedup() -> None:
    df = pd.read_parquet(OUT / "raw_rows.parquet")
    df = df[df["raw_ok"]].drop(columns="raw_ok")
    df["c_rank"] = df["collector"].map({c: i for i, c in enumerate(COLLECTOR_ORDER)})
    # fixed tie-break: collector order, then search term, then a hash of the text
    df["h"] = [hashlib.md5(t.encode("utf-8", "replace")).hexdigest() for t in df["text"]]
    df = df.sort_values(["platform", "post_id", "c_rank", "term", "h"], kind="stable")
    g = df.groupby(["platform", "post_id"], sort=False)
    agg = g.agg(terms=("term", lambda s: "|".join(sorted(set(s.dropna())))),
                collectors=("collector", lambda s: "|".join(sorted(set(s)))),
                n_rows=("term", "size")).reset_index()
    posts = (df.drop_duplicates(["platform", "post_id"]).drop(columns=["c_rank", "h", "term"])
             .merge(agg, on=["platform", "post_id"]))
    posts.to_parquet(OUT / "posts.parquet", index=False)
    print(posts.groupby(["platform", "collector"]).agg(posts=("post_id", "size"),
                                                       rows=("n_rows", "sum")))
    print("posts found by more than one collector:",
          int(posts["collectors"].str.contains(r"\|").sum()))


# ---------------------------------------------------------------- step 2: language
_STRIP = re.compile(r"https?://\S+|www\.\S+|[@#]\w+|[^\w\s'’-]|\d+", re.UNICODE)


def clean_for_lang(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", _STRIP.sub(" ", s)).strip()


def lang() -> None:
    from lingua import Language, LanguageDetectorBuilder

    L = Language
    langs = [L.ENGLISH, L.SPANISH, L.FRENCH, L.PORTUGUESE, L.ITALIAN, L.GERMAN, L.DUTCH,
             L.CATALAN, L.TAGALOG, L.INDONESIAN, L.RUSSIAN, L.ARABIC, L.TURKISH, L.JAPANESE,
             L.CHINESE, L.HINDI, L.POLISH]
    det = LanguageDetectorBuilder.from_languages(*langs).build()
    posts = pd.read_parquet(OUT / "posts.parquet", columns=["platform", "post_id", "text",
                                                            "lang_platform"])
    cleaned = posts["text"].map(clean_for_lang)
    letters = cleaned.str.count(r"[^\W\d_]")
    long = letters >= MIN_LETTERS
    res = det.detect_languages_in_parallel_of(cleaned[long].tolist())
    posts["lang_detected"] = posts["lang_platform"].str.lower()
    posts.loc[long, "lang_detected"] = [r.iso_code_639_1.name.lower() if r else "und"
                                        for r in res]
    posts["lang_source"] = long.map({True: "detector", False: "platform_tag_short_text"})
    posts[["platform", "post_id", "lang_detected", "lang_source"]].to_parquet(
        OUT / "lang.parquet", index=False)
    posts["kept"] = posts["lang_detected"].isin(LANGS_KEPT)
    print(posts.groupby(["platform", "lang_source"]).agg(n=("post_id", "size"),
                                                         kept=("kept", "sum")))
    print(pd.crosstab([posts["platform"], posts["lang_platform"].where(
        posts["lang_platform"].isin(["en", "es", "fr"]), "other")], posts["lang_detected"].where(
        posts["lang_detected"].isin(["en", "es", "fr", "pt", "und"]), "other")))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["raw", "dedup", "lang"])
    {"raw": raw, "dedup": dedup, "lang": lang}[ap.parse_args().step]()
