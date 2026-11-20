"""Score every state-extraction method against the labelled sample.

Reads labels.csv (the ground truth) and every results CSV in the same folder
(gemma3_27b_v2.csv, stanza_ner.csv, ...), and adds the keyword gazetteer as a
reference row: its answer is simply the states it matched, with no main state,
because a multi-state post is exactly what it cannot resolve.

Measures, per method and per language:
  main state   share of posts whose main state matches the label exactly
               (posts with no state count as a match when both are blank)
  exact list   share whose full state list matches exactly
  precision    of the states the method returned, the share that are right
  recall       of the labelled states, the share the method found
  event        agreement on us_event (blank for methods that do not answer it)

Labels are matched on the row column, not post_id: Excel rounds 16-digit IDs.
Colab cell 8 in docs/Geolocation_Extraction.docx runs this same code.

    uv run python -m scripts.score_state_methods                      # data/llm_pilot/
    uv run python -m scripts.score_state_methods --high-confidence    # skip 'low' labels
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SKIP = {"sample.csv", "labels.csv"}


def _set(s) -> frozenset:
    return frozenset(x.strip().upper() for x in str(s or "").split(",") if x.strip())


def score(folder: Path, high_confidence: bool = False) -> pd.DataFrame:
    lab = pd.read_csv(folder / "labels.csv", dtype=str, keep_default_na=False)
    lab["row"] = lab["row"].str.strip()
    lab = lab[lab.human_event.str.strip() != ""]
    if high_confidence and "confidence" in lab.columns:
        lab = lab[lab.confidence != "low"]

    methods = {"keyword_gazetteer": lab.assign(llm_states=lab.geo_matched, llm_main="",
                                               llm_event="")}
    for f in sorted(folder.glob("*.csv")):
        if f.name in SKIP:
            continue
        m = pd.read_csv(f, dtype=str, keep_default_na=False)
        if "llm_main" not in m.columns:
            print(f"{f.stem}: no main state (made before prompt v2), skipped")
            continue
        m = (m.drop(columns=["lang", "geo_matched", "post_text"], errors="ignore")
              .rename(columns={"post_id": "res_post_id"}).merge(lab, on="row"))
        # Rows must be the same posts. Skip the check where Excel has mangled post_id.
        if "res_post_id" in m.columns:
            clean = m.post_id.str.fullmatch(r"\d+")
            bad = clean & (m.res_post_id != m.post_id)
            if bad.any():
                print(f"{f.stem}: {bad.sum()} rows are different posts from labels.csv "
                      f"(sample drawn differently?) -- left out")
                m = m[~bad]
        methods[f.stem] = m

    out = []
    for name, m in methods.items():
        for lang, g in [("all", m)] + sorted(m.groupby("lang")):
            truth, pred = g.human_states.map(_set), g.llm_states.map(_set)
            tp = sum(len(t & p) for t, p in zip(truth, pred))
            n_pred, n_true = sum(map(len, pred)), sum(map(len, truth))
            answered = g.llm_event.str.strip() != ""
            ev = ((g.llm_event[answered].str.lower().map({"true": "1", "false": "0"})
                   == g.human_event[answered].str.strip()).mean() if answered.any() else None)
            out.append({
                "method": name, "lang": lang, "n": len(g),
                "main state": (g.llm_main.str.upper().str.strip()
                               == g.human_main.str.upper().str.strip()).mean(),
                "exact list": (truth == pred).mean(),
                "precision": tp / n_pred if n_pred else None,
                "recall": tp / n_true if n_true else None,
                "event": ev,
            })
    return pd.DataFrame(out)


def show(df: pd.DataFrame) -> None:
    pct = lambda v: "   -  " if v is None or pd.isna(v) else f"{v:6.1%}"
    print(f"{'method':22} {'lang':4} {'n':>4}  {'main state':>10} {'exact list':>10} "
          f"{'precision':>9} {'recall':>7} {'event':>7}")
    for r in df.itertuples():
        print(f"{r.method:22} {r.lang:4} {r.n:>4}  {pct(r[4]):>10} {pct(r[5]):>10} "
              f"{pct(r.precision):>9} {pct(r.recall):>7} {pct(r.event):>7}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="data/llm_pilot")
    ap.add_argument("--high-confidence", action="store_true",
                    help="score only labels not marked confidence=low")
    args = ap.parse_args()
    show(score(Path(args.folder), args.high_confidence))


if __name__ == "__main__":
    main()
