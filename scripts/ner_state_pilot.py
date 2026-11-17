"""Stanford NER baseline for the multi-state question: which US states is a post about?

WHY. The LLM test (scripts/llm_state_pilot.py and the Colab cells in
docs/Geolocation_Extraction.docx) needs a non-LLM method to beat. This is the standard one:
a trained named-entity recogniser finds the PLACE names in the text, and each place
name that is exactly a US state is kept.

WHAT IT CAN AND CANNOT DO.
  * It reads grammar, not a word list, so "Lake Michigan", "Kansas City" and
    "Washington County" come back as whole place names that are not states.
  * It cannot judge whether an event happened there, or whether "California" in
    "California condor" names a place. It has no us_event answer.
  * Cities are not mapped to states (no city list), so "Portland" counts for nothing.

The model is Stanza, the Stanford NLP group's Python toolkit: English uses its
OntoNotes model (place types GPE and LOC), Spanish its AnCora model (type LOC).
It runs locally; no text leaves the machine. Models download once (~0.5 GB) to
~/stanza_resources.

Output has the same columns as the LLM results, so the same scoring code reads both:
llm_states / llm_main hold this method's answer, llm_event is blank.

    uv run --with stanza python -m scripts.ner_state_pilot          # data/llm_pilot/
    uv run --with stanza python -m scripts.ner_state_pilot --out /content/drive/MyDrive/h5n1_llm_pilot
"""
from __future__ import annotations

import argparse
import re
import time
from collections import Counter
from pathlib import Path

import pandas as pd

from h5n1.sources.meta_location import FRENCH_STATE, SPANISH_STATE, STATE_NAME

ALL_NAMES = {**STATE_NAME, **{k: v for k, v in SPANISH_STATE.items() if v}, **FRENCH_STATE}
PLACE_TYPES = {"GPE", "LOC"}

# Wrappers around a state name that still mean the state: "the State of Texas",
# "el estado de Texas", "Washington state", "New York State".
_PREFIX = re.compile(r"^(the\s+)?(state\s+of|estado\s+de|el\s+estado\s+de|[ée]tat\s+de)\s+", re.I)
_SUFFIX = re.compile(r"\s+state$", re.I)


def to_state(entity: str) -> str | None:
    """'Texas' / 'Nueva York' / 'Washington state' -> code. Anything else -> None."""
    t = _SUFFIX.sub("", _PREFIX.sub("", entity.strip(" .,;:()\"'"))).lower()
    if t in ("washington d.c", "washington, d.c", "washington dc", "d.c", "dc"):
        return "DC"
    return ALL_NAMES.get(t)


def label(nlp, text: str) -> tuple[list[str], str, str]:
    """(states, main state, the place entities found) for one post."""
    doc = nlp(text)
    places = [e.text for e in doc.ents if e.type in PLACE_TYPES]
    hits = [s for s in map(to_state, places) if s]
    if not hits:
        return [], "", "; ".join(places[:15])
    counts = Counter(hits)
    top = max(counts.values())
    main = next(s for s in hits if counts[s] == top)   # most mentioned; ties -> first
    return sorted(counts), main, "; ".join(places[:15])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/llm_pilot", help="folder holding sample.csv")
    args = ap.parse_args()

    import stanza
    out = Path(args.out)
    sample = pd.read_csv(out / "sample.csv", dtype=str, keep_default_na=False)
    pipes = {}
    for lang in sorted(sample.lang.unique()):
        stanza.download(lang, processors="tokenize,ner", verbose=False)
        pipes[lang] = stanza.Pipeline(lang, processors="tokenize,ner", verbose=False)

    rows = []
    for i, p in enumerate(sample.itertuples(), 1):
        t0 = time.time()
        try:
            states, main_state, places = label(pipes[p.lang], p.post_text)
            err = ""
        except Exception as e:
            states, main_state, places, err = [], "", "", str(e)[:200]
        rows.append({"row": p.row, "post_id": p.post_id, "lang": p.lang,
                     "geo_matched": p.geo_matched, "post_text": p.post_text,
                     "llm_states": ",".join(states), "llm_main": main_state, "llm_event": "",
                     "evidence": places, "seconds": round(time.time() - t0, 1), "error": err})
        if i % 25 == 0 or i == len(sample):
            print(f"  {i}/{len(sample)}")

    res = pd.DataFrame(rows)
    path = out / "stanza_ner.csv"
    res.to_csv(path, index=False)
    print(f"wrote {path} | errors {(res.error != '').sum()} | median {res.seconds.median()}s per post")


if __name__ == "__main__":
    main()
