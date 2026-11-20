"""Fill the English gap in the X corpus: 2025-01-01 -> present.

The previous cohort's export stops at 2025-01-01 (`until_2025-01-01` in every
filename, confirmed against the data). This pulls the same 14 keywords, the same
`-is_retweet` filter, and the same 33 columns forward from there, so the result
concatenates onto `x_prev_cohort_clean.csv` with no schema reconciliation.

SEPARATE STATE DIRECTORY from the Spanish pull on purpose: both scripts checkpoint,
and sharing `state.json` would have each mark the other's units done.

Design notes carried over from the Spanish pull, both learned the hard way:
  * Queries are MERGED into OR-groups, not one per keyword. twitterapi.io bills a
    15-credit minimum PER CALL even for an empty response; 14 queries x 638 days is
    8,932 calls of mostly-minimums, versus 1,276 for two merged groups.
  * Group 2 NEGATES group 1's terms so the two are disjoint at purchase time. Without
    that, every "avian flu H5N1" tweet is bought twice and deduped away at export --
    paid for, then discarded.
  * `check` audits that every provenance keyword is actually searched for. The Spanish
    pull silently dropped "Farm flu" during a query rewrite; this makes that class of
    bug loud instead of silent.

Usage:
    python scripts/pull_twitterapi_en_gap.py check          # free, no API calls
    python scripts/pull_twitterapi_en_gap.py estimate       # ~$0.05, samples 6 days
    python scripts/pull_twitterapi_en_gap.py pull           # resumable, uncapped
    python scripts/pull_twitterapi_en_gap.py export
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
OUT_DIR = Path("data/twitterapi/en_gap")
RAW = OUT_DIR / "tweets_en_gap.jsonl"
STATE = OUT_DIR / "state.json"

# The previous cohort's corpus covers 2022-06-01 .. 2024-12-31 (verified against the
# data, not the filenames). That leaves TWO gaps, and this script fills both:
#     GAP B  2021-09-01 .. 2022-05-31   273 days   (matches the Spanish corpus start)
#     GAP A  2025-01-01 .. today        639 days
WINDOW_START = date(2021, 9, 1)
COVERED = (date(2022, 6, 1), date(2024, 12, 31))

# The previous cohort's 14 keywords, as regexes, for per-tweet provenance. Derived from
# their filenames (`_Avian flu_ -is_retweet since_... lang_en.csv`).
#
# NOTE ON FIDELITY: "bird flu" is deliberately NOT here. It is the commonest colloquial
# English term and its absence is a real weakness in the original cohort's design, but
# adding it would make this pull non-comparable with the 2022-2024 half of the corpus.
# Matching their filter exactly is worth more than fixing it silently.
KEYWORD_PATTERNS = {
    "Avian flu":       r"\bavian\s+flu\b",
    "Avian influenza": r"\bavian\s+influenza\b",
    "Chicken flu":     r"\bchicken\s+flu\b",
    "Duck flu":        r"\bduck\s+flu\b",
    "Farm flu":        r"\bfarm\s+flu\b",
    "Poultry flu":     r"\bpoultry\s+flu\b",
    "Zoonotic flu":    r"\bzoonotic\s+flu\b",
    "HPAI":            r"\bHPAI\b",
    "LPAI":            r"\bLPAI\b",
    "H5N1":            r"\bH5N1\b",
    "H5N8":            r"\bH5N8\b",
    "H7N9":            r"\bH7N9\b",
    "H9N2":            r"\bH9N2\b",
    "H10N3":           r"\bH10N3\b",
}
KEYWORD_RE = {k: re.compile(v, re.IGNORECASE) for k, v in KEYWORD_PATTERNS.items()}

_PHRASES = [
    '"avian flu"', '"avian influenza"', '"chicken flu"', '"duck flu"',
    '"farm flu"', '"poultry flu"', '"zoonotic flu"', "HPAI", "LPAI",
]
QUERY_GROUPS = {
    "phrases": " OR ".join(_PHRASES),
    # subtypes, minus anything group 1 already bought
    "subtypes": ("(H5N1 OR H5N8 OR H7N9 OR H9N2 OR H10N3) "
                 + " ".join(f"-{p}" for p in _PHRASES)),
}

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
                break
    if not key:
        sys.exit("No API key. export TWITTERAPI_KEY=... (or add it to .env)")
    return key


def unix(d: date, end: bool = False) -> int:
    t = (datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc) if end
         else datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc))
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
        if r.status_code == 402:
            sys.exit("OUT OF CREDITS. Top up and re-run -- the checkpoint is intact, "
                     "nothing will be re-bought.")
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        sys.exit(f"HTTP {r.status_code}: {r.text[:400]}")
    return {}


def iso(created_at: str) -> str:
    """'Sat Mar 15 23:54:18 +0000 2025' -> '2025-03-15T23:54:18.000Z'."""
    try:
        return (datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    except (ValueError, TypeError):
        return created_at or ""


def source_query(text: str) -> str:
    hits = [k for k, rx in KEYWORD_RE.items() if rx.search(text or "")]
    return "|".join(hits) if hits else "other"


def tweet_type(t: dict) -> str:
    if t.get("isReply") or t.get("inReplyToId"):
        return "reply"
    if t.get("quoted_tweet"):
        return "quote"
    return "original"


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


def to_row(t: dict) -> dict:
    a = t.get("author") or {}
    ent = t.get("entities") or {}
    links = [u.get("expanded_url") or u.get("url") or "" for u in (ent.get("urls") or [])]
    imgs = [m.get("media_url_https") or m.get("media_url") or ""
            for m in ((t.get("extendedEntities") or {}).get("media") or [])]
    ment = ["@" + m.get("screen_name", "") for m in (ent.get("user_mentions") or []) if m.get("screen_name")]
    rt = t.get("retweeted_tweet") or {}
    return {
        "_id": t.get("id", ""), "userId": a.get("id", ""),
        "userName": a.get("name", ""), "alias": a.get("userName", ""),
        "profilePicture": a.get("profilePicture", ""),
        "userCreatedAt": iso(a.get("createdAt", "")), "bio": _bio(a),
        "followers": a.get("followers", ""), "following": a.get("following", ""),
        "lists": "", "statusesCount": a.get("statusesCount", ""),
        "verified": str(bool(a.get("isVerified") or a.get("isBlueVerified"))).lower(),
        "userLocation": a.get("location", ""), "lang": t.get("lang", ""),
        "type": tweet_type(t), "text": t.get("text", ""),
        "createdAt": iso(t.get("createdAt", "")),
        "mentions": json.dumps(ment, ensure_ascii=False),
        "totalRetweets": t.get("retweetCount", ""), "favorites": t.get("likeCount", ""),
        "replies": t.get("replyCount", ""),
        "links": json.dumps(links, ensure_ascii=False),
        "firstLink": links[0] if links else "",
        "images": json.dumps(imgs, ensure_ascii=False),
        "impressions": t.get("viewCount", ""), "isImage": str(bool(imgs)).lower(),
        "firstImage": imgs[0] if imgs else "",
        "location": _cohort_location(a.get("location") or ""),
        "url": t.get("url", ""), "noteTweetText": "",
        "retweetedUserId": (rt.get("author") or {}).get("id", "") if rt else "",
        "reportName": "", "source_query": source_query(t.get("text", "")),
    }


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"done": [], "tweets": 0, "calls": 0}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2))


def gap_days(a: date, b: date, which: str = "b") -> list[date]:
    """Uncovered days in [a, b], NEWEST FIRST, restricted to the requested gap.

    which="b"    2021-09-01 .. 2022-05-31  -> walks back from 2022-05-31
    which="a"    2025-01-01 .. today       -> walks back from today
    which="both" everything uncovered      -> walks back from today through 2021-09-01

    Descending within each gap is deliberate: if credits run out partway, the data you
    hold ends at a clean date boundary you can state in a methods section, rather than
    stopping mid-sweep with no way to say what is complete.
    """
    out, d = [], a
    while d <= b:
        in_covered = COVERED[0] <= d <= COVERED[1]
        in_b = d < COVERED[0]
        in_a = d > COVERED[1]
        want = (which == "both" and not in_covered) or (which == "b" and in_b) or \
               (which == "a" and in_a)
        if want:
            out.append(d)
        d += timedelta(days=1)
    return sorted(out, reverse=True)


def fetch_day(query: str, d: date, key: str, cap: float) -> tuple[list[dict], int, bool]:
    q = f"({query}) lang:en -filter:retweets since_time:{unix(d)} until_time:{unix(d, True)}"
    out, cursor, calls = [], "", 0
    while True:
        params = {"query": q, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        data = get(params, key)
        calls += 1
        out.extend(data.get("tweets") or [])
        if len(out) >= cap:
            return out, calls, True
        if not data.get("has_next_page") or not data.get("next_cursor"):
            return out, calls, False
        cursor = data["next_cursor"]


def cmd_check(args, key=None):
    """Free. Proves every provenance keyword is actually in a query sent to the API."""
    sent = " ".join(QUERY_GROUPS.values()).lower()
    gaps = gap_days(args.start, args.end, args.gap)
    print(f"previous cohort covers {COVERED[0]} .. {COVERED[1]} (excluded)")
    print(f"--gap {args.gap}  ->  {len(gaps):,} days to pull: "
          f"{max(gaps)} walking BACK to {min(gaps)}")
    print(f"  {len(gaps):,} days x {len(QUERY_GROUPS)} groups = "
          f"{len(gaps)*len(QUERY_GROUPS):,} units")
    print(f"  first 3 days: {[str(d) for d in gaps[:3]]}")
    print(f"  last 3 days : {[str(d) for d in gaps[-3:]]}")
    print("query lengths: " + ", ".join(f"{k}={len(v)}" for k, v in QUERY_GROUPS.items()))
    print(f"\n{'keyword':20s}{'searched?':12s}probe")
    print("-" * 52)
    gaps = 0
    for k, pat in KEYWORD_PATTERNS.items():
        probe = pat.replace(r"\b", "").replace(r"\s+", " ").lower()
        ok = probe in sent
        gaps += not ok
        print(f"{k:20s}{'yes' if ok else 'NO -- GAP':12s}{probe}")
    print(f"\n{len(KEYWORD_PATTERNS) - gaps}/{len(KEYWORD_PATTERNS)} covered"
          + ("" if not gaps else f"  <-- {gaps} GAP(S), fix before pulling"))


def cmd_estimate(args, key):
    """Six evenly spaced days, one page each, to size the pull before committing."""
    gaps = gap_days(args.start, args.end, args.gap)
    days = [gaps[round(i * (len(gaps) - 1) / 5)] for i in range(6)]
    tot = 0
    print("sampling one page per day per group (a page is 20 tweets)\n")
    for d in days:
        row = []
        for g, q in QUERY_GROUPS.items():
            data = get({"query": f"({q}) lang:en -filter:retweets "
                                 f"since_time:{unix(d)} until_time:{unix(d, True)}",
                        "queryType": "Latest"}, key)
            n = len(data.get("tweets") or [])
            tot += max(n, 1)
            row.append(f"{g}={n}{'+' if data.get('has_next_page') else ''}")
        print(f"  {d}  " + "  ".join(row))
    print(f"\nSPEND: {tot} tweets, ${tot*CREDITS_PER_TWEET*USD_PER_CREDIT:.4f}")
    print("A '+' means the day has more than one page, so its true count is higher --\n"
          "treat any projection built on these as a FLOOR.")


def cmd_pull(args, key):
    st = load_state()
    done = set(st["done"])
    budget = args.max_tweets or float("inf")
    spent = 0
    units = [(g, d) for d in gap_days(args.start, args.end, args.gap)
             for g in QUERY_GROUPS]
    todo = [u for u in units if f"{u[0]}|{u[1]}" not in done]
    print(f"{len(units):,} units total, {len(todo):,} remaining, "
          f"{st['tweets']:,} tweets banked", flush=True)

    with RAW.open("a", encoding="utf-8") as sink:
        for g, d in todo:
            if spent >= budget:
                print("\n-- run budget reached; re-run to continue", flush=True)
                break
            tweets, calls, capped = fetch_day(QUERY_GROUPS[g], d, key, budget - spent)
            for t in tweets:
                sink.write(json.dumps({"_g": g, "_day": str(d), "t": t},
                                      ensure_ascii=False) + "\n")
            sink.flush()
            spent += len(tweets)
            st["tweets"] += len(tweets)
            st["calls"] += calls
            if not capped:            # a truncated day must be refetched, not banked
                st["done"].append(f"{g}|{d}")
            save_state(st)
            if tweets:
                print(f"  {g:10s} {d}  n={len(tweets):4d}  "
                      f"total {st['tweets']:,}  "
                      f"${st['tweets']*CREDITS_PER_TWEET*USD_PER_CREDIT:,.2f}", flush=True)

    usd = st["tweets"] * CREDITS_PER_TWEET * USD_PER_CREDIT
    print(f"\nBANKED: {st['tweets']:,} tweets, {st['calls']:,} calls, ${usd:,.2f}")
    print(f"raw: {RAW}")


def cmd_export(args, key=None):
    if not RAW.exists():
        sys.exit(f"No raw data at {RAW}. Run `pull` first.")
    out = OUT_DIR / "x_en_2025_present.csv"
    seen, n, oob = set(), 0, 0
    with RAW.open(encoding="utf-8") as fh, out.open("w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=COLUMNS)
        w.writeheader()
        for line in fh:
            t = json.loads(line)["t"]
            if t.get("id") in seen:
                continue
            seen.add(t.get("id"))
            row = to_row(t)
            # Guard against date contamination, and against re-importing days the
            # previous cohort already covers (which would duplicate on concat).
            day = row["createdAt"][:10]
            if day and (day < str(WINDOW_START)
                        or str(COVERED[0]) <= day <= str(COVERED[1])):
                oob += 1
                continue
            w.writerow(row)
            n += 1
    print(f"wrote {n:,} unique rows -> {out}")
    if oob:
        print(f"dropped {oob:,} rows outside the gaps (contamination or already covered)")
    print("schema matches x_prev_cohort_clean.csv -- concatenates directly")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("estimate", cmd_estimate),
                     ("pull", cmd_pull), ("export", cmd_export)):
        sp = sub.add_parser(name)
        sp.add_argument("--start", type=date.fromisoformat, default=WINDOW_START)
        sp.add_argument("--gap", choices=("b", "a", "both"), default="b",
                        help="b = 2021-09..2022-05 (default), a = 2025-01..today, both")
        sp.add_argument("--end", type=date.fromisoformat, default=date.today())
        if name == "pull":
            sp.add_argument("--max-tweets", type=int, default=0,
                            help="spend cap for THIS run in tweets; 0 = uncapped")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.fn(args, None if args.cmd in ("check", "export") else api_key())


if __name__ == "__main__":
    main()
