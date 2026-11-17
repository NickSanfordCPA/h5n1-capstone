"""Geocode the TEXT of a post to one US state and, where the text supports it, a county.

This is the one geography rule shared by both platforms. Meta gives no author location, so
the place a post NAMES is the only geography both platforms have. It answers "where is this
post about", not "where does the author live". (X profile locations are kept separately as a
sensitivity tier, built by h5n1.sources.x_location.)

No model. Fixed rules against the 2021-vintage Census gazetteer already used by x_location
(legacy Connecticut counties, like dim_county).

EVIDENCE, strongest first
    county        "Weld County" / "condado de Weld" with its state named, or a county name
                  that exists in only one state
    city          "Willmar, MN" / "Willmar, Minnesota" / "Willmar, Minn." found in the
                  gazetteer for that state
    city_nostate  one of a fixed list of large, unambiguous US cities ("Seattle")
    state         a state named in full ("Minnesota", "Nuevo México")

WHAT COUNTS AS A STATE NAME
    * Full names only, capitalised in the post (or the post has no lower-case letters at all).
      Two-letter codes count only in "Place, ST" where the place is in the gazetteer for ST.
      AP abbreviations count only in "Place, Minn." form.
    * "Washington": DC when written "Washington, D.C." / "DC"; the state when written
      "Washington state", "state of Washington", or with a compass word; otherwise the state
      with `ambiguous` set, unless a federal cue is present (White House, Congress, ...), in
      which case it is ignored. The reader later confirms or moves every state.
    * "Georgia": ignored when the post has a cue for the country or for South Georgia island.
    * A state name followed by "County"/"Parish" is a county, not a state ("Delaware County").
    * Outlets, species, rivers and flyways that carry a state name are removed first
      ("Washington Post", "California condor", "Mississippi Flyway").
    * "Sierra Nevada" and "Río Colorado" are removed. "Baja California", "La Florida",
      "South Georgia" and "New South Wales" are foreign places: flagged, then removed.
    * Text is NFKC-normalised first, so styled Unicode capitals read as plain letters.

CLASSES
    us       exactly one state                      -> kept
    multi    two or more states                     -> removed ("Names more than one US state")
    foreign  no state, and a foreign place is named -> removed ("No US state named")
    none     no state                               -> removed ("No US state named")
A post with one state AND a foreign place is kept as `us` with foreign_marker = True.
"""
from __future__ import annotations

import functools
import re
import unicodedata

from h5n1.sources import x_location as xl

ST = xl.ST                       # USPS -> lower-case English name
ES_NAMES = {
    "nueva york": "NY", "nueva jersey": "NJ", "nuevo méxico": "NM", "nuevo mexico": "NM",
    "nuevo hampshire": "NH", "nueva hampshire": "NH", "carolina del norte": "NC",
    "carolina del sur": "SC", "dakota del norte": "ND", "dakota del sur": "SD",
    "virginia occidental": "WV", "luisiana": "LA", "pensilvania": "PA", "misisipi": "MS",
    "misisipí": "MS", "misuri": "MO", "tejas": "TX", "hawái": "HI", "hawai": "HI",
    "oregón": "OR", "míchigan": "MI", "minesota": "MN", "florida": "FL",
}
AP = {"Ala": "AL", "Ariz": "AZ", "Ark": "AR", "Calif": "CA", "Colo": "CO", "Conn": "CT",
      "Del": "DE", "Fla": "FL", "Ga": "GA", "Ill": "IL", "Ind": "IN", "Kan": "KS", "Ky": "KY",
      "Md": "MD", "Mass": "MA", "Mich": "MI", "Minn": "MN", "Miss": "MS", "Mo": "MO",
      "Mont": "MT", "Neb": "NE", "Nev": "NV", "Okla": "OK", "Ore": "OR", "Pa": "PA",
      "Tenn": "TN", "Vt": "VT", "Va": "VA", "Wash": "WA", "Wis": "WI", "Wyo": "WY",
      "N.Y": "NY", "N.J": "NJ", "N.C": "NC", "N.D": "ND", "S.C": "SC", "S.D": "SD",
      "N.M": "NM", "N.H": "NH", "R.I": "RI", "W.Va": "WV"}

_names = {**{v: k for k, v in ST.items()}, **ES_NAMES}
_names.pop("district of columbia", None)
STATE_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _names), key=len, reverse=True))
                      + r")(?![\w-])", re.I)
PLACE = r"[A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,2}"
ABBR_RE = re.compile(rf"({PLACE}),\s?({'|'.join(ST)})(?![\w.])")
AP_RE = re.compile(rf"({PLACE}),\s({'|'.join(map(re.escape, AP))})\.")
COUNTY_RE = re.compile(rf"({PLACE}) (?:County|Parish|Borough|Co\.)(?![\w])")
CONDADO_RE = re.compile(rf"[Cc]ondado de ({PLACE})")
DC_RE = re.compile(r"Washington,? D\.?\s?C\.?|(?<![\w.])D\.C\.|(?<![\w.])DC(?![\w.])|"
                   r"District of Columbia|Distrito de Columbia", re.I)
DC_CAPS = re.compile(r"(?<![\w.])DC(?![\w.])")
KC_RE = re.compile(r"Kansas City(,? (?:Kansas|KS|Kan\.))?", re.I)
WA_STATE = re.compile(r"washington state|state of washington|estado de washington|"
                      r"(?:western|eastern|central|southwest|northwest|southeast|northeast|"
                      r"southern|northern) washington|washington['’]s (?:department|state)|"
                      r"washington (?:department|dept\.?) of|\bwdfw\b|\bwsda\b", re.I)
FEDERAL = re.compile(r"white house|casa blanca|congress|congreso|senate|senado|capitol|"
                     r"administration|administración|pentagon|federal|biden|trump|"
                     r"WASHINGTON \(|WASHINGTON —|WASHINGTON -|desde washington|"
                     r"gobierno de (?:estados unidos|ee\.? ?uu)", re.I)
GEORGIA_COUNTRY = re.compile(r"tbilisi|republic of georgia|georgia \(country\)|south georgia|"
                             r"georgia del sur|georgias del sur|caucasus|cáucaso|armenia|"
                             r"azerbaijan|black sea|sandwich islands", re.I)

# removed before any matching: outlets, species, rivers, flyways, brands
STOP = re.compile(
    r"new york (?:times|post|magazine|daily news)|washington (?:post|times|examiner)|"
    r"los angeles times|l\.a\. times|usa today|"
    r"california condors?|c[óo]ndor(?:es)? (?:de|californian[oa]s?) (?:de )?california|"
    r"carolina (?:wren|chickadee|parakeet)s?|virginia (?:rail|opossum)s?|"
    r"mississippi (?:kite|flyway|river)s?|missouri river|colorado river|ohio (?:river|valley)|"
    r"tennessee (?:river|valley)|arkansas river|delaware (?:river|bay|valley)|"
    r"r[íi]o (?:colorado|misisip[ií]|mississippi|grande)|sierra nevada|"
    r"golfo de california|gulf of california|"
    r"kentucky fried|texas roadhouse|indiana jones|hawaiian (?:goose|geese)|"
    r"nueva york times|george washington|mount washington", re.I)
# foreign places whose names contain a US state name: counted as a foreign marker, then hidden
FOREIGN_PHRASE = re.compile(
    r"baja california(?: sur)?|la florida|south georgia|georgias? del sur|"
    r"new (?:south wales|zealand|brunswick|delhi|caledonia|guinea)", re.I)

FOREIGN = re.compile(
    xl.FOREIGN.pattern[:-3] + r"|ecuador|bolivia|uruguay|paraguay|guatemala|honduras|nicaragua|"
    r"costa rica|panam[áa]|el salvador|rep[úu]blica dominicana|puerto rico|gal[áa]pagos|"
    r"antarctic\w*|ant[áa]rtida|ant[áa]rtica|falklands?|malvinas|russia|rusia|ukraine|ucrania|"
    r"cambodia|camboya|laos|myanmar|iraq|iran|hungary|hungr[íi]a|czech|romania|bulgaria|"
    r"m[ée]xico|per[úu]|espa[ñn]a|brasil|canad[áa]|jap[óo]n|reino unido|inglaterra|francia|"
    r"alemania|italia|pa[íi]ses bajos|holanda|sud[áa]frica|filipinas|europa|"
    r"sonora|jalisco|chihuahua|yucat[áa]n|nuevo le[óo]n|tijuana|bogot[áa]|lima|santiago|"
    r"buenos aires|quito|caracas|andaluc[íi]a|catalu[ñn]a|galicia|castilla)\b", re.I)

# Large US cities whose bare name is safe in free text. Fixed list: cities of 250k+ that
# are 5x the next US place of the same name, minus names that are also common words,
# first names or foreign cities (Mesa, Plano, Buffalo, Lincoln, San Jose, Toledo, ...).
BARE_CITIES = [
    "albuquerque", "anaheim", "anchorage", "atlanta", "austin", "bakersfield", "baltimore",
    "boston", "chicago", "cincinnati", "cleveland", "colorado springs", "corpus christi",
    "dallas", "denver", "detroit", "el paso", "fort wayne", "fort worth", "fresno", "greensboro",
    "houston", "indianapolis", "irvine", "jacksonville", "jersey city", "las vegas", "long beach",
    "los angeles", "lubbock", "memphis", "miami", "milwaukee", "minneapolis", "nashville",
    "new orleans", "newark", "oakland", "oklahoma city", "omaha", "orlando", "philadelphia",
    "phoenix", "pittsburgh", "portland", "raleigh", "reno", "sacramento", "san antonio",
    "san diego", "san francisco", "seattle", "stockton", "tampa", "tucson", "tulsa",
    "virginia beach", "wichita", "winston-salem", "new york city", "nyc",
]
EXTRA_BARE = {"honolulu": ("HI", "15003"), "louisville": ("KY", "21111"),
              "st. louis": ("MO", None), "saint louis": ("MO", None),
              "st. paul": ("MN", "27123"), "saint paul": ("MN", "27123"),
              "new york city": ("NY", None), "nyc": ("NY", None),
              "nueva orleans": ("LA", "22071"), "filadelfia": ("PA", "42101")}
BARE_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(
    map(re.escape, set(BARE_CITIES) | set(EXTRA_BARE)), key=len, reverse=True)) + r")(?![\w-])", re.I)

_URL = re.compile(r"https?://\S+|www\.\S+")
_HANDLE = re.compile(r"@\w+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _cap_ok(orig: str, has_lower: bool) -> bool:
    return (not has_lower) or orig[:1].isupper()


def _suffixes(place: str):
    """'In Weld' -> 'in weld', 'weld' (the capture can swallow a capitalised lead word)."""
    words = place.split()
    for i in range(len(words)):
        yield xl.norm(_strip_accents(" ".join(words[i:])))


def geocode_text(text: str) -> dict:
    out = {"geo_class": "none", "state": None, "county_fips": None, "geo_tier": None,
           "geo_matched": None, "n_states": 0, "foreign_marker": False, "ambiguous": False}
    if not isinstance(text, str) or not text.strip():
        return out
    cd, kd, ks, bd = _tables()
    text = unicodedata.normalize("NFKC", text)   # styled Unicode capitals -> plain letters
    t = _HANDLE.sub(" ", _URL.sub(" ", text)).replace("#", " ")
    t = STOP.sub(" ", t)
    if FOREIGN_PHRASE.search(t):
        out["foreign_marker"] = True
        t = FOREIGN_PHRASE.sub(" ", t)
    has_lower = any(c.islower() for c in t)
    hits: list[tuple[str, str, str | None, str]] = []      # (tier, state, county_fips, matched)

    def blank(m):                                           # keep offsets, hide the span
        return " " * (m.end() - m.start())

    # DC and Kansas City first: both contain another state's name
    if DC_RE.search(t):
        for m in DC_RE.finditer(t):
            if m.group(0).upper() == "DC" and not DC_CAPS.fullmatch(m.group(0)):
                continue
            hits.append(("city", "DC", "11001", m.group(0)))
        t = DC_RE.sub(blank, t)
    for m in KC_RE.finditer(t):
        if _cap_ok(m.group(0), has_lower):
            hits.append(("state", "KS" if m.group(1) else "MO", None, m.group(0)))
    t = KC_RE.sub(blank, t)

    # "Place, ST" and "Place, Minn." validated against the gazetteer
    for rx, table in ((ABBR_RE, None), (AP_RE, AP)):
        for m in rx.finditer(t):
            st = table[m.group(2)] if table else m.group(2)
            for cand in _suffixes(m.group(1)):
                cand_c = re.sub(r" (county|parish)$", "", cand)
                if cand_c != cand and (st, cand_c) in kd:
                    hits.append(("county", st, kd[(st, cand_c)], m.group(0)))
                    break
                fips, found = _city(cd, st, cand)
                if found:
                    hits.append(("city", st, fips, m.group(0)))
                    break

    # county names; the state is settled below
    county_names = []
    for rx in (COUNTY_RE, CONDADO_RE):
        for m in rx.finditer(t):
            county_names.append((list(_suffixes(m.group(1))), m.group(0)))
        t_wo = rx.sub(blank, t) if rx is COUNTY_RE else t
        t = t_wo

    # full state names
    georgia_country = bool(GEORGIA_COUNTRY.search(t))
    federal = bool(FEDERAL.search(t))
    wa_state = bool(WA_STATE.search(t))
    state_spans = []
    for m in STATE_RE.finditer(t):
        raw = m.group(1)
        if not _cap_ok(raw, has_lower):
            continue
        st = _names[raw.lower()]
        if st == "GA" and georgia_country:
            out["foreign_marker"] = True
            continue
        if st == "WA" and not wa_state:
            if federal:
                continue
            out["ambiguous"] = True
        state_spans.append((m.start(), m.end()))
        # "Willmar, Minnesota": a gazetteer city directly before the state name
        pre = re.search(rf"({PLACE}),\s*$", t[max(0, m.start() - 60):m.start()])
        tier, fips = "state", None
        if pre:
            for cand in _suffixes(pre.group(1)):
                f, found = _city(cd, st, cand)
                if found:
                    tier, fips = "city", f
                    break
        hits.append((tier, st, fips, raw if tier == "state" else f"{pre.group(1)}, {raw}"))
    for a, b in state_spans:
        t = t[:a] + " " * (b - a) + t[b:]

    # bare big cities
    for m in BARE_RE.finditer(t):
        if not _cap_ok(m.group(1), has_lower) and m.group(1) != "NYC":
            continue
        key = m.group(1).lower()
        if key in EXTRA_BARE:
            st, fips = EXTRA_BARE[key]
        elif key in bd:
            st, fips = bd[key]
        else:
            continue
        hits.append(("city_nostate", st, fips, m.group(1)))

    # counties: with a named state if one fits, else only if the name is in one state
    named = {h[1] for h in hits}
    for cands, matched in county_names:
        done = False
        for cand in cands:
            fit = [s for s in named if (s, cand) in kd]
            if len(fit) == 1:
                hits.append(("county", fit[0], kd[(fit[0], cand)], matched))
                done = True
                break
        if done or named:
            continue
        for cand in cands:
            states = ks.get(cand, [])
            if len(states) == 1:
                hits.append(("county", states[0], kd[(states[0], cand)], matched))
                break

    if FOREIGN.search(t):
        out["foreign_marker"] = True
    states = {h[1] for h in hits}
    out["n_states"] = len(states)
    if not states:
        out["geo_class"] = "foreign" if out["foreign_marker"] else "none"
        return out
    out["geo_matched"] = " | ".join(dict.fromkeys(h[3] for h in hits))[:200]
    if len(states) > 1:
        out["geo_class"] = "multi"
        return out
    order = {"county": 0, "city": 1, "city_nostate": 2, "state": 3}
    best = min(hits, key=lambda h: order[h[0]])
    fips = {h[2] for h in hits if h[2]}
    out.update(geo_class="us", state=best[1], geo_tier=best[0],
               county_fips=fips.pop() if len(fips) == 1 else None)
    if out["county_fips"] is None and out["geo_tier"] in ("county",):
        out["geo_tier"] = "state"          # two different counties named: state level only
    return out


@functools.lru_cache(maxsize=1)
def _tables():
    """Gazetteer lookups as plain dicts (pandas .loc is far too slow per post).
    city:   (state, name) -> county FIPS, or None for a multi-county city
    county: (state, name) -> county FIPS
    county_states: name -> list of states that have a county of that name
    bare:   name -> (state, county FIPS or None)"""
    city, county, bare, _ = xl._lookups()
    cd = {k: (r.county_fips if r.county_share >= xl.MIN_SHARE else None)
          for k, r in zip(city.index, city.itertuples())}
    kd = dict(zip(county.index, county["county_fips"]))
    ks: dict[str, list[str]] = {}
    for (st, name) in kd:
        ks.setdefault(name, []).append(st)
    bd = {k: (r.state, r.county_fips if r.county_share >= xl.MIN_SHARE else None)
          for k, r in zip(bare.index, bare.itertuples())}
    return cd, kd, ks, bd


def _city(cd: dict, state: str, name: str):
    """(county FIPS or None, found), as x_location._city."""
    name = name.strip(" -'")
    if not name or xl.REGIONAL.match(name):
        return None, False
    for cand in (name, re.sub(r"^(city of|town of|village of)\s+", "", name)):
        if (state, cand) in cd:
            return cd[(state, cand)], True
    return None, False


if __name__ == "__main__":
    tests = [
        "Bird flu confirmed in a backyard flock in Weld County, Colorado.",
        "Dead geese found at Lake Harriet in Minneapolis test positive for avian flu",
        "WASHINGTON (AP) — The White House said bird flu funding ...",
        "Avian flu found in Washington state dairy cattle",
        "Bird flu has been confirmed in mammals inhabiting South Georgia.",
        "HPAI detected in Maricopa County",
        "La gripe aviar llega a Nuevo México y Texas",
        "Gripe aviar en Tijuana, Baja California",
        "Mass die-off of snow geese near Willmar, MN",
        "California condors are being vaccinated against bird flu",
        "Avian influenza detected in Delaware County, Pennsylvania",
        "bird flu is spreading in peru and chile",
        "Officials in St. Paul, Minn., said the flock was depopulated",
        "A second case in Kansas City, Kansas",
        "MICHIGAN: bird flu found in dairy herd",
        "the nevada of the sierra",
        "BAJA CALIFORNIA SE MANTIENE LIBRE DE GRIPE AVIAR, a diferencia de California",
        "\U0001d5d5\U0001d5dc\U0001d5e5\U0001d5d7 \U0001d5d9\U0001d5df\U0001d5e8 in \U0001d5e0\U0001d5f6\U0001d5fb\U0001d5fb\U0001d5f2\U0001d600\U0001d5fc\U0001d601\U0001d5ee",
        "Dead swans at a pond in Madison, Wis.",
    ]
    for s in tests:
        r = geocode_text(s)
        print(f"{r['geo_class']:8} {str(r['state']):5} {str(r['county_fips']):6} "
              f"{str(r['geo_tier']):13} amb={r['ambiguous']!s:5} for={r['foreign_marker']!s:5} | {s[:70]}")
