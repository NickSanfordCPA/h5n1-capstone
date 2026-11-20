"""Pull Spanish-language H5N1/avian-influenza tweets from twitterapi.io.

Mirrors the previous cohort's English corpus (`x_prev_cohort_clean.csv`) so the two
can be concatenated: same 33 columns, same value formats, same `-is_retweet` filter.
Their 14 keywords are translated below; virus subtype names (H5N1, H7N9, ...) are
identical in Spanish and carry over unchanged.

WHY A SEPARATE SCRIPT FROM probe_twitterapi.py: the probe samples days to estimate
size and is deliberately cheap. This one is exhaustive and long-running -- days can
take many pages -- so it checkpoints after every single day. Interrupt it, re-run it,
and it resumes from where it stopped without re-buying a single tweet.

COST: 15 credits per tweet returned ($0.00015). --max-tweets is a hard cap and the
run stops cleanly at it, checkpoint intact, so you can top up and continue.

Usage:
    export TWITTERAPI_KEY=...            # or put it in .env
    python scripts/pull_twitterapi_es.py validate                    # ~60 tweets, checks 2021 reach
    python scripts/pull_twitterapi_es.py pull --max-tweets 5000      # resumable
    python scripts/pull_twitterapi_es.py export                      # JSONL -> English-schema CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

SEARCH = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CREDITS_PER_TWEET = 15
USD_PER_CREDIT = 1 / 100_000
OUT_DIR = Path("data/twitterapi/es")
RAW = OUT_DIR / "tweets_es.jsonl"
STATE = OUT_DIR / "state.json"

# Spanish equivalents of the 14 English filter words. Both the Iberian ("gripe") and
# Latin American ("influenza") registers are included -- LatAm is where the poultry
# industry reporting is, Spain is where much of the news vocabulary is, and the two
# genuinely do not overlap in usage. "peste aviar" is the older term still used in
# veterinary and trade contexts. Subtypes are identical across languages.
# Spanish equivalents of the 14 English filter words, as REGEXES. These are not sent
# to the API -- they run locally over returned text to recover which concept matched,
# reproducing the English corpus's per-keyword `source_query` provenance.
#
# Both registers are covered deliberately: "gripe" is Iberian, "influenza" Latin
# American, and the two barely overlap in usage -- LatAm is where the poultry industry
# reporting is, Spain is where much of the news vocabulary is. "peste aviar" is the
# older term still current in veterinary and trade writing. Accented and unaccented
# spellings are both matched because neither users nor the search index are consistent.
KEYWORD_PATTERNS = {
    "Avian flu":       r"gripe\s+aviar(ia)?",
    "Avian influenza": r"influenza\s+aviar(ia)?",
    "Peste aviar":     r"peste\s+aviar",
    "Chicken flu":     r"gripe\s+(del?\s+)?pollos?|gripe\s+de\s+las\s+gallinas",
    "Duck flu":        r"gripe\s+(del?\s+)?patos?",
    "Farm flu":        r"gripe\s+(de\s+granja|en\s+granjas)",
    "Poultry flu":     r"gripe\s+av[ií]cola|gripe\s+de\s+aves\s+de\s+corral",
    "Zoonotic flu":    r"(gripe|influenza)\s+zoon[óo]tica",
    "HPAI":            r"\bIAAP\b|influenza\s+aviar\s+altamente\s+pat[óo]gena",
    "LPAI":            r"\bIABP\b|influenza\s+aviar\s+de\s+baja\s+patogenicidad",
    "H5N1":            r"\bH5N1\b",
    "H5N8":            r"\bH5N8\b",
    "H7N9":            r"\bH7N9\b",
    "H9N2":            r"\bH9N2\b",
    "H10N3":           r"\bH10N3\b",
}
KEYWORD_RE = {k: re.compile(v, re.IGNORECASE) for k, v in KEYWORD_PATTERNS.items()}

# What actually goes to the API. Merged into two OR-groups rather than one query per
# keyword, because twitterapi.io bills a 15-CREDIT MINIMUM PER CALL even when the call
# returns nothing -- and most Spanish keyword-days return nothing. Fifteen queries a
# day over this window is 27,810 calls ($4.17) of pure minimums before a single tweet
# is paid for; two merged queries a day is 3,708 calls ($0.56). Same coverage.
#
# Two groups, not one, only because a single OR-chain of every term risks tripping the
# search backend's query-length limit.
QUERY_GROUPS = {
    "phrases": (
        '"gripe aviar" OR "gripe aviaria" OR "influenza aviar" OR "influenza aviaria" '
        'OR "peste aviar" OR "gripe avicola" OR "gripe avícola" '
        'OR "gripe de aves de corral"'
    ),
    # The negations matter financially, not just tidily. Most Spanish H5N1 tweets ALSO
    # say "gripe aviar" or "influenza aviar", so without them this group re-buys a large
    # share of what `phrases` already returned -- at 15 credits each -- only for export
    # to dedup them away. Excluding group 1's terms makes the two groups disjoint at
    # purchase time while keeping their UNION identical.
    "subtypes_and_rest": (
        '(H5N1 OR H5N8 OR H7N9 OR H9N2 OR H10N3 OR IAAP OR IABP '
        'OR "gripe del pollo" OR "gripe de pollo" OR "gripe de las gallinas" '
        'OR "gripe del pato" OR "gripe de pato" OR "gripe zoonotica" '
        'OR "gripe zoonótica" OR "influenza zoonotica") '
        '-"gripe aviar" -"gripe aviaria" -"influenza aviar" -"influenza aviaria" '
        '-"peste aviar" -"gripe avicola" -"gripe avícola" -"gripe de aves de corral"'
    ),
}


def source_query(text: str) -> str:
    """Which concept(s) this tweet matched, as a '|'-joined label.

    Derived locally instead of taken from the query that fetched it -- which is
    strictly better provenance than the English corpus had, where a tweet matching
    three keywords was simply duplicated across three files.
    """
    hits = [k for k, rx in KEYWORD_RE.items() if rx.search(text or "")]
    return "|".join(hits) if hits else "other"


# The English export's 32 columns, plus our provenance column. Order is the CSV order.
COLUMNS = [
    "_id", "userId", "userName", "alias", "profilePicture", "userCreatedAt", "bio",
    "followers", "following", "lists", "statusesCount", "verified", "userLocation",
    "lang", "type", "text", "createdAt", "mentions", "totalRetweets", "favorites",
    "replies", "links", "firstLink", "images", "impressions", "isImage", "firstImage",
    "location", "url", "noteTweetText", "retweetedUserId", "reportName", "source_query",
]


def api_key() -> str:
    key = os.environ.get("TWITTERAPI_KEY") or os.environ.get("TWITTERAPI_IO_KEY")
    if not key and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith(("TWITTERAPI_KEY=", "TWITTERAPI_IO_KEY=")):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("No API key. export TWITTERAPI_KEY=... (or add it to .env)")
    return key


def unix(d: date, end: bool = False) -> int:
    t = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc) if end else \
        datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    return int(t.timestamp())


def get(params: dict, key: str, tries: int = 5) -> dict:
    for attempt in range(tries):
        try:
            r = requests.get(SEARCH, params=params, headers={"X-API-Key": key}, timeout=60)
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        sys.exit(f"HTTP {r.status_code}: {r.text[:400]}")
    return {}


# --- field mapping -------------------------------------------------------------

def iso(created_at: str) -> str:
    """'Sat Mar 15 23:54:18 +0000 2025' -> '2025-03-15T23:54:18.000Z' (the English format)."""
    try:
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except (ValueError, TypeError):
        return created_at or ""


def tweet_type(t: dict) -> str:
    """Match the English export's vocabulary: original / reply / quote."""
    if t.get("isReply") or t.get("inReplyToId"):
        return "reply"
    if t.get("quoted_tweet"):
        return "quote"
    return "original"


def media_urls(t: dict) -> list[str]:
    ext = t.get("extendedEntities") or {}
    return [m.get("media_url_https") or m.get("media_url") or ""
            for m in (ext.get("media") or []) if isinstance(m, dict)]


def link_urls(t: dict) -> list[str]:
    ent = t.get("entities") or {}
    return [u.get("expanded_url") or u.get("url") or ""
            for u in (ent.get("urls") or []) if isinstance(u, dict)]


def _bio(a: dict) -> str:
    """The API puts the bio in author.profile_bio.description, NOT author.description.
    The latter is present but empty on ~100% of rows -- mapping it cost us 89% fill
    against the cohort's `bio` column until an audit caught it."""
    pb = a.get("profile_bio")
    if isinstance(pb, dict) and (pb.get("description") or "").strip():
        return pb["description"]
    return a.get("description") or ""


def _cohort_location(user_location: str) -> str:
    """The cohort's `location` column is NOT tweet geo -- it is userLocation with
    commas stripped ("Hubbards, Nova Scotia, Canada" -> "Hubbards Nova Scotia Canada").
    Reproduced here so the column means the same thing in both halves of the corpus.
    Tweet-level place lives in twitterapi.io's `place`, which is ~0% populated."""
    return " ".join(user_location.replace(",", " ").split())


def to_row(t: dict, source_query: str) -> dict:
    """twitterapi.io tweet object -> one row in the English corpus's schema.

    Four English columns have no twitterapi.io equivalent and are left empty rather
    than faked: `lists` (user's list membership count), `noteTweetText` (long-form
    body), `reportName` (empty in the English data too), and `firstImage` is derived
    rather than given. Everything else maps directly.
    """
    a = t.get("author") or {}
    links = link_urls(t)
    imgs = media_urls(t)
    ment = ["@" + m.get("screen_name", "") for m in ((t.get("entities") or {}).get("user_mentions") or []) if m.get("screen_name")]
    rt = t.get("retweeted_tweet") or {}
    return {
        "_id": t.get("id", ""),
        "userId": a.get("id", ""),
        "userName": a.get("name", ""),
        "alias": a.get("userName", ""),
        "profilePicture": a.get("profilePicture", ""),
        "userCreatedAt": iso(a.get("createdAt", "")),
        "bio": _bio(a),
        "followers": a.get("followers", ""),
        "following": a.get("following", ""),
        "lists": "",
        "statusesCount": a.get("statusesCount", ""),
        "verified": str(bool(a.get("isVerified") or a.get("isBlueVerified"))).lower(),
        "userLocation": a.get("location", ""),
        "lang": t.get("lang", ""),
        "type": tweet_type(t),
        "text": t.get("text", ""),
        "createdAt": iso(t.get("createdAt", "")),
        "mentions": json.dumps(ment, ensure_ascii=False),
        "totalRetweets": t.get("retweetCount", ""),
        "favorites": t.get("likeCount", ""),
        "replies": t.get("replyCount", ""),
        "links": json.dumps(links, ensure_ascii=False),
        "firstLink": links[0] if links else "",
        "images": json.dumps(imgs, ensure_ascii=False),
        "impressions": t.get("viewCount", ""),
        "isImage": str(bool(imgs)).lower(),
        "firstImage": imgs[0] if imgs else "",
        "location": _cohort_location(a.get("location") or ""),
        "url": t.get("url", ""),
        "noteTweetText": "",
        "retweetedUserId": (rt.get("author") or {}).get("id", "") if rt else "",
        "reportName": "",
        "source_query": source_query,
    }


# --- state ---------------------------------------------------------------------

def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"done": [], "tweets": 0, "calls": 0}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2))


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_day(query: str, d: date, key: str, cap: int) -> tuple[list[dict], int, bool]:
    """All tweets for one keyword on one day. Returns (tweets, calls, hit_cap)."""
    q = f"({query}) lang:es -filter:retweets since_time:{unix(d)} until_time:{unix(d, True)}"
    out, cursor, calls = [], "", 0
    while True:
        params = {"query": q, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        data = get(params, key)
        calls += 1
        page = data.get("tweets") or []
        out.extend(page)
        if len(out) >= cap:          # cap is inf when uncapped, so this never trips
            return out, calls, True
        if not data.get("has_next_page") or not data.get("next_cursor"):
            return out, calls, False
        cursor = data["next_cursor"]


# --- subcommands ---------------------------------------------------------------

def cmd_validate(args, key):
    """Cheapest possible answer to the two questions that decide the whole pull:
    does the API reach back to 2021, and is there enough Spanish volume to bother?"""
    probes = [date(2021, 9, 1), date(2022, 6, 15), date(2025, 3, 15)]
    spend = 0
    print("Does twitterapi.io reach back that far, and how dense is Spanish?\n")
    for d in probes:
        q = f'({QUERY_GROUPS["phrases"]}) lang:es -filter:retweets'
        data = get({"query": f"{q} since_time:{unix(d)} until_time:{unix(d, True)}",
                    "queryType": "Latest"}, key)
        n = len(data.get("tweets") or [])
        spend += max(n, 1)
        more = data.get("has_next_page")
        dates = {(t.get("createdAt") or "")[:16] for t in (data.get("tweets") or [])}
        print(f"  {d}  n={n:3d}{'+' if more else ' '}  sample dates: {sorted(dates)[:2]}")
    print(f"\nSPEND: {spend} tweets, {spend*CREDITS_PER_TWEET:,} credits, "
          f"${spend*CREDITS_PER_TWEET*USD_PER_CREDIT:.4f}")
    # A zero here is ambiguous on its own -- it means either the archive stops short
    # or the topic had not started. Only a high-volume generic query on the SAME day
    # separates them. Measured 2026-09-29: '"buenos dias" lang:es' returns 20+ on
    # 2021-09-01, so the archive does reach 2021 and the topic zeros are real.
    print("\nA zero does NOT by itself mean the archive stops there -- re-check with a "
          "generic high-volume query on the same day before moving the start date.")


def cmd_pull(args, key):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    st = load_state()
    done = set(st["done"])
    budget = args.max_tweets or float("inf")
    spent_this_run = 0
    groups = {k: v for k, v in QUERY_GROUPS.items()
              if not args.groups or k in args.groups}
    units = [(g, d) for g in groups for d in daterange(args.start, args.end)]
    todo = [(kw, d) for kw, d in units if f"{kw}|{d}" not in done]
    print(f"{len(units):,} keyword-days total, {len(todo):,} remaining, "
          f"{st['tweets']:,} tweets banked so far")

    with RAW.open("a", encoding="utf-8") as sink:
        for kw, d in todo:
            if spent_this_run >= budget:
                print(f"\n-- budget reached ({spent_this_run:,} tweets this run). "
                      f"Re-run to continue; nothing will be re-bought.")
                break
            remaining = budget - spent_this_run
            tweets, calls, capped = fetch_day(groups[kw], d, key, cap=remaining)
            for t in tweets:
                sink.write(json.dumps({"_kw": kw, "_day": str(d), "t": t},
                                      ensure_ascii=False) + "\n")
            sink.flush()
            spent_this_run += len(tweets)
            st["tweets"] += len(tweets)
            st["calls"] += calls
            # Only mark the day complete if we saw its end -- a day cut off by the
            # budget must be re-fetched, or it silently ends up partial forever.
            if not capped:
                st["done"].append(f"{kw}|{d}")
            save_state(st)
            if len(tweets):
                usd = st["tweets"] * CREDITS_PER_TWEET * USD_PER_CREDIT
                print(f"  {kw:18s} {d}  n={len(tweets):4d}  "
                      f"total {st['tweets']:,} tweets  ${usd:,.2f}"
                      f"{'  CAPPED' if capped else ''}")

    cr = st["tweets"] * CREDITS_PER_TWEET
    print(f"\nTOTAL BANKED: {st['tweets']:,} tweets, {cr:,} credits, "
          f"${cr*USD_PER_CREDIT:.2f}   (raw: {RAW})")


def cmd_export(args, key=None):
    """JSONL -> CSV in the English corpus's exact schema, deduped on tweet id."""
    if not RAW.exists():
        sys.exit(f"No raw data at {RAW}. Run `pull` first.")
    seen, n = set(), 0
    out = OUT_DIR / "x_es_2021_present.csv"
    with RAW.open(encoding="utf-8") as fh, out.open("w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=COLUMNS)
        w.writeheader()
        for line in fh:
            rec = json.loads(line)
            t = rec["t"]
            if t.get("id") in seen:
                continue
            seen.add(t.get("id"))
            w.writerow(to_row(t, source_query(t.get("text", ""))))
            n += 1
    print(f"wrote {n:,} unique rows -> {out}")
    print("columns identical to x_prev_cohort_clean.csv, so the two concatenate directly")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="cheap reachability + density check")
    v.set_defaults(fn=cmd_validate)

    pl = sub.add_parser("pull", help="exhaustive, resumable pull")
    pl.add_argument("--start", type=date.fromisoformat, default=date(2021, 9, 1))
    pl.add_argument("--end", type=date.fromisoformat, default=date.today())
    pl.add_argument("--max-tweets", type=int, default=0,
                    help="spend cap for THIS run in tweets (15 credits each); 0 = uncapped")
    pl.add_argument("--groups", nargs="*", default=None,
                    help=f"subset of {list(QUERY_GROUPS)}")
    pl.set_defaults(fn=cmd_pull)

    e = sub.add_parser("export", help="JSONL -> English-schema CSV")
    e.set_defaults(fn=cmd_export)

    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.fn(args, api_key())


if __name__ == "__main__":
    main()
