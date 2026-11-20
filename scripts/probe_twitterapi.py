"""Feasibility probe for twitterapi.io — measure the corpus BEFORE paying to pull it.

The question this answers is not "are there H5N1 tweets" (there are). It is the two
numbers that decide whether fact_social_sentiment at county x day is buildable:

  1. VOLUME   tweets/day matching the query, across the study window.
  2. GEO YIELD share of those tweets that can be resolved to a US county at all.

(2) is the binding constraint. In the existing xpoz pull only 4 of 4,962 tweets
(0.08%) carry a tweet-level place. If twitterapi.io is the same, tweet-level geo is
dead and the county grain has to come from the AUTHOR PROFILE location string --
so this probe measures profile-location fill and US-parseability too, and tests
whether server-side geo operators (geocode:/near:) work at all.

COST MODEL (twitterapi.io, 1 USD = 100,000 credits):
    15 credits = $0.00015 per tweet returned; 15 credits minimum per call.
    So $1.00 of free credit ~= 6,600 tweets. The probe is budgeted in tweets and
    refuses to exceed --max-tweets. Every run prints what it actually spent.

Usage:
    export TWITTERAPI_KEY=...
    python scripts/probe_twitterapi.py balance
    python scripts/probe_twitterapi.py schema          # 1 page, dump raw JSON to inspect fields
    python scripts/probe_twitterapi.py volume  --max-tweets 1200
    python scripts/probe_twitterapi.py geo     --max-tweets 600
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://api.twitterapi.io"
SEARCH = f"{BASE}/twitter/tweet/advanced_search"
CREDITS_PER_TWEET = 15
USD_PER_CREDIT = 1 / 100_000
OUT_DIR = Path("data/twitterapi/probe")

# --- query variants -------------------------------------------------------------
# Kept deliberately parallel to the xpoz RELEVANT_RE terms so the two pulls are
# comparable. Ordered narrow-to-broad; the report shows what each tier costs you.
CORE = '(h5n1 OR "bird flu" OR "avian influenza" OR HPAI OR "avian flu")'
QUERIES = {
    # everything, any language -- the ceiling on corpus size
    "broad_all": CORE,
    # English only -- what the sentiment model scores without the translate step
    "broad_en": f"{CORE} lang:en",
    # the agriculture/outbreak register, i.e. tweets plausibly tied to a real farm event
    "ag_en": f'{CORE} (poultry OR chicken OR flock OR cull OR dairy OR cattle OR egg OR eggs) lang:en',
    # excludes retweets -- retweets duplicate text and skew county-day post_count
    "ag_en_orig": f'{CORE} (poultry OR chicken OR flock OR cull OR dairy OR cattle OR egg OR eggs) lang:en -filter:retweets',
}

# Geo operator probes. If ANY of these return results, server-side geo filtering
# works and county x day becomes a query-per-region pull instead of a text-parse.
# Coordinates are the three densest poultry/dairy regions in the outbreak record.
GEO_PROBES = {
    "geocode_iowa": f"{CORE} geocode:41.878,-93.098,200mi",
    "geocode_calif": f"{CORE} geocode:36.778,-119.418,200mi",
    "near_texas": f'{CORE} near:"Texas" within:200mi',
    "place_country": f"{CORE} place_country:US",
}

US_STATES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut","delaware",
    "florida","georgia","hawaii","idaho","illinois","indiana","iowa","kansas","kentucky",
    "louisiana","maine","maryland","massachusetts","michigan","minnesota","mississippi",
    "missouri","montana","nebraska","nevada","new hampshire","new jersey","new mexico",
    "new york","north carolina","north dakota","ohio","oklahoma","oregon","pennsylvania",
    "rhode island","south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming","district of columbia",
}
US_ABBR = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia","ks","ky","la",
    "me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj","nm","ny","nc","nd","oh","ok",
    "or","pa","ri","sc","sd","tn","tx","ut","vt","va","wa","wv","wi","wy","dc",
}
US_HINT = re.compile(r"\b(usa|u\.s\.a?\.?|united states|america)\b", re.IGNORECASE)


class Budget:
    """Hard cap on spend. Every tweet returned costs 15 credits whether we use it."""

    def __init__(self, max_tweets: int):
        self.max_tweets = max_tweets
        self.tweets = 0
        self.calls = 0

    @property
    def credits(self) -> int:
        # 15 credits/tweet, but never less than 15 credits for a call that returned nothing
        return max(self.tweets, self.calls) * CREDITS_PER_TWEET

    @property
    def usd(self) -> float:
        return self.credits * USD_PER_CREDIT

    def exhausted(self) -> bool:
        return self.tweets >= self.max_tweets

    def line(self) -> str:
        return (f"{self.calls} calls, {self.tweets} tweets, "
                f"{self.credits:,} credits, ${self.usd:.4f}")


def api_key() -> str:
    key = os.environ.get("TWITTERAPI_KEY") or os.environ.get("TWITTERAPI_IO_KEY")
    if not key:
        env = Path(".env")
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith(("TWITTERAPI_KEY=", "TWITTERAPI_IO_KEY=")):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("No API key. export TWITTERAPI_KEY=... (or add it to .env)")
    return key


def get(path: str, params: dict, key: str, tries: int = 4) -> dict:
    """GET with backoff. 429/5xx are retried; anything else surfaces immediately."""
    for attempt in range(tries):
        r = requests.get(path, params=params, headers={"X-API-Key": key}, timeout=45)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        sys.exit(f"HTTP {r.status_code} on {path}\n{r.text[:500]}")
    return {}


def unix(d: date, end: bool = False) -> int:
    t = datetime(d.year, d.month, d.day, 23, 59, 59 if end else 0, tzinfo=timezone.utc)
    if not end:
        t = t.replace(hour=0, minute=0, second=0)
    return int(t.timestamp())


def search_window(query: str, day: date, key: str, budget: Budget,
                  max_pages: int, raw_sink: list | None = None) -> tuple[list[dict], bool]:
    """Page one day of `query`. Returns (tweets, exhausted_naturally).

    exhausted_naturally=True means we saw the END of that day's results, so len(tweets)
    is the TRUE daily count. False means we hit the page budget and the count is a
    lower bound -- the report must not treat the two the same.
    """
    q = f"{query} since_time:{unix(day)} until_time:{unix(day, end=True)}"
    tweets: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        if budget.exhausted():
            return tweets, False
        params = {"query": q, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        data = get(SEARCH, params, key)
        budget.calls += 1
        page = data.get("tweets") or []
        budget.tweets += len(page)
        if raw_sink is not None and not raw_sink:
            raw_sink.append(data)
        tweets.extend(page)
        if not data.get("has_next_page") or not data.get("next_cursor"):
            return tweets, True
        cursor = data["next_cursor"]
    return tweets, False


# --- geo assessment --------------------------------------------------------------

def tweet_place(t: dict) -> str | None:
    """Tweet-level geo, checked across the field spellings the API might use."""
    for k in ("place", "geo", "coordinates", "placeName", "location"):
        v = t.get(k)
        if isinstance(v, dict) and v:
            return json.dumps(v)[:200]
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def author_location(t: dict) -> str:
    a = t.get("author") or {}
    return (a.get("location") or "").strip()


def us_parseable(loc: str) -> bool:
    """Would this free-text profile location plausibly resolve to a US county?

    Deliberately GENEROUS -- it is an upper bound. A real geocoder will do worse,
    so if this number is already small the county grain is not viable.
    """
    if not loc:
        return False
    low = loc.lower()
    if US_HINT.search(low):
        return True
    if any(s in low for s in US_STATES):
        return True
    # "Ames, IA" / "Des Moines, Iowa"
    parts = [p.strip().lower() for p in re.split(r"[,/|]", low) if p.strip()]
    return any(p in US_ABBR or p in US_STATES for p in parts)


def geo_report(tweets: list[dict]) -> dict:
    n = len(tweets)
    if n == 0:
        return {"n": 0}
    with_place = [t for t in tweets if tweet_place(t)]
    locs = [author_location(t) for t in tweets]
    has_loc = [loc for loc in locs if loc]
    us_ok = [loc for loc in has_loc if us_parseable(loc)]
    return {
        "n": n,
        "tweet_place_n": len(with_place),
        "tweet_place_pct": round(100 * len(with_place) / n, 2),
        "author_loc_n": len(has_loc),
        "author_loc_pct": round(100 * len(has_loc) / n, 2),
        "author_loc_us_n": len(us_ok),
        "author_loc_us_pct": round(100 * len(us_ok) / n, 2),
        "top_locations": Counter(us_ok).most_common(15),
        "all_locations": sorted(Counter(has_loc).items(), key=lambda kv: -kv[1]),
        "sample_places": [tweet_place(t) for t in with_place[:5]],
    }


# --- subcommands -----------------------------------------------------------------

def cmd_balance(args, key):
    for path in (f"{BASE}/oapi/my/info", f"{BASE}/twitter/user/info"):
        r = requests.get(path, headers={"X-API-Key": key}, timeout=30)
        print(f"{path} -> {r.status_code}: {r.text[:300]}")


def cmd_schema(args, key):
    """One page, dumped raw. Confirms the real field names before we trust any parser."""
    budget = Budget(args.max_tweets)
    sink: list = []
    tweets, _ = search_window(QUERIES["broad_en"], args.day, key, budget, max_pages=1, raw_sink=sink)
    out = OUT_DIR / "schema_sample.json"
    out.write_text(json.dumps(sink[0] if sink else {}, indent=2)[:400_000])
    print(f"wrote {out}  ({len(tweets)} tweets)")
    if tweets:
        t = tweets[0]
        print("\ntop-level tweet fields:", sorted(t.keys()))
        print("author fields:", sorted((t.get("author") or {}).keys()))
        print("\ngeo on this page:", json.dumps(geo_report(tweets), indent=2)[:1500])
    print("\nSPEND:", budget.line())


def cmd_volume(args, key):
    """Sample days across the study window, per query tier. Volume + geo in one pass."""
    budget = Budget(args.max_tweets)
    days = sample_days(args.start, args.end, args.n_days)
    print(f"sampling {len(days)} days: {', '.join(str(d) for d in days)}\n")

    queries = {k: v for k, v in QUERIES.items() if not args.queries or k in args.queries}
    if not queries:
        sys.exit(f"--queries matched nothing; choose from {list(QUERIES)}")
    rows, pooled = [], {q: [] for q in queries}
    for qname, q in queries.items():
        for d in days:
            if budget.exhausted():
                print(f"!! budget hit; stopping at {qname} {d}")
                break
            tw, complete = search_window(q, d, key, budget, args.max_pages)
            pooled[qname].extend(tw)
            rows.append({"query": qname, "day": str(d), "n": len(tw),
                         "complete": complete, "note": "" if complete else f">= (capped at {args.max_pages} pages)"})
            print(f"  {qname:14s} {d}  n={len(tw):4d} {'exact' if complete else 'LOWER BOUND'}")

    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "days_sampled": [str(d) for d in days],
        "per_day": rows,
        "per_query": {q: {**summarize(rows, q), "geo": geo_report(pooled[q])} for q in queries},
        "spend": {"calls": budget.calls, "tweets": budget.tweets,
                  "credits": budget.credits, "usd": round(budget.usd, 4)},
    }
    out = OUT_DIR / "volume_report.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print_volume(report)
    print(f"\nwrote {out}\nSPEND: {budget.line()}")


def summarize(rows, qname):
    ns = [r["n"] for r in rows if r["query"] == qname]
    exact = [r["n"] for r in rows if r["query"] == qname and r["complete"]]
    if not ns:
        return {}
    mean = sum(ns) / len(ns)
    return {
        "days": len(ns),
        "mean_per_day": round(mean, 1),
        "min": min(ns), "max": max(ns),
        "all_days_exact": len(exact) == len(ns),
        "projected_per_month": round(mean * 30),
        "projected_window_total": round(mean * 365 * 2),
        "projected_full_pull_usd": round(mean * 365 * 2 * CREDITS_PER_TWEET * USD_PER_CREDIT, 2),
    }


def print_volume(rep):
    print("\n" + "=" * 78)
    print(f"{'query':16s}{'tw/day':>9s}{'/month':>9s}{'2yr tot':>10s}{'pull $':>9s}{'geo:place%':>12s}{'geo:profUS%':>12s}")
    print("-" * 78)
    for q, s in rep["per_query"].items():
        g = s.get("geo", {})
        flag = "" if s.get("all_days_exact") else "+"
        print(f"{q:16s}{str(s.get('mean_per_day','-'))+flag:>9s}{s.get('projected_per_month','-'):>9}"
              f"{s.get('projected_window_total','-'):>10}{s.get('projected_full_pull_usd','-'):>9}"
              f"{g.get('tweet_place_pct','-'):>12}{g.get('author_loc_us_pct','-'):>12}")
    print("=" * 78)
    print("+ = lower bound (page cap hit, true volume is higher)")
    print("geo:place%   = tweets with tweet-level coordinates -> direct county assignment")
    print("geo:profUS%  = tweets whose AUTHOR PROFILE location looks US-resolvable (upper bound)")


def cmd_geo(args, key):
    """Do server-side geo operators work? This decides the whole county x day design."""
    budget = Budget(args.max_tweets)
    results = {}
    for name, q in GEO_PROBES.items():
        if budget.exhausted():
            break
        tw, complete = search_window(q, args.day, key, budget, max_pages=args.max_pages)
        results[name] = {"query": q, "n": len(tw), "complete": complete, "geo": geo_report(tw),
                         "sample_text": [t.get("text", "")[:120] for t in tw[:3]]}
        verdict = "WORKS" if tw else "no results (operator likely unsupported or region empty)"
        print(f"  {name:16s} n={len(tw):4d}  {verdict}")

    # Control: same day, no geo operator. If control returns rows and a probe does not,
    # the operator is the reason -- not an empty day.
    ctrl, _ = search_window(QUERIES["broad_all"], args.day, key, budget, max_pages=1)
    results["_control_no_geo"] = {"n": len(ctrl)}
    print(f"  {'_control':16s} n={len(ctrl):4d}  (same day, no geo operator)")

    out = OUT_DIR / "geo_report.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out}\nSPEND: {budget.line()}")


def sample_days(start: date, end: date, n: int) -> list[date]:
    """Evenly spaced days across the window. Spread beats clustering: it catches the
    seasonal outbreak peaks instead of measuring one quiet week."""
    span = (end - start).days
    if n <= 1:
        return [start]
    return [start + timedelta(days=round(i * span / (n - 1))) for i in range(n)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, default_max):
        sp.add_argument("--max-tweets", type=int, default=default_max,
                        help="hard spend cap in tweets (15 credits each)")
        sp.add_argument("--max-pages", type=int, default=10,
                        help="pages per day-window (20 tweets/page)")

    b = sub.add_parser("balance")
    b.set_defaults(fn=cmd_balance)

    s = sub.add_parser("schema")
    common(s, 40)
    s.add_argument("--day", type=date.fromisoformat, default=date(2025, 3, 15))
    s.set_defaults(fn=cmd_schema)

    v = sub.add_parser("volume")
    common(v, 1200)
    v.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 1))
    v.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 1))
    v.add_argument("--n-days", type=int, default=6)
    v.add_argument("--queries", nargs="*", default=None,
                   help=f"subset of query tiers to run (default: all). choices: {list(QUERIES)}")
    v.set_defaults(fn=cmd_volume)

    g = sub.add_parser("geo")
    common(g, 600)
    g.add_argument("--day", type=date.fromisoformat, default=date(2025, 3, 15))
    g.set_defaults(fn=cmd_geo)

    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.fn(args, api_key())


if __name__ == "__main__":
    main()
