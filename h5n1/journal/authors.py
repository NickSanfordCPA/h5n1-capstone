"""Author type (organisation / individual), protocol section 6.3.

    uv run python -m h5n1.journal.authors batches     # X accounts that need a reader
    uv run python -m h5n1.journal.authors status

Meta: from post_owner.type, no reader (Page -> organisation, profile -> individual).
X:    verifiedType Business or Government -> organisation; every other reader-set author is
      labelled by Sonnet readers from the display name, handle and bio (guide:
      docs/journal_rebuild/GUIDE_author.md), twice, with Opus adjudication.

An author's name, handle and bio are taken from their latest reader-set post. Readers see no
posts and no outcomes.
"""
from __future__ import annotations

import argparse
import json
import re

import pandas as pd

from h5n1.journal.localdb import DATA_DIR
from h5n1.journal.posts import OUT

LAB = DATA_DIR / "labels" / "authors"
BATCH = 500
ORG_VERIFIED = {"Business", "Government"}


def x_authors() -> pd.DataFrame:
    p = pd.read_parquet(OUT / "funnel.parquet", columns=[
        "platform", "author_id", "author_name", "author_handle", "bio", "verified_type",
        "created_at", "reader_set"])
    p = p[p["reader_set"] & (p["platform"] == "x")].sort_values("created_at")
    a = p.groupby("author_id").agg(name=("author_name", "last"), handle=("author_handle", "last"),
                                   bio=("bio", "last"), verified_type=("verified_type", "last"),
                                   n_posts=("created_at", "size")).reset_index()
    a["from_metadata"] = a["verified_type"].isin(ORG_VERIFIED)
    return a


def batches(seed: int = 20261117) -> None:
    if (LAB / "manifest.csv").exists():
        raise SystemExit(f"{LAB} already has a manifest; batches are fixed and are not rebuilt")
    for sub in ("A", "B"):
        (LAB / sub).mkdir(parents=True, exist_ok=True)
    a = x_authors()
    need = a[~a["from_metadata"]].sample(frac=1, random_state=seed).reset_index(drop=True)
    need["batch"] = need.index // BATCH
    for n, g in need.groupby("batch"):
        with open(LAB / f"batch_{n}.jsonl", "w", encoding="utf-8") as f:
            for r in g.itertuples(index=False):
                bio = re.sub(r"\s+", " ", r.bio or "")[:300]
                f.write(json.dumps({"id": f"x{r.author_id}", "name": (r.name or "")[:80],
                                    "handle": r.handle or "", "bio": bio},
                                   ensure_ascii=False) + "\n")
    need[["batch", "author_id"]].to_csv(LAB / "manifest.csv", index=False)
    print(f"X reader-set authors {len(a):,}; organisation from verifiedType "
          f"{int(a['from_metadata'].sum()):,}; to readers {len(need):,} in "
          f"{need['batch'].nunique()} batches of {BATCH}")


def status() -> None:
    n = len(list(LAB.glob("batch_*.jsonl")))
    for sub in ("A", "B"):
        done = sorted(int(f.stem.split("_")[1]) for f in (LAB / sub).glob("labels_*.csv"))
        todo = [i for i in range(n) if i not in set(done)]
        print(f"read {sub}: {len(done)} of {n} done; next to do: {todo[:12]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["batches", "status"])
    {"batches": batches, "status": status}[ap.parse_args().step]()
