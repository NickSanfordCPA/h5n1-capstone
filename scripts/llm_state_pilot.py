"""Test local LLMs on one question: which US states does this post report bird flu in?

WHY. The keyword extractor (h5n1.sources.meta_location) finds every state a post
NAMES. It cannot tell which ones the post is ABOUT. "Wisconsin officials found avian
flu in ducks along Lake Michigan" names two states and is about one. So the 4,611
T5_multi posts are all discarded, though many carry a clear signal -- and some name
two states because two things really happened ("first cases in dairy cattle in Texas
and Kansas"). An LLM reading the sentence can separate these.

WHY LOCAL. The models run through Ollama on this machine. No post text leaves it.

WHAT IT WRITES. CSVs under data/llm_pilot/ (git-ignored), never the database:
    sample.csv      the posts, with blank human_states / human_event columns to fill in
    <model>.csv     one file per model tested, same posts, same order
Every model sees the same sample, so their answers can be compared post by post.

    ollama serve                                         # terminal A
    ollama pull qwen3:8b                                 # once per model
    uv run python -m scripts.llm_state_pilot --model qwen3:8b
    uv run python -m scripts.llm_state_pilot --model gemma3:12b
    # fill human_states ("WI", "KS,TX" or blank), human_main (one code) and human_event (1/0)
    uv run python -m scripts.llm_state_pilot --score    # = scripts/score_state_methods.py
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import text

from h5n1.db import get_engine
from h5n1.sources.meta_location import SPANISH_STATE, STATE_NAME

OUT = Path("data/llm_pilot")
PROMPT_VERSION = "v2"     # v2 adds main_state; bump it whenever SYSTEM changes
OLLAMA = "http://localhost:11434/api/chat"
MAX_CHARS = 2000      # long posts are mostly link lists; the claim is near the top

CODES = sorted(set(STATE_NAME.values()))

# The reference list the model is given: code, English name, Spanish name where
# it differs. Built from the extractor's own tables so the two never disagree.
_es = {}
for name, code in SPANISH_STATE.items():
    if code:
        _es.setdefault(code, []).append(name)
REFERENCE = "\n".join(
    f"{code} = {en.title()}" + (f" (es: {', '.join(n.title() for n in _es[code])})"
                                if code in _es else "")
    for en, code in sorted(STATE_NAME.items(), key=lambda kv: kv[1]))

SYSTEM = f"""You read social-media posts about avian influenza (bird flu). Posts are in English or Spanish.

Task: find the US states where the post reports a bird-flu EVENT. An event is a detection in birds or animals, a human case, an outbreak, a cull, a quarantine, or a state's own response such as an emergency declaration.

Rules:
- Use only codes from the reference list below.
- A post can report events in several states. List every one.
- Do NOT count a state that is only part of another place's name: Lake Michigan, Kansas City, Washington County (Oregon), Georgia del Sur / South Georgia, Baja California, Sierra Nevada, La Florida (Chile).
- Do NOT count a state name that is part of the name of a species, breed, product, company or publication: California condor, Carolina wren, Mississippi kite, Rhode Island Red (a chicken breed), Jersey cows, Kentucky Fried Chicken, The New York Times, The Washington Post. Place the event where it happened: a California condor that died in Arizona is an Arizona event.
- Do NOT count "Washington" when it means the US federal government.
- Do NOT count a state named only for contrast, history or comparison ("unlike Iowa, we have no cases").
- Places outside the US are not states. If the event is outside the US, return no states.
- If the post is about the US as a whole with no specific state, return no states.

states: every US state with an event, or [] if none.
main_state: the ONE state the post is mainly about -- where its main event happened. If several states are covered equally, the one the post names first. It must be one of the codes in states. Use "" when states is empty.
us_event: true if the post reports a specific bird-flu event inside the US, else false.
evidence: the shortest phrase from the post (at most 15 words, original language) that supports your answer, or "" if none.

Reference list of US states:
{REFERENCE}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "states": {"type": "array", "items": {"type": "string", "enum": CODES}},
        "main_state": {"type": "string", "enum": CODES + [""]},
        "us_event": {"type": "boolean"},
        "evidence": {"type": "string"},
    },
    "required": ["states", "main_state", "us_event", "evidence"],
}


def draw_sample(n_per_lang: int, tier: str, seed: str) -> pd.DataFrame:
    """Same seed -> same posts. Ordering by a hash of the id is a stable shuffle."""
    sql = text("""
        SELECT post_id, lang, geo_tier, geo_matched, post_text FROM (
            SELECT *, row_number() OVER (PARTITION BY lang
                                         ORDER BY md5(post_id::text || :seed)) AS r
            FROM meta_post_analysis WHERE geo_tier = :tier) s
        WHERE r <= :n ORDER BY lang, r""")
    return pd.read_sql(sql, get_engine(), params={"tier": tier, "n": n_per_lang, "seed": seed})


def ask(model: str, post: str, hint: str | None, think) -> dict:
    user = post[:MAX_CHARS]
    if hint:
        user += f"\n\n(A keyword search found these state names in the text: {hint}. They may be wrong.)"
    body = {"model": model, "stream": False, "format": SCHEMA, "think": think,
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": user}]}
    r = requests.post(OLLAMA, json=body, timeout=300)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def run(args) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sample_path = OUT / "sample.csv"
    if sample_path.exists():
        sample = pd.read_csv(sample_path, dtype=str, keep_default_na=False)
        print(f"reusing {sample_path} ({len(sample)} posts)")
    else:
        sample = draw_sample(args.n, args.tier, args.seed)
        sample["human_states"] = ""
        sample["human_main"] = ""
        sample["human_event"] = ""
        sample.to_csv(sample_path, index=False)
        print(f"wrote {sample_path} ({len(sample)} posts: {sample.lang.value_counts().to_dict()})")

    think = {"false": False, "true": True}.get(args.think, args.think)  # gpt-oss takes low/medium/high
    rows = []
    for i, p in enumerate(sample.itertuples(), 1):
        t0 = time.time()
        try:
            a = ask(args.model, p.post_text, None if args.no_hint else p.geo_matched, think)
            err = ""
        except Exception as e:   # one bad reply should not end a 200-post run
            a, err = {"states": [], "main_state": "", "us_event": None, "evidence": ""}, str(e)[:200]
        states, main = set(a.get("states", [])), a.get("main_state", "")
        if main:
            states.add(main)    # the main state is always one of the states
        rows.append({"post_id": p.post_id, "lang": p.lang, "geo_matched": p.geo_matched,
                     "post_text": p.post_text,
                     "llm_states": ",".join(sorted(states)), "llm_main": main,
                     "llm_event": a.get("us_event"), "evidence": a.get("evidence", ""),
                     "seconds": round(time.time() - t0, 1), "error": err})
        if i % 10 == 0 or i == len(sample):
            print(f"  {i}/{len(sample)}  last: {rows[-1]['llm_states'] or '-'}  ({rows[-1]['seconds']}s)")

    out = pd.DataFrame(rows)
    path = OUT / f"{args.model.replace(':', '_').replace('/', '_')}_{PROMPT_VERSION}.csv"
    out.to_csv(path, index=False)
    print(f"\nwrote {path}  | errors: {(out.error != '').sum()}  "
          f"| median {out.seconds.median()}s per post")


def _set(s) -> frozenset:
    return frozenset(x.strip().upper() for x in str(s or "").split(",") if x.strip())


def score() -> None:
    """Compare each model's answers to the hand labels in sample.csv."""
    lab = pd.read_csv(OUT / "sample.csv", dtype=str, keep_default_na=False)
    lab = lab[lab.human_event != ""]          # only posts someone has labelled
    if "human_main" not in lab.columns:
        lab["human_main"] = ""
    if lab.empty:
        print("no labels yet: fill human_states and human_event in data/llm_pilot/sample.csv")
        return
    print(f"{len(lab)} labelled posts {lab.lang.value_counts().to_dict()}\n")
    for f in sorted(OUT.glob("*.csv")):
        if f.name == "sample.csv":
            continue
        m = pd.read_csv(f, dtype=str, keep_default_na=False)
        if "llm_main" not in m.columns:
            print(f"{f.stem}: made before v2 (no main state), skipped\n")
            continue
        m = m.merge(lab[["post_id", "human_states", "human_main", "human_event"]], on="post_id")
        for lang, g in [("all", m)] + list(m.groupby("lang")):
            truth, pred = g.human_states.map(_set), g.llm_states.map(_set)
            tp = sum(len(t & p) for t, p in zip(truth, pred))
            n_pred, n_true = sum(map(len, pred)), sum(map(len, truth))
            exact = (truth == pred).mean()
            ev = (g.llm_event.str.lower().map({"true": "1", "false": "0"}) == g.human_event).mean()
            main = (g.llm_main.str.upper() == g.human_main.str.strip().str.upper()).mean()
            print(f"{f.stem:28} {lang:3}  n={len(g):3}  main state {main:5.1%}  exact list {exact:5.1%}  "
                  f"precision {tp / max(n_pred, 1):5.1%}  recall {tp / max(n_true, 1):5.1%}  "
                  f"event agree {ev:5.1%}  median {pd.to_numeric(g.seconds).median()}s")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="Ollama model tag, e.g. qwen3:8b")
    ap.add_argument("--n", type=int, default=100, help="posts per language (default 100)")
    ap.add_argument("--tier", default="T5_multi", help="geo_tier to sample (default T5_multi)")
    ap.add_argument("--seed", default="pilot1", help="change to draw a different sample")
    ap.add_argument("--no-hint", action="store_true",
                    help="do not tell the model which states the keyword search found")
    ap.add_argument("--think", default="false",
                    help="false (default) / true, or low|medium|high for gpt-oss")
    ap.add_argument("--score", action="store_true", help="score models against hand labels")
    args = ap.parse_args()
    if args.score:
        from scripts.score_state_methods import score as score_all, show
        show(score_all(OUT))     # labels.csv vs every results file, NER included
    elif args.model:
        run(args)
    else:
        ap.error("give --model or --score")


if __name__ == "__main__":
    main()
