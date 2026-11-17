"""Resolve a US STATE from social-media post text.

WHAT THIS IS FOR. The Meta Content Library exports no location field of any kind --
all 36 columns were checked against loc|geo|countr|place|city|state|region and none
matched. Page names carry almost nothing: of 63,840 distinct pages, 1,117 (1.7%)
contain a US state name. So h5n1.sources.x_location, which geocodes an author's
free-text PROFILE location, has no input to work from here. This module reads the
post text instead.

That is a different question, and a better one. The profile field says where the
author lives; the text says what the post is about. A Minnesota account reporting a
cull in Iowa is evidence about Iowa. Because it needs no platform-specific field,
the same extractor serves Meta and X.

GRAIN IS THE STATE, deliberately. Measured on 4,001 English Meta posts (2026-10-28):
21.7% resolve to a state but only 1.0% to a county, because county names need a
state disambiguator the text rarely supplies ("Polk County" exists in FL, IA, OR and
more). x_location.py reached the same conclusion from the other direction: "the
modeling grain for X is therefore the STATE; counties are kept for inspection only".
A county_fips is returned when the text is unambiguous, for inspection, never as the
modeling key.

WHAT THIS DOES NOT DO. It finds the places a post NAMES. It does not judge whether
the post is REPORTING AN EVENT there -- "unlike Iowa, our state has had no cases"
names Iowa and means the opposite. That judgement is a separate LLM step; see
docs/Geolocation_Extraction.docx. Keeping the two apart is what makes this half reproducible.

    from h5n1.sources.meta_location import Gazetteer
    gz = Gazetteer.from_db(engine)
    gz.extract("Avian flu confirmed in Sioux County, Iowa")
    # -> Match(tier='T1_county_state', state='IA', county_fips='19167', ...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

STATE_NAME = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
ABBR = set(STATE_NAME.values())

# SPANISH. 41.9% of the Meta corpus is Spanish, and several US states are spelled
# differently: without these, "Nueva York" and "Pensilvania" return nothing, and a
# post naming two states looks like a confident single-state post.
SPANISH_STATE = {
    "nueva york": "NY", "carolina del norte": "NC", "carolina del sur": "SC",
    "dakota del norte": "ND", "dakota del sur": "SD", "nuevo mexico": "NM",
    "nuevo m\u00e9xico": "NM", "luisiana": "LA", "pensilvania": "PA",
    "virginia occidental": "WV", "virginia del oeste": "WV",
    "nueva jersey": "NJ", "nuevo hampshire": "NH", "misisipi": "MS",
    "misisip\u00ed": "MS", "misuri": "MO", "haw\u00e1i": "HI",
    "nueva inglaterra": None,          # a region, not a state -- never resolve it
    "tejas": "TX", "georgia": "GA",
}

# FRENCH. X has 20,903 French posts; Meta has none. Names that are the same in
# French (Texas, Iowa, Oregon...) are already covered by STATE_NAME.
FRENCH_STATE = {
    "californie": "CA", "floride": "FL", "g\u00e9orgie": "GA", "louisiane": "LA",
    "pennsylvanie": "PA", "virginie": "VA", "virginie-occidentale": "WV",
    "virginie occidentale": "WV", "caroline du nord": "NC", "caroline du sud": "SC",
    "caroline-du-nord": "NC", "caroline-du-sud": "SC",          # French often hyphenates
    "dakota du nord": "ND", "dakota du sud": "SD", "dakota-du-nord": "ND",
    "dakota-du-sud": "SD", "nouveau-mexique": "NM", "nouveau mexique": "NM",
    "nouvelle-orl\u00e9ans": "LA", "hawa\u00ef": "HI", "new hampshire": "NH",
    "new-york": "NY", "new-jersey": "NJ",
}

# FALSE FRIENDS. These US state names are ordinary Spanish words:
#   nevada    = snowfall      colorado = red/ruddy
#   montana   = mountain      florida  = flowering, in bloom
# Matching them case-insensitively turns "una nevada fuerte" (a heavy snowfall)
# into Nevada. They are therefore matched CASE-SENSITIVELY -- a capital initial is
# the only thing distinguishing the place from the word.
FALSE_FRIENDS = {"nevada", "colorado", "montana", "florida", "california",
                 "indiana", "oregon"}

# Foreign places that CONTAIN a US state name. "Baja California" is Mexican and
# must be caught before "california" matches. Checked first, always.
FOREIGN_CONTAINING_STATE = re.compile(
    r"\b(baja california(?:\s+sur)?|nuevo le[o\u00f3]n|california del sur)\b", re.I)

# Postal codes that are also ordinary English words. Matching these bare is the
# single most common way a naive extractor fails -- "a farm in OR" is the word "or".
# They count only when a comma precedes them, as in "Willmar, MN".
AMBIGUOUS_ABBR = {"IN", "OR", "OK", "HI", "ME", "DE", "LA", "MS", "PA",
                  "MD", "MA", "CO", "ID", "AL", "AR"}

# Datelines tag federal-policy stories to the capital. sql/013_gdelt.sql records the
# same effect in GDELT: DC holds 30,558 articles, six times the next county.
FEDERAL = re.compile(
    r"\b(usda|aphis|cdc|fda|white house|congress|federal|"
    r"department of agriculture|secretary)\b", re.I)

# "Washington" alone means the capital far more often than the state in a federal
# story. Without this, a USDA dateline lands in Washington STATE -- the same
# media-hub failure as DC, one state over. This was caught in testing, not theory.
CAPITAL = re.compile(r"\bwashington\b(?!\s+state)", re.I)

# Clearly non-US places. Not exhaustive; it exists so "Belgium will cull 55,000
# chickens" returns `foreign` rather than `none`, which are different findings.
FOREIGN = re.compile(
    r"\b(canada|mexico|m[eé]xico|britain|england|scotland|wales|ireland|uk\b|"
    r"france|spain|espa[nñ]a|germany|belgium|netherlands|poland|italy|"
    r"japan|china|korea|india|vietnam|cambodia|australia|brazil|brasil|"
    r"argentina|chile|colombia|peru|per[uú]|venezuela|ecuador|bolivia|"
    r"british columbia|ontario|quebec|alberta|saskatchewan|manitoba)\b", re.I)

# French additions, applied ONLY to posts tagged lang='fr' (X has French; Meta does
# not). Kept out of the shared lists so Meta results stay exactly as built: "Argentine"
# is also an English word and "Japon" is Spanish for Japan written without its accent.
FRENCH_STATE_RE = re.compile(
    r"\b(" + "|".join(sorted(FRENCH_STATE, key=len, reverse=True)) + r")\b", re.I)
# Places that CONTAIN a US state name but are not in the US. "Géorgie du Sud" is the
# South Atlantic island (Spanish: Georgia del Sur); Maine-et-Loire is a French
# department, Haut-/Bas-Maine parts of the old province. Bare "le Maine" is NOT
# blocked: French uses the same words for the US state.
FRENCH_FOREIGN_CONTAINING_STATE = re.compile(
    r"\b(basse-californie(?:\s+du\s+sud)?|g[ée]orgie[\s-]du[\s-]sud|maine-et-loire|"
    r"(?:haut|bas)-maine)\b", re.I)
# The federal government, in French: guards "Washington" from becoming the state.
FRENCH_FEDERAL = re.compile(
    r"\b(maison[\s-]blanche|congr[èe]s|f[ée]d[ée]ral|gouvernement am[ée]ricain|"
    r"d[ée]partement de l'agriculture)", re.I)
FRENCH_FOREIGN = re.compile(
    r"\b(mexique|royaume-uni|angleterre|[ée]cosse|irlande|allemagne|belgique|pays-bas|"
    r"pologne|italie|espagne|japon|chine|cor[ée]e|inde|cambodge|australie|br[ée]sil|"
    r"argentine|chili|colombie|p[ée]rou|[ée]quateur|bolivie|qu[ée]bec)\b", re.I)

COUNTY_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(?:County|Parish|Co\.)"
    r"(?:[,\s]+(?:in\s+)?([A-Z][a-z]+(?:\s[A-Z][a-z]+)?|[A-Z]{2})\b)?")
CITY_ST_RE = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*([A-Z]{2})\b")
_SAFE = [k for k in list(STATE_NAME) + [k for k in SPANISH_STATE if SPANISH_STATE[k]]
         if k not in FALSE_FRIENDS]
_SAFE.sort(key=len, reverse=True)          # longest first: "nuevo mexico" before "mexico"
STATE_RE = re.compile(r"\b(" + "|".join(_SAFE) + r")\b", re.I)

# Two readings of the false friends, picked by the post's language.
#   English  -> case-insensitive: "nevada" has no other meaning, and social-media
#               headlines are often ALL CAPS or all lower.
#   Spanish  -> Capitalised or ALL CAPS only, because "una nevada fuerte" is a
#               heavy snowfall and "el gallo colorado" is a red rooster.
_FF = sorted(FALSE_FRIENDS, key=len, reverse=True)
FALSE_FRIEND_ANY = re.compile(r"\b(" + "|".join(_FF) + r")\b", re.I)
FALSE_FRIEND_CAP = re.compile(
    r"\b(" + "|".join(f"{f.capitalize()}|{f.upper()}" for f in _FF) + r")\b")

ALL_STATE = {**STATE_NAME, **{k: v for k, v in SPANISH_STATE.items() if v}, **FRENCH_STATE}

COUNTY_SUFFIX = r"\s+(county|parish|borough|city and borough|census area|municipality)$"


@dataclass(frozen=True)
class Match:
    tier: str
    state: str | None
    county_fips: str | None
    matched: str | None


def _state_token(tok: str | None) -> str | None:
    """'Iowa' or 'IA' -> 'IA'. None if it is neither."""
    if not tok:
        return None
    t = tok.strip()
    if len(t) == 2 and t.upper() in ABBR:
        return t.upper()
    return ALL_STATE.get(t.lower())


class Gazetteer:
    """County lookups built from dim_county, so the FIPS vintage matches the
    warehouse (2021, legacy Connecticut counties 09001-09015)."""

    def __init__(self, by_state: dict, unique: dict, fips_state: dict | None = None):
        self.by_state = by_state
        self.unique = unique
        # '06' -> 'CA'. A bare county gives a FIPS code; its first two digits name the
        # state numerically, and the state must be returned as a postal code like the
        # other tiers. (Until 2026-11-07, T4 returned the two digits themselves.)
        self.fips_state = fips_state or {}

    @classmethod
    def from_db(cls, engine) -> "Gazetteer":
        df = pd.read_sql("SELECT fips, county_name, state FROM dim_county", engine)
        df["base"] = (df.county_name.str.lower()
                        .str.replace(COUNTY_SUFFIX, "", regex=True).str.strip())
        by_state = dict(zip(zip(df.base, df.state.str.lower()), df.fips))
        # A bare county name is admitted ONLY where it occurs in exactly one state
        # nationally. "Sioux" is excluded automatically: IA, NE and ND all have one.
        n = df.groupby("base").fips.count()
        uq = df[df.base.map(n) == 1]
        return cls(by_state, dict(zip(uq.base, uq.fips)),
                   dict(zip(df.fips.str[:2], df.state)))

    def extract(self, text: str | None, lang: str | None = None) -> Match:
        """Highest-confidence US state in the text.

        `lang` is the post's language ('en' / 'es'). It decides how the false
        friends are read -- see FALSE_FRIEND_ANY above. Pass it whenever you have
        it; omitting it takes the cautious Spanish reading.
        """
        if not text:
            return Match("none", None, None, None)

        federal = bool(FEDERAL.search(text)) or (
            (lang or "").lower().startswith("fr") and bool(FRENCH_FEDERAL.search(text)))

        # "Baja California, Mexico" must not become California. Checked first.
        french = (lang or "").lower().startswith("fr")
        fcs = FOREIGN_CONTAINING_STATE.search(text) or (
            FRENCH_FOREIGN_CONTAINING_STATE.search(text) if french else None)
        if fcs:
            return Match("T6_foreign", None, None, fcs.group(0))

        named = {_state_token(m.group(1)) for m in STATE_RE.finditer(text)}
        if french:
            named |= {_state_token(m.group(1)) for m in FRENCH_STATE_RE.finditer(text)}
        ff = FALSE_FRIEND_ANY if (lang or "").lower().startswith("en") else FALSE_FRIEND_CAP
        named |= {_state_token(m.group(1)) for m in ff.finditer(text)}
        named.discard(None)

        # T1 -- county WITH a state. The only tier that yields a reliable FIPS.
        for name, st_tok in COUNTY_RE.findall(text):
            st = _state_token(st_tok)
            if not st or (federal and st == "DC"):
                continue
            fips = self.by_state.get((name.lower(), st.lower()))
            if fips:
                return Match("T1_county_state", st, fips, f"{name} County, {st}")

        # T2 -- "City, ST". The comma is what makes the abbreviation safe.
        for city, ab in CITY_ST_RE.findall(text):
            if ab not in ABBR:
                continue
            if ab in AMBIGUOUS_ABBR and f", {ab}" not in text:
                continue
            return Match("T2_city_state", ab, None, f"{city}, {ab}")

        # T5 -- several states named: not evidence about one place.
        if len(named) > 1:
            return Match("T5_multi", None, None, ",".join(sorted(named)))

        # T3 -- exactly one state named.
        if len(named) == 1:
            st = named.pop()
            if federal and st in ("DC", "WA") and CAPITAL.search(text):
                return Match("none", None, None, None)
            return Match("T3_state", st, None, st)

        # T4 -- bare county name that is nationally unique.
        for name, _ in COUNTY_RE.findall(text):
            fips = self.unique.get(name.lower())
            if fips:
                return Match("T4_county_unique", self.fips_state.get(fips[:2]), fips,
                             f"{name} County")

        # T6 -- names a foreign place and no US place.
        foreign = FOREIGN.search(text) or (FRENCH_FOREIGN.search(text) if french else None)
        if foreign:
            return Match("T6_foreign", None, None, foreign.group(0))

        return Match("none", None, None, None)

    def extract_series(self, texts: pd.Series,
                       langs: pd.Series | None = None) -> pd.DataFrame:
        lg = langs if langs is not None else pd.Series([None] * len(texts),
                                                       index=texts.index)
        out = [self.extract(t, lang) for t, lang in zip(texts, lg)]
        return pd.DataFrame(
            [(m.tier, m.state, m.county_fips, m.matched) for m in out],
            columns=["geo_tier", "state", "county_fips", "geo_matched"],
            index=texts.index,
        )
