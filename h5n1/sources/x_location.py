"""Geocode free-text X profile locations ("Rochester, MN", "30248", "Boston") to a US state
and, where the text supports it, a 2021-vintage county FIPS.

WHAT THE INPUT IS. X gives only the author's self-reported profile location -- no post
geotag. It is where the author says they live, not where the post is about, and 54% of
it is blank or fiction ("TARDIS Station"). The modeling grain for X is therefore the
STATE; counties are kept for inspection only (about 1% of county-days have any post).

LEVELS, most specific first:
    zip           5-digit ZCTA -> the county holding most of its land
    county        "Meeker County, MN"
    city          "Willmar MN" (Census place or county subdivision); county assigned only
                  when >= MIN_SHARE of the place's land is in one county, so NYC,
                  Oklahoma City and Kansas City MO stay state-level
    city_nostate  unambiguous large US city with no state ("Chicago", "Portland")
    state         state only, or a multi-county city
    us            "USA" and nothing finer
    multi         names more than one state ("Missouri, NY, California")
    foreign       clearly outside the US
    none          blank / unparseable

REFERENCE DATA (2021 vintage, legacy Connecticut counties 09001-09015, like dim_county):
Census 2021 gazetteers (places, county subdivisions, counties), the 2021 cartographic
place and county boundaries (for place -> county land shares), the 2020 ZCTA-county
relationship file (2020 still has the legacy CT counties), and 2021 place population
estimates. Downloaded once into data/reference/census_geo/ (gitignored) and reduced to
two parquet lookups.

Measured 2026-09-28 on the prior-cohort pull (364k posts): 24% of posts reach a state,
13.7% a county; a 45-string hand check was ~95% right. Known miss: bare "Kansas City"
resolves to the Kansas side.
"""
from __future__ import annotations

import functools
import pathlib
import re
import zipfile

import pandas as pd
import requests

REF_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "reference" / "census_geo"
MIN_SHARE = 0.8
URLS = {
    "county_shp": "https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_2021_us_county_500k.zip",
    "place_shp": "https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_2021_us_place_500k.zip",
    "gaz_place": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2021_Gazetteer/2021_Gaz_place_national.zip",
    "gaz_cousub": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2021_Gazetteer/2021_Gaz_cousubs_national.zip",
    "gaz_county": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2021_Gazetteer/2021_Gaz_counties_national.zip",
    "zcta_county": "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt",
    "place_pop": "https://www2.census.gov/programs-surveys/popest/datasets/2020-2021/cities/totals/sub-est2021_all.csv",
}

ST = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
      "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
      "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
      "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
      "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
      "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
      "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
      "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
      "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
      "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
      "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming"}
STATE_FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
              "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
              "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
              "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
              "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
              "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
              "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
              "WI": "55", "WY": "56"}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}
NAME2AB = {v: k for k, v in ST.items()}
NAME2AB.update({"calif": "CA", "cali": "CA", "mass": "MA", "penn": "PA", "tenn": "TN", "minn": "MN",
                "wisc": "WI", "mich": "MI", "washington dc": "DC", "wash dc": "DC"})
# longest first, so "west virginia" beats "virginia"
STATE_NAME_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, NAME2AB), key=len, reverse=True)) + r")\b")
# two-letter codes count only when written in capitals ("in", "me", "or", "hi" are words)
AB_RE = re.compile(r"(?:^|[\s,/|(])(" + "|".join(ST) + r")(?=$|[\s,/|).])")

FOREIGN = re.compile(
    r"\b(canada|canadian|uk|u\.k|united kingdom|great britain|britain|england|scotland|wales|ireland|"
    r"london|manchester|liverpool|glasgow|edinburgh|dublin|belfast|india|pakistan|bangladesh|"
    r"sri lanka|nepal|australia|aussie|new zealand|nz|nigeria|kenya|ghana|south africa|uganda|"
    r"zimbabwe|philippines|indonesia|malaysia|singapore|thailand|vietnam|japan|china|hong kong|"
    r"taiwan|korea|germany|deutschland|france|paris|italy|italia|spain|espana|portugal|netherlands|"
    r"nederland|holland|belgium|sweden|norway|denmark|finland|poland|austria|switzerland|greece|"
    r"turkey|israel|egypt|uae|dubai|saudi|qatar|iran|brazil|brasil|argentina|chile|colombia|peru|"
    r"mexico|venezuela|cuba|jamaica|toronto|ontario|quebec|montreal|ottawa|british columbia|"
    r"alberta|calgary|edmonton|manitoba|winnipeg|saskatchewan|nova scotia|new brunswick|"
    r"newfoundland|halifax|melbourne|sydney|brisbane|perth|adelaide|queensland|new south wales|nsw|"
    r"auckland|wellington|lagos|nairobi|delhi|mumbai|bangalore|bengaluru|karachi|lahore|manila|"
    r"jakarta|tokyo|beijing|shanghai|berlin|munich|amsterdam|brussels|stockholm|oslo|copenhagen|"
    r"madrid|barcelona|lisbon|rome|milan|vienna|zurich|geneva|europe|eu|asia|africa)\b")
CA_PROV_AB = re.compile(r",\s*(on|bc|ab|qc|ns|nb|mb|sk|nl|pe|pei)\s*$")
US_RE = re.compile(r"\b(usa|u\.s\.a|u\.s|us|united states|america|estados unidos)\b")
US_WORDS = re.compile(r"\b(usa|us|united states)\b")
ALIAS = {"nyc": ("NY", None), "new york city": ("NY", None), "brooklyn": ("NY", "36047"),
         "manhattan": ("NY", "36061"), "bronx": ("NY", "36005"), "queens": ("NY", "36081"),
         "staten island": ("NY", "36085"), "la": ("CA", None), "sf": ("CA", "06075"),
         "san francisco bay area": ("CA", None), "bay area": ("CA", None), "socal": ("CA", None),
         "norcal": ("CA", None), "silicon valley": ("CA", None), "philly": ("PA", "42101"),
         "dfw": ("TX", None), "atx": ("TX", "48453"), "htx": ("TX", None), "atl": ("GA", None),
         "chi": ("IL", "17031"), "chitown": ("IL", "17031"), "nola": ("LA", "22071"),
         "pdx": ("OR", None), "dmv": ("DC", None), "dc": ("DC", "11001"), "d c": ("DC", "11001"),
         "washington dc": ("DC", "11001"), "twin cities": ("MN", None), "stl": ("MO", None),
         "kc": ("MO", None), "tampa bay": ("FL", None), "south florida": ("FL", None),
         "long island": ("NY", None), "upstate ny": ("NY", None), "upstate new york": ("NY", None),
         "hudson valley": ("NY", None), "cape cod": ("MA", "25001")}
# large US cities whose bare name usually means somewhere else, or is too common to trust
AMBIGUOUS_BARE = {"london", "paris", "vancouver", "birmingham", "cambridge", "richmond", "hamilton",
                  "athens", "victoria", "kingston", "windsor", "waterloo", "burlington", "perth",
                  "melbourne", "sydney", "dublin", "manchester", "rome", "berlin", "toronto", "surrey",
                  "oxford", "lancaster", "york", "durham", "salem", "springfield", "columbia", "aurora",
                  "glendale", "peoria", "pasadena"}
# region words that the gazetteer would otherwise read as a town ("Central IL", "Lake Michigan")
REGIONAL = re.compile(r"^(the\s+)?(central|north|south|east|west|northern|southern|eastern|western|"
                      r"northeast|northwest|southeast|southwest|ne|nw|se|sw|upstate|downstate|rural|"
                      r"coastal|greater|metro|lake|mount|mountains|hill country|panhandle|gulf coast|"
                      r"somewhere in|outside|near|born in|living in|based in)(\s|$)")
LSAD = re.compile(r"\s+(city and borough|consolidated government|unified government|metro government|"
                  r"metropolitan government|city|town|village|borough|cdp|municipality|township|"
                  r"plantation|charter township|urban county|corporation|comunidad|zona urbana|ccd|"
                  r"gore|grant|location|purchase|unorganized territory|reservation)$")


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\bst\.?\s", "saint ", s)
    s = re.sub(r"\bft\.?\s", "fort ", s)
    s = re.sub(r"\bmt\.?\s", "mount ", s)
    s = re.sub(r"[^a-z0-9 ,'/|-]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def _base_name(full: str) -> str:
    n = norm(full)
    n = re.sub(r"\s*-\s*.*(balance|government).*$", "", n)  # "Nashville-Davidson metropolitan government"
    return LSAD.sub("", n).strip()


def _download() -> None:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    for url in URLS.values():
        dest = REF_DIR / url.rsplit("/", 1)[1]
        if not dest.exists():
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            dest.write_bytes(r.content)


def _gaz(name: str) -> pd.DataFrame:
    with zipfile.ZipFile(REF_DIR / URLS[name].rsplit("/", 1)[1]) as z:
        with z.open(z.namelist()[0]) as fh:
            df = pd.read_csv(fh, sep="\t", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df


def build_reference() -> None:
    """Download the Census files and write gaz.parquet + zcta.parquet. Idempotent."""
    import geopandas as gpd

    _download()
    counties = gpd.read_file(f"zip://{REF_DIR / 'cb_2021_us_county_500k.zip'}").to_crs(5070)
    places = gpd.read_file(f"zip://{REF_DIR / 'cb_2021_us_place_500k.zip'}").to_crs(5070)
    places = places[["GEOID", "geometry"]].assign(parea=lambda d: d.area)
    ov = gpd.overlay(places, counties[["GEOID", "geometry"]].rename(columns={"GEOID": "county_fips"}),
                     how="intersection", keep_geom_type=True)
    ov["share"] = ov.area / ov["parea"]
    ov = ov.sort_values("share", ascending=False).drop_duplicates("GEOID")[["GEOID", "county_fips", "share"]]

    pop = pd.read_csv(REF_DIR / "sub-est2021_all.csv", dtype=str, encoding="latin-1")
    pop = pop[pop.SUMLEV == "162"].assign(GEOID=lambda d: d.STATE + d.PLACE)[["GEOID", "POPESTIMATE2021"]]
    pg = _gaz("gaz_place").merge(ov, on="GEOID", how="left").merge(pop, on="GEOID", how="left")
    pg = pd.DataFrame({"state": pg.USPS, "name": pg.NAME.map(_base_name), "county_fips": pg.county_fips,
                       "county_share": pg.share.astype(float),
                       "pop": pd.to_numeric(pg.POPESTIMATE2021).fillna(0), "src": "place"})
    cs = _gaz("gaz_cousub")
    cs = cs[~cs.NAME.str.contains(r"CCD|UT$|unorganized", case=False)]
    cs = pd.DataFrame({"state": cs.USPS, "name": cs.NAME.map(_base_name), "county_fips": cs.GEOID.str[:5],
                       "county_share": 1.0, "pop": 0.0, "src": "cousub"})  # cousubs nest in counties
    cg = _gaz("gaz_county")
    cg = pd.DataFrame({"state": cg.USPS,
                       "name": cg.NAME.map(lambda s: re.sub(r"\s+(county|parish|borough|census area|city and "
                                                            r"borough|municipality)$", "", norm(s))),
                       "county_fips": cg.GEOID, "county_share": 1.0, "pop": 0.0, "src": "county"})
    gaz = pd.concat([pg, cs, cg], ignore_index=True).dropna(subset=["county_fips"])
    gaz = gaz[gaz.name != ""]
    if gaz.county_fips.str.match(r"^091\d\d$").any():  # the project-wide CT guard
        raise ValueError("CT planning-region FIPS (091xx) in the gazetteer; expected 2021 vintage")
    gaz.to_parquet(REF_DIR / "gaz.parquet", index=False)

    z = pd.read_csv(REF_DIR / "tab20_zcta520_county20_natl.txt", sep="|", dtype=str)
    z = z[z.GEOID_ZCTA5_20.fillna("") != ""].copy()
    z["part"] = pd.to_numeric(z.AREALAND_PART)
    z["share"] = z.part / z.groupby("GEOID_ZCTA5_20").part.transform("sum").where(lambda s: s > 0)
    z = z.sort_values("share", ascending=False).drop_duplicates("GEOID_ZCTA5_20")
    z.rename(columns={"GEOID_ZCTA5_20": "zip", "GEOID_COUNTY_20": "county_fips", "share": "county_share"})[
        ["zip", "county_fips", "county_share"]].to_parquet(REF_DIR / "zcta.parquet", index=False)


@functools.lru_cache(maxsize=1)
def _lookups():
    if not (REF_DIR / "gaz.parquet").exists():
        build_reference()
    gaz = pd.read_parquet(REF_DIR / "gaz.parquet")
    zc = pd.read_parquet(REF_DIR / "zcta.parquet").set_index("zip")
    gaz["r"] = gaz.src.map({"place": 0, "cousub": 1, "county": 2})
    # same name twice in a state: prefer the incorporated place, then the most populous
    city = (gaz[gaz.src != "county"].sort_values(["r", "pop"], ascending=[True, False])
            .drop_duplicates(["state", "name"]).set_index(["state", "name"]))
    county = gaz[gaz.src == "county"].drop_duplicates(["state", "name"]).set_index(["state", "name"])
    # bare city name: >= 100k people and >= 5x the next place of that name anywhere
    pl = gaz[gaz.src == "place"].sort_values("pop", ascending=False)
    top2 = pl.groupby("name")["pop"].apply(lambda p: list(p.head(2)) + [0])
    big = {n for n, p in top2.items() if p[0] >= 100_000 and p[0] >= 5 * p[1] and n not in AMBIGUOUS_BARE}
    bare = pl[pl.name.isin(big)].drop_duplicates("name").set_index("name")
    return city, county, bare, zc


def _city(state: str, name: str):
    """(county_fips or None, found). found=True with None means a multi-county city."""
    city, *_ = _lookups()
    name = name.strip(" -'")
    if not name or REGIONAL.match(name):
        return None, False
    for cand in (name, re.sub(r"^(city of|town of|village of)\s+", "", name)):
        if (state, cand) in city.index:
            r = city.loc[(state, cand)]
            return (r.county_fips if r.county_share >= MIN_SHARE else None), True
    return None, False


def geocode(raw) -> tuple[str, str | None, str | None]:
    """Profile location -> (level, state USPS code, county FIPS). See the module docstring."""
    city, county, bare, zc = _lookups()
    if not isinstance(raw, str) or not raw.strip():
        return ("none", None, None)
    orig = raw.strip()
    s = norm(orig)
    if not s:
        return ("none", None, None)
    caps = [m.group(1) for m in AB_RE.finditer(orig.replace(".", ""))]
    named = {NAME2AB[m.group(1)] for m in STATE_NAME_RE.finditer(s)}
    if len(set(caps) | (named - {"DC"})) > 1 and not re.search(r"\b(new york|washington)\b", s):
        return ("multi", None, None)
    state = caps[-1] if caps else (NAME2AB[STATE_NAME_RE.search(s).group(1)] if named else None)
    if state == "WA" and re.search(r"\bd ?c\b", s):
        state = "DC"
    foreign = bool(FOREIGN.search(s) or CA_PROV_AB.search(s))
    if foreign and not state:
        return ("foreign", None, None)
    if state == "NY" and re.fullmatch(r"new york( city)?( ny)?( usa| us)?", s.replace(",", "")):
        return ("state", "NY", None)  # city or state; either spans several counties

    mz = re.search(r"(?<!\d)(\d{5})(?:-\d{4})?(?!\d)", s)
    if mz and mz.group(1) in zc.index and not foreign:
        fips = zc.loc[mz.group(1)].county_fips
        z_state = FIPS_STATE.get(fips[:2])
        if not state or z_state == state:
            return ("zip", z_state, fips)

    segs = [p.strip() for p in re.split(r"[,/|]| - ", s) if p.strip()]
    for seg in [s] + segs:
        seg = US_WORDS.sub("", seg).strip()
        if seg in ALIAS and (not state or state == ALIAS[seg][0]):
            a_state, a_fips = ALIAS[seg]
            return ("city" if a_fips else "state", a_state, a_fips)

    if state:
        for seg in segs:
            m = re.fullmatch(r"(.+?) (county|parish|co)", US_WORDS.sub("", seg).strip())
            if m and (state, m.group(1)) in county.index:
                return ("county", state, county.loc[(state, m.group(1))].county_fips)
        if len(segs) >= 2:
            cand = segs[0]
        else:  # "Rochester MN" / "Des Moines Iowa": drop the trailing state token
            tokens = "|".join([k.lower() for k in ST] + [re.escape(n) for n in NAME2AB])
            cand = re.sub(rf"\s+({tokens})(\s+(usa|us))?$", "", s)
        cand = US_WORDS.sub("", cand).strip(" -")
        if cand and cand not in NAME2AB and cand != state.lower():
            fips, found = _city(state, cand)
            if found:
                return ("city", state, fips) if fips else ("state", state, None)
        return ("state", state, None)

    for seg in segs:
        seg = US_WORDS.sub("", seg).strip()
        if seg in bare.index:
            r = bare.loc[seg]
            return ("city_nostate", r.state, r.county_fips if r.county_share >= MIN_SHARE else None)
    if US_RE.search(s):
        return ("us", None, None)
    return ("none", None, None)


def geocode_series(locations: pd.Series) -> pd.DataFrame:
    """Geocode each distinct string once; returns level/state/county_fips aligned to the input."""
    loc = locations.fillna("").astype(str).str.strip()
    uniq = loc.drop_duplicates()
    res = pd.DataFrame([geocode(v) for v in uniq], columns=["geo_level", "state", "county_fips"],
                       index=uniq.values)
    return res.reindex(loc.values).set_index(locations.index)


if __name__ == "__main__":
    build_reference()
    for s in ["Rochester, MN", "Meeker County, MN", "30248", "Boston", "New York, NY", "Central IL",
              "Ontario, Canada", "Missouri, NY, California", "TARDIS Station"]:
        print(f"{s!r:32} {geocode(s)}")
