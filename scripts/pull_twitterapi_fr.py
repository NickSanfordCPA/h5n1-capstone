"""Pull French-language H5N1/avian-influenza tweets from twitterapi.io.

Third language in the corpus, after English and Spanish. Same 14 keyword concepts,
same `-filter:retweets`, same 33-column output schema, so all four components
concatenate without reconciliation.

TIERED PRIORITY (the reason this is not just the Spanish script with --lang fr):
credit is limited, so days are pulled in priority order rather than straight through.
Tier 1 is the outbreak core; tier 3 is pre-outbreak baseline, valuable but last.
Within each tier days run NEWEST FIRST, so an interrupted run ends at a clean date
boundary that can be stated in a methods section.

    tier 1   2022-01-01 .. 2024-12-31   1,096 days   outbreak core
    tier 2   2025-01-01 .. today          639 days   recent
    tier 3   2021-09-01 .. 2021-12-31     122 days   pre-outbreak baseline

Usage:
    python scripts/pull_twitterapi_fr.py check        # free; audits keyword coverage
    python scripts/pull_twitterapi_fr.py estimate     # ~$0.05; sizes French volume
    python scripts/pull_twitterapi_fr.py pull         # resumable; stops cleanly on 402
    python scripts/pull_twitterapi_fr.py export
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
OUT_DIR = Path("data/twitterapi/fr")
RAW = OUT_DIR / "tweets_fr.jsonl"
STATE = OUT_DIR / "state.json"

TIERS = [
    ("t1_core",     date(2022, 1, 1),  date(2024, 12, 31)),
    ("t2_recent",   date(2025, 1, 1),  date.today()),
    ("t3_baseline", date(2021, 9, 1),  date(2021, 12, 31)),
]

# French equivalents of the inherited 14 concepts, as regexes for per-tweet provenance.
# "grippe aviaire" is overwhelmingly dominant in French; "influenza aviaire" is the
# technical/veterinary register and "peste aviaire" the older term still used in trade
# writing. Accented and unaccented spellings are both matched -- French accents are
# routinely dropped on social media and are not reliably indexed.
KEYWORD_PATTERNS = {
    "Avian flu":       r"grippe\s+aviaire",
    "Avian influenza": r"influenza\s+aviaire",
    "Peste aviaire":   r"peste\s+aviaire",
    "Chicken flu":     r"grippe\s+d[eu]s?\s+poulets?",
    "Duck flu":        r"grippe\s+d[eu]s?\s+canards?",
    "Farm flu":        r"grippe\s+(de\s+ferme|des\s+[ée]levages?|en\s+[ée]levage)",
    "Poultry flu":     r"grippe\s+d[eu]\s+(la\s+)?volailles?|grippe\s+des\s+volailles",
    "Zoonotic flu":    r"(grippe|influenza)\s+zoonotique",
    "HPAI":            r"\bIAHP\b|influenza\s+aviaire\s+hautement\s+patho?g[èe]ne",
    "LPAI":            r"\bIAFP\b|influenza\s+aviaire\s+faiblement\s+patho?g[èe]ne",
    "H5N1":            r"\bH5N1\b",
    "H5N8":            r"\bH5N8\b",
    "H7N9":            r"\bH7N9\b",
    "H9N2":            r"\bH9N2\b",
    "H10N3":           r"\bH10N3\b",
}
KEYWORD_RE = {k: re.compile(v, re.IGNORECASE) for k, v in KEYWORD_PATTERNS.items()}

# One exact literal per concept that MUST appear in a query actually sent to the API.
# Written out rather than derived from the regexes above: de-sugaring a regex produces
# both false alarms and false passes, which makes the audit worse than no audit. This
# is the guard that catches the class of bug that silently dropped "gripe de granja"
# from the Spanish pull.
COVERAGE_PROBES = {
    "Avian flu":       '"grippe aviaire"',
    "Avian influenza": '"influenza aviaire"',
    "Peste aviaire":   '"peste aviaire"',
    "Chicken flu":     '"grippe du poulet"',
    "Duck flu":        '"grippe du canard"',
    "Farm flu":        '"grippe de ferme"',
    "Poultry flu":     '"grippe de la volaille"',
    "Zoonotic flu":    '"grippe zoonotique"',
    "HPAI":            "IAHP",
    "LPAI":            "IAFP",
    "H5N1":            "H5N1",
    "H5N8":            "H5N8",
    "H7N9":            "H7N9",
    "H9N2":            "H9N2",
    "H10N3":           "H10N3",
}

_G1 = ['"grippe aviaire"', '"influenza aviaire"', '"peste aviaire"',
       '"grippe de la volaille"', '"grippe des volailles"']
QUERY_GROUPS = {
    "phrases": " OR ".join(_G1),
    # Subtypes, acronyms and the narrower animal/farm terms, with group 1's terms
    # negated so the two groups are disjoint at purchase time. Most French subtype
    # mentions also say "grippe aviaire"; without the negations those are bought twice
    # and discarded at export.
    "subtypes_and_rest": (
        '(H5N1 OR H5N8 OR H7N9 OR H9N2 OR H10N3 OR IAHP OR IAFP '
        'OR "grippe du poulet" OR "grippe des poulets" '
        'OR "grippe du canard" OR "grippe des canards" '
        'OR "grippe de ferme" OR "grippe des elevages" OR "grippe des élevages" '
        'OR "grippe zoonotique" OR "influenza zoonotique") '
        + " ".join(f"-{p}" for p in _G1)
    ),
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
            sys.exit("OUT OF CREDITS -- stopping cleanly. The checkpoint is intact; "
                     "top up and re-run and nothing already bought is re-bought.")
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        sys.exit(f"HTTP {r.status_code}: {r.text[:400]}")
    return {}


def iso(created_at: str) -> str:
    try:
        return (datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    except (ValueError, TypeError):
        return created_at or ""


def source_query(text: str) -> str:
    hits = [k for k, rx in KEYWORD_RE.items() if rx.search(text or "")]
    return "|".join(hits) if hits else "other"


def _bio(a: dict) -> str:
    """The API returns author.description present-but-empty; the bio is in
    author.profile_bio.description. Established by a field audit against the
    inherited English corpus, where mapping the wrong key cost 89% fill."""
    pb = a.get("profile_bio")
    if isinstance(pb, dict) and (pb.get("description") or "").strip():
        return pb["description"]
    return a.get("description") or ""


def _cohort_location(user_location: str) -> str:
    """The inherited `location` column is userLocation with commas stripped, NOT tweet
    geolocation (the API's `place` is ~0% populated). Reproduced for consistency."""
    return " ".join(user_location.replace(",", " ").split())


def tweet_type(t: dict) -> str:
    if t.get("isReply") or t.get("inReplyToId"):
        return "reply"
    if t.get("quoted_tweet"):
        return "quote"
    return "original"


def to_row(t: dict) -> dict:
    a = t.get("author") or {}
    ent = t.get("entities") or {}
    links = [u.get("expanded_url") or u.get("url") or "" for u in (ent.get("urls") or [])]
    imgs = [m.get("media_url_https") or m.get("media_url") or ""
            for m in ((t.get("extendedEntities") or {}).get("media") or [])]
    ment = ["@" + m.get("screen_name", "") for m in (ent.get("user_mentions") or [])
            if m.get("screen_name")]
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


def tiered_units(only: list[str] | None = None) -> list[tuple[str, str, date]]:
    """(tier, group, day) in priority order: tier 1 before 2 before 3, newest day first
    within each tier, and both groups done for a day before the day advances."""
    units = []
    for name, a, b in TIERS:
        if only and name not in only:
            continue
        d = b
        while d >= a:
            for g in QUERY_GROUPS:
                units.append((name, g, d))
            d -= timedelta(days=1)
    return units


def fetch_day(query: str, d: date, key: str, cap: float) -> tuple[list[dict], int, bool]:
    q = f"({query}) lang:fr -filter:retweets since_time:{unix(d)} until_time:{unix(d, True)}"
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
    sent = " ".join(QUERY_GROUPS.values()).lower()
    print("TIER PLAN (pulled in this order):")
    for name, a, b in TIERS:
        if args.tiers and name not in args.tiers:
            continue
        n = (b - a).days + 1
        print(f"  {name:12s} {b} back to {a}   {n:,} days -> {n*len(QUERY_GROUPS):,} units")
    tot = len(tiered_units(args.tiers))
    print(f"  TOTAL {tot:,} units; per-call minimums alone = "
          f"${tot*CREDITS_PER_TWEET*USD_PER_CREDIT:.2f}")
    print("\nquery lengths: " + ", ".join(f"{k}={len(v)}" for k, v in QUERY_GROUPS.items()))
    print(f"\n{'concept':20s}{'searched?':12s}probe")
    print("-" * 64)
    gaps = 0
    missing_probe = set(KEYWORD_PATTERNS) - set(COVERAGE_PROBES)
    for k in KEYWORD_PATTERNS:
        probe = COVERAGE_PROBES.get(k)
        if probe is None:
            gaps += 1
            print(f"{k:20s}{'NO PROBE':12s}(add one to COVERAGE_PROBES)")
            continue
        ok = probe.lower() in sent
        gaps += not ok
        print(f"{k:20s}{'yes' if ok else 'NOT SENT':12s}{probe}")
    print(f"\n{len(KEYWORD_PATTERNS)-gaps}/{len(KEYWORD_PATTERNS)} concepts verified "
          f"present in a query sent to the API")
    if gaps:
        sys.exit("COVERAGE GAP -- fix QUERY_GROUPS before pulling.")
    if missing_probe:
        sys.exit(f"concepts without a coverage probe: {sorted(missing_probe)}")


def cmd_estimate(args, key):
    units = tiered_units(args.tiers)
    days = sorted({d for _, _, d in units})
    picks = [days[round(i * (len(days) - 1) / 5)] for i in range(6)]
    tot = 0
    print("one page per group per sampled day (a page is 20 tweets)\n")
    for d in picks:
        parts = []
        for g, q in QUERY_GROUPS.items():
            data = get({"query": f"({q}) lang:fr -filter:retweets "
                                 f"since_time:{unix(d)} until_time:{unix(d, True)}",
                        "queryType": "Latest"}, key)
            n = len(data.get("tweets") or [])
            tot += max(n, 1)
            parts.append(f"{g}={n}{'+' if data.get('has_next_page') else ''}")
        print(f"  {d}  " + "  ".join(parts))
    mean = tot / len(picks)
    print(f"\nSPEND: {tot} tweets, ${tot*CREDITS_PER_TWEET*USD_PER_CREDIT:.4f}")
    print(f"crude projection for {len(days):,} days at {mean:.0f} tweets/day: "
          f"${mean*len(days)*CREDITS_PER_TWEET*USD_PER_CREDIT:.2f}")
    print("'+' means the day had more than one page, so this is a FLOOR.")


def cmd_pull(args, key):
    st = load_state()
    done = set(st["done"])
    budget = args.max_tweets or float("inf")
    spent = 0
    units = tiered_units(args.tiers)
    todo = [u for u in units if f"{u[0]}|{u[1]}|{u[2]}" not in done]
    print(f"{len(units):,} units in plan, {len(todo):,} remaining, "
          f"{st['tweets']:,} tweets banked", flush=True)
    cur_tier = None
    with RAW.open("a", encoding="utf-8") as sink:
        for tier, g, d in todo:
            if spent >= budget:
                print("\n-- run budget reached; re-run to continue", flush=True)
                break
            if tier != cur_tier:
                cur_tier = tier
                print(f"\n=== TIER {tier} ===", flush=True)
            tweets, calls, capped = fetch_day(QUERY_GROUPS[g], d, key, budget - spent)
            for t in tweets:
                sink.write(json.dumps({"_tier": tier, "_g": g, "_day": str(d), "t": t},
                                      ensure_ascii=False) + "\n")
            sink.flush()
            spent += len(tweets)
            st["tweets"] += len(tweets)
            st["calls"] += calls
            if not capped:
                st["done"].append(f"{tier}|{g}|{d}")
            save_state(st)
            if tweets:
                print(f"  {g:18s} {d}  n={len(tweets):4d}  total {st['tweets']:,}  "
                      f"${st['tweets']*CREDITS_PER_TWEET*USD_PER_CREDIT:,.2f}", flush=True)
    usd = st["tweets"] * CREDITS_PER_TWEET * USD_PER_CREDIT
    print(f"\nBANKED: {st['tweets']:,} tweets, {st['calls']:,} calls, ${usd:,.2f}")
    days = sorted({x.split('|')[2] for x in st["done"]})
    if days:
        print(f"days complete: {len(days):,}  ({min(days)} .. {max(days)})")


def cmd_export(args, key=None):
    if not RAW.exists():
        sys.exit(f"No raw data at {RAW}. Run `pull` first.")
    out = OUT_DIR / "x_fr_2021_present.csv"
    seen, n = set(), 0
    with RAW.open(encoding="utf-8") as fh, out.open("w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=COLUMNS)
        w.writeheader()
        for line in fh:
            t = json.loads(line)["t"]
            if t.get("id") in seen:
                continue
            seen.add(t.get("id"))
            w.writerow(to_row(t))
            n += 1
    print(f"wrote {n:,} unique rows -> {out}")
    print("33 columns, same schema as the English and Spanish components")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("estimate", cmd_estimate),
                     ("pull", cmd_pull), ("export", cmd_export)):
        sp = sub.add_parser(name)
        sp.add_argument("--tiers", nargs="*", default=None,
                        choices=[t[0] for t in TIERS],
                        help="restrict to these tiers (default: all, in priority order)")
        if name == "pull":
            sp.add_argument("--max-tweets", type=int, default=0,
                            help="spend cap for THIS run in tweets; 0 = uncapped")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.fn(args, None if args.cmd in ("check", "export") else api_key())


if __name__ == "__main__":
    main()
