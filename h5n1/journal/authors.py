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


TYPES = {"organisation", "individual"}
INSTRUCTIONS = """# Author-type reader instructions

You are labelling X accounts as organisation or individual. Do not spawn any subagents. You are
given READ (A, B or ADJ) and BATCH (a number n).

1. Read the guide in full first:
   `C:\\Users\\NickS\\Desktop\\Harvard\\Capstone\\docs\\journal_rebuild\\GUIDE_author.md`
2. Your batch file (one account per line, JSON):
   READ A or B: `{d}\\batch_<n>.jsonl`
   READ ADJ:    `{d}\\adj\\batch_<n>.jsonl` (each line also carries `read_1` and `read_2`, two
   readers' answers in shuffled order; they disagree, so decide yourself from the guide)
3. Your own scratch folder (create it; use ONLY this folder for scratch files):
   `{d}\\<READ>\\tmp_<n>`  (for ADJ: `{d}\\adj\\tmp_<n>`)
4. Write your output CSV, header exactly `id,author_type`, to:
   `{d}\\<READ>\\labels_<n>.csv`  (for ADJ: `{d}\\adj\\labels_<n>.csv`)
5. Then run the checker from `C:\\Users\\NickS\\Desktop\\Harvard\\Capstone` and fix anything it
   reports until it prints OK:
   `uv run python -m h5n1.journal.authors check <your labels csv> <your batch jsonl>`

Rules: read every account yourself with the Read tool and decide by applying the guide; do not
classify with a keyword or regex script (a script may only write out answers you decided). Do
not read any other reader's folder or output.

Final reply, nothing else: the single word `OK` if the checker printed OK, otherwise one line
saying what failed.
"""


def instructions() -> None:
    (LAB / "READER_INSTRUCTIONS.md").write_text(INSTRUCTIONS.format(d=str(LAB)), encoding="utf-8")
    print(LAB / "READER_INSTRUCTIONS.md")


def check(labels_csv: str, batch_jsonl: str, quiet: bool = False) -> int:
    ids = [json.loads(line)["id"] for line in open(batch_jsonl, encoding="utf-8")]
    x = pd.read_csv(labels_csv, dtype=str, keep_default_na=False)
    errs = []
    if list(x.columns) != ["id", "author_type"]:
        errs.append("header must be exactly id,author_type")
    elif len(x) != len(ids) or set(x["id"]) != set(ids) or x["id"].duplicated().any():
        errs.append(f"{len(x)} rows; ids do not match the {len(ids)} in the batch")
    elif not x["author_type"].isin(TYPES).all():
        errs.append("author_type must be organisation or individual")
    if not quiet:
        print("OK" if not errs else "PROBLEM: " + "; ".join(errs))
    return 1 if errs else 0


def _valid(folder, batch_dir) -> tuple[list[int], list[int]]:
    have = sorted(int(f.stem.split("_")[1]) for f in folder.glob("labels_*.csv"))
    bad = [i for i in have if check(str(folder / f"labels_{i}.csv"),
                                    str(batch_dir / f"batch_{i}.jsonl"), quiet=True)]
    return [i for i in have if i not in bad], bad


def status() -> None:
    n = len(list(LAB.glob("batch_*.jsonl")))
    for sub in ("A", "B"):
        ok, bad = _valid(LAB / sub, LAB)
        todo = [i for i in range(n) if i not in set(ok)]
        print(f"read {sub}: {len(ok)} of {n} valid; failing the checker: {bad}; next to do: {todo[:12]}")
    if (LAB / "adj" / "manifest.csv").exists():
        na = len(list((LAB / "adj").glob("batch_*.jsonl")))
        ok, bad = _valid(LAB / "adj", LAB / "adj")
        print(f"adjudication: {len(ok)} of {na} valid; failing: {bad}")


def _reads() -> tuple[pd.Series, pd.Series]:
    rd = lambda sub: pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False)  # noqa: E731
                                for f in sorted((LAB / sub).glob("labels_*.csv"))]).set_index("id")["author_type"]
    a, b = rd("A"), rd("B")
    ids = a.index.intersection(b.index)
    return a.loc[ids], b.loc[ids]


def agree() -> None:
    from sklearn.metrics import cohen_kappa_score

    a, b = _reads()
    print(f"{len(a):,} accounts read twice; agreement {(a == b).mean():.3f}, "
          f"kappa {cohen_kappa_score(a, b):.3f}; differ {int((a != b).sum()):,}")
    print(pd.crosstab(a.rename("read A"), b.rename("read B")).to_string())


def adjbatches(seed: int = 20261118, size: int = 250) -> None:
    """Fix the adjudication batches: every account the two reads label differently, with both
    answers in a per-account shuffled order. Refuses to overwrite; run only when both reads
    are complete."""
    import numpy as np

    d = LAB / "adj"
    if (d / "manifest.csv").exists():
        raise SystemExit(f"{d} already has a manifest; batches are fixed and are not rebuilt")
    a, b = _reads()
    need = pd.read_csv(LAB / "manifest.csv", dtype=str)
    if len(a) != len(need):
        raise SystemExit(f"only {len(a):,} of {len(need):,} accounts have both reads")
    acc = {}
    for f in LAB.glob("batch_*.jsonl"):
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            acc[r["id"]] = r
    ids = sorted(a.index[a != b])
    rng = np.random.default_rng(seed)
    order, a_first = rng.permutation(len(ids)), rng.random(len(ids)) < 0.5
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for k, j in enumerate(order):
        i = ids[j]
        r1, r2 = (a[i], b[i]) if a_first[j] else (b[i], a[i])
        rows.append({"batch": k // size, "id": i, "read_1": "A" if a_first[j] else "B",
                     "line": json.dumps({**acc[i], "read_1": r1, "read_2": r2}, ensure_ascii=False)})
    m = pd.DataFrame(rows)
    for n, g in m.groupby("batch"):
        (d / f"batch_{n}.jsonl").write_text("\n".join(g["line"]) + "\n", encoding="utf-8")
    m[["batch", "id"]].to_csv(d / "manifest.csv", index=False)
    m[["id", "read_1"]].to_csv(LAB / "adj_key.csv", index=False)
    print(f"{len(m):,} accounts in {m['batch'].nunique()} adjudication batches under {d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["batches", "instructions", "status", "check", "agree", "adjbatches"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    if a.step == "check":
        raise SystemExit(check(*a.args))
    {"batches": batches, "instructions": instructions, "status": status, "agree": agree,
     "adjbatches": adjbatches}[a.step]()
