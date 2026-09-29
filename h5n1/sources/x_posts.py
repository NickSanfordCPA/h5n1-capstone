"""X (Twitter) keyword-search CSVs -> deduplicated, geocoded, flagged post and author tables.

EXPLORATORY, LOCAL ONLY. Input is the prior cohort's scraper export (one CSV per search
term, data/X/*.csv); Muthu is re-pulling with better terms, and this module is meant to
run unchanged on that pull. Outputs are parquet under data/X_derived/ (gitignored) --
nothing goes to GCS or Postgres until the repull lands and the design is settled.

    uv run python -m h5n1.sources.x_posts build          # posts.parquet, authors.parquet
    uv run python -m h5n1.sources.x_posts accounts       # account_type + hub -> authors.parquet
    uv run python -m h5n1.sources.x_posts subset         # topic_subset.parquet
    uv run --extra ml python -m h5n1.sources.x_posts sentiment [--universe all]  # resumable

GRAIN. Geography is the author's profile location (see x_location): STATE is the modeling
grain. A post found by several search terms is one post; `terms` keeps them all.

WHAT IS FLAGGED, NOT DROPPED (the filters are applied downstream, so each is auditable):
  copypasta   the post's normalized 80-char prefix is shared by >= COPYPASTA_MIN posts
              from >= 2 authors -- coordinated campaigns and templated bots. The earliest
              post of each group is kept as the representative (copypasta_first).
  train-window author volume, from which the hub rule is applied once account types exist.

ACCOUNT TYPE (step `accounts`). Four groups: farmer_keeper, ag_institution, media_aggregator,
general_public. Authors whose bio has a farm/ag/animal-health term, plus every top-1% author,
were classified by an LLM reading the bio in-session (labels/bio_labels_*.csv; no API). Every
other author gets a regex fallback: media_aggregator if the bio has a strong news term, else
general_public.

HUB RULE -- fixed 2026-09-28, BEFORE any X feature was built or scored, mirroring the GDELT
media-hub rule (top 1% by train-window volume AND no local stake):
  hub = train-window post count in the top 1% of authors
        AND account_type NOT IN (farmer_keeper, ag_institution)
Hubs are dropped from every signal feature. Media accounts below the volume cut are kept as
their own stream (local outlets relaying a local story may carry signal); they are not
pooled with the public.

TOPIC SUBSET (step `subset`): state-geocoded, not a hub, and not a non-first copypasta post.
"""
from __future__ import annotations

import argparse
import pathlib
import re

import pandas as pd

from h5n1.sources.x_location import geocode_series

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "X"
OUT_DIR = ROOT / "data" / "X_derived"
TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC")  # SPLIT in regression_baseline / gdelt_lift
COPYPASTA_MIN = 5
KEEP = ["_id", "userId", "userName", "alias", "bio", "followers", "verified", "userLocation", "lang",
        "type", "text", "createdAt", "totalRetweets", "favorites", "replies", "impressions", "url"]


def _term(path: pathlib.Path) -> str:
    m = re.match(r"_(.+?)_ ", path.name)
    return m.group(1) if m else path.stem


def load_raw() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW_DIR.glob("*.csv")):
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        frames.append(df[[c for c in KEEP if c in df.columns]].assign(term=_term(f)))
    raw = pd.concat(frames, ignore_index=True)
    raw["ts"] = pd.to_datetime(raw.createdAt, errors="coerce", utc=True)
    bad = raw.ts.isna() | ~raw["type"].isin(["original", "reply", "quote"])
    print(f"dropping {bad.sum()} malformed rows (unparseable date or type) of {len(raw)}")
    return raw[~bad]


def build() -> None:
    raw = load_raw()
    terms = raw.groupby("_id").term.agg(lambda t: "|".join(sorted(set(t))))
    posts = raw.drop_duplicates("_id").drop(columns="term").merge(terms.rename("terms"), on="_id")
    posts = posts.join(geocode_series(posts.userLocation))

    key = (posts.text.str.lower().str.replace(r"https?://\S+|@\w+|\d+", "", regex=True)
           .str.replace(r"\W+", " ", regex=True).str.strip().str[:80])
    grp = posts.assign(key=key).groupby("key")
    n_posts, n_auth = grp._id.transform("size"), grp.userId.transform("nunique")
    posts["copypasta"] = (n_posts >= COPYPASTA_MIN) & (n_auth >= 2) & (key.str.len() >= 20)
    first = posts.assign(key=key).sort_values("ts").drop_duplicates("key")._id
    posts["copypasta_first"] = posts.copypasta & posts._id.isin(first)

    tr = posts[posts.ts < TRAIN_END]
    authors = (posts.sort_values("ts").groupby("userId")
               .agg(userName=("userName", "last"), bio=("bio", "last"), userLocation=("userLocation", "last"),
                    followers=("followers", "last"), verified=("verified", "last"),
                    n_posts=("_id", "size"), state=("state", "last"), geo_level=("geo_level", "last")))
    authors["n_posts_train"] = tr.groupby("userId").size().reindex(authors.index).fillna(0).astype(int)
    authors["volume_pctile"] = authors.n_posts_train.rank(pct=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    posts.to_parquet(OUT_DIR / "posts.parquet", index=False)
    authors.reset_index().to_parquet(OUT_DIR / "authors.parquet", index=False)
    print(f"posts {len(posts):,}  authors {len(authors):,}  "
          f"state-geocoded {posts.state.notna().mean():.3f}  copypasta {posts.copypasta.mean():.3f}")


def sentiment(universe: str = "subset", batch: int = 64, chunk: int = 5_000) -> None:
    """Score posts with the English TweetEval model. Chunked + resumable: each chunk is its
    own parquet, and finished chunks are skipped on restart.

    universe="subset" scores topic_subset.parquet (the analysis universe, ~58k posts, ~1 h on
    this CPU); "all" scores every post (~364k, ~6 h -- a Modal job if ever needed). fp32 only:
    int8 dynamic quantization was 2.3x faster but flipped 22% of argmax labels (2026-09-28).
    12 threads measured fastest on the Core Ultra 7 155H (6 -> 12/s, 12 -> 18/s, 16 -> 18/s)."""
    import torch

    from h5n1.sentiment.core import ENGLISH_MODEL, load_model, score_probs

    torch.set_num_threads(12)
    src = "topic_subset.parquet" if universe == "subset" else "posts.parquet"
    posts = pd.read_parquet(OUT_DIR / src, columns=["_id", "text"]).sort_values("_id")
    part_dir = OUT_DIR / f"sentiment_parts_{universe}"
    part_dir.mkdir(parents=True, exist_ok=True)
    # a local copy (curl-downloaded) sidesteps HF Hub connection resets seen 2026-09-28
    local = ROOT / "data" / "reference" / "models" / ENGLISH_MODEL.split("/")[1]
    model = load_model(str(local) if (local / "pytorch_model.bin").exists() else ENGLISH_MODEL)
    for start in range(0, len(posts), chunk):
        dest = part_dir / f"part_{start:07d}.parquet"
        if dest.exists():
            continue
        sub = posts.iloc[start:start + chunk]
        # length-sorted batches: far less padding, same results
        order = sub.text.str.len().sort_values().index
        rows = []
        texts = sub.loc[order, "text"].tolist()
        for i in range(0, len(texts), batch):
            rows.extend(score_probs(texts[i:i + batch], model))
        out = pd.DataFrame(rows, columns=["p_neg", "p_neu", "p_pos"], index=order)
        out.insert(0, "_id", sub.loc[order, "_id"].values)
        out["sentiment"] = out.p_pos - out.p_neg
        out.to_parquet(dest, index=False)
        print(f"chunk {start:,}-{start + len(sub):,} done", flush=True)
    parts = pd.concat(pd.read_parquet(p) for p in sorted(part_dir.glob("part_*.parquet")))
    parts.assign(model=ENGLISH_MODEL).to_parquet(OUT_DIR / f"sentiment_{universe}.parquet", index=False)
    print(f"sentiment rows {len(parts):,}  mean {parts.sentiment.mean():+.3f}")


ACCOUNT_TYPES = ["farmer_keeper", "ag_institution", "media_aggregator", "general_public"]
MEDIA_FALLBACK = re.compile(r"\b(?:news|newspaper|journalist|reporter|editor|correspondent|anchor|magazine|"
                            r"radio|television|broadcast\w*|headlines|breaking news|alerts?|tracker|newsletter|"
                            r"press)\b", re.I)


def accounts() -> None:
    authors = pd.read_parquet(OUT_DIR / "authors.parquet")
    labels = pd.concat(pd.read_csv(f, dtype=str) for f in sorted((OUT_DIR / "labels").glob("bio_labels_*.csv")))
    labels["account_type"] = labels.account_type.str.strip()
    bad = ~labels.account_type.isin(ACCOUNT_TYPES)
    if bad.any():
        raise ValueError(f"unknown account types: {labels.account_type[bad].unique()}")
    labels = labels.drop_duplicates("userId").set_index("userId").account_type
    authors["type_source"] = authors.userId.isin(labels.index).map({True: "llm_bio", False: "regex"})
    fallback = authors.bio.fillna("").str.contains(MEDIA_FALLBACK).map(
        {True: "media_aggregator", False: "general_public"})
    authors["account_type"] = authors.userId.map(labels).fillna(fallback)
    authors["hub"] = (authors.volume_pctile >= 0.99) & ~authors.account_type.isin(
        ["farmer_keeper", "ag_institution"])
    authors.to_parquet(OUT_DIR / "authors.parquet", index=False)
    print(authors.groupby(["account_type", "type_source"]).size().unstack(fill_value=0))
    print(f"hubs: {authors.hub.sum()} authors, {authors.loc[authors.hub, 'n_posts'].sum():,} posts")


def subset() -> None:
    posts = pd.read_parquet(OUT_DIR / "posts.parquet")
    authors = pd.read_parquet(OUT_DIR / "authors.parquet", columns=["userId", "account_type", "hub"])
    posts = posts.merge(authors, on="userId", how="left")
    keep = posts.state.notna() & ~posts.hub & (~posts.copypasta | posts.copypasta_first)
    sub = posts[keep]
    sub.to_parquet(OUT_DIR / "topic_subset.parquet", index=False)
    print(f"topic subset {len(sub):,} posts of {len(posts):,}")
    print(sub.account_type.value_counts().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "sentiment", "accounts", "subset"])
    ap.add_argument("--universe", choices=["subset", "all"], default="subset")
    a = ap.parse_args()
    if a.step == "sentiment":
        sentiment(a.universe)
    else:
        {"build": build, "accounts": accounts, "subset": subset}[a.step]()
