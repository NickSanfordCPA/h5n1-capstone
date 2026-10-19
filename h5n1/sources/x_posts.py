"""X (Twitter) keyword-search posts -> deduplicated, geocoded, flagged post and author tables.

LOCAL ONLY. Outputs are parquet under data/X_derived/ (gitignored); nothing goes to GCS or
Postgres. Three STREAMS, each with its own output directory (Amendment 1 of
docs/x_event_preregistration.md):

  prior    the prior cohort's scraper export, one CSV per search term (data/X/*.csv),
           2022-06..2024-12 -> data/X_derived/. FROZEN: these are the training-period files
           the amendment reuses and hashes; rebuild them only with --out pointed elsewhere.
  en_test  x_post_unique, corpus twitterapi_en, originals + replies from 2025-01-01
           -> data/X_derived/test_2025/
  es       x_post_unique, corpus twitterapi_es, originals + replies from 2022-06-01
           -> data/X_derived/es/
  fr       x_post_unique, corpus twitterapi_fr, originals + replies from 2022-06-01
           -> data/X_derived/fr/ (added 2026-10-19; not in Amendment 1, so exploratory
           until an amendment names it)

    uv run python -m h5n1.sources.x_posts build    [--stream S]   # posts.parquet, authors.parquet
    uv run python -m h5n1.sources.x_posts biobatch  --stream S    # bios needing an LLM label
    uv run python -m h5n1.sources.x_posts accounts [--stream S]   # account_type + hub -> authors.parquet
    uv run python -m h5n1.sources.x_posts subset   [--stream S]   # topic_subset.parquet

SENTIMENT is not computed here: features read Waree's x_post_sentiment via waree_sentiment().

GRAIN. Geography is the author's profile location (see x_location): STATE is the modeling
grain. A post found by several search terms is one post; `terms` keeps them all.

WHAT IS FLAGGED, NOT DROPPED (the filters are applied downstream, so each is auditable):
  copypasta   the post's normalized 80-char prefix is shared by >= COPYPASTA_MIN posts
              from >= 2 authors -- coordinated campaigns and templated bots. The earliest
              post of each group is kept as the representative (copypasta_first). Flagged
              within each stream.
  hub-window author volume, from which the hub rule is applied once account types exist.

ACCOUNT TYPE (step `accounts`). Four groups: farmer_keeper, ag_institution, media_aggregator,
general_public. Authors whose bio has a farm/ag/animal-health term, plus every top-1% author,
are classified by an LLM reading the bio in-session (labels/bio_labels_*.csv; no API; step
`biobatch` writes what needs labelling). Every other author gets a regex fallback:
media_aggregator if the bio has a strong news term, else general_public. Labels from the
prior stream carry over to every stream.

HUB RULE -- fixed 2026-09-28, BEFORE any X feature was built or scored, mirroring the GDELT
media-hub rule (top 1% by hub-window volume AND no local stake):
  hub = hub-window post count in the top 1% of the stream's authors
        AND account_type NOT IN (farmer_keeper, ag_institution)
The hub window is pre-2025 for prior and es. en_test has no pre-2025 posts (the corpus
changes on 2025-01-01), so its window is all of its posts, and a prior-stream hub stays a hub
(Amendment 1, item 1). Hubs are dropped from every signal feature. Media accounts below the
volume cut are kept as their own stream (local outlets relaying a local story may carry
signal); they are not pooled with the public.

TOPIC SUBSET (step `subset`): state-geocoded, not a hub, and not a non-first copypasta post.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

import pandas as pd

from h5n1.sources.x_location import geocode_series

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "X"
XD = ROOT / "data" / "X_derived"
TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC")  # SPLIT in regression_baseline / gdelt_lift
COPYPASTA_MIN = 5
KEEP = ["_id", "userId", "userName", "alias", "bio", "followers", "verified", "userLocation", "lang",
        "type", "text", "createdAt", "totalRetweets", "favorites", "replies", "impressions", "url"]
STREAMS = {
    "prior": {"out": XD, "corpus": None, "start": None, "hub_pre2025": True},
    "en_test": {"out": XD / "test_2025", "corpus": "twitterapi_en", "start": "2025-01-01", "hub_pre2025": False},
    "es": {"out": XD / "es", "corpus": "twitterapi_es", "start": "2022-06-01", "hub_pre2025": True},
    "fr": {"out": XD / "fr", "corpus": "twitterapi_fr", "start": "2022-06-01", "hub_pre2025": True},
}
OUT_DIR = XD  # set from --stream / --out in __main__

# Spanish-only geography filter (Amendment 1, item 5): a profile naming another
# Spanish-speaking country, or Baja California, is not a US location -- the geocoder reads
# "Bogota, D.C., Colombia" as DC and "Tijuana, Baja California" as CA.
ES_FOREIGN = re.compile(
    r"\b(?:argentina|bolivia|chile|colombia|costa rica|cuba|ecuador|el salvador|espa[ñn]a|spain|"
    r"guatemala|guinea ecuatorial|equatorial guinea|honduras|m[ée]xico|mexico|nicaragua|panam[áa]|"
    r"paraguay|per[úu]|peru|rep[úu]blica dominicana|dominican republic|uruguay|venezuela|"
    r"baja california)\b", re.I)
# French equivalent: France, Belgium, Switzerland, Quebec/French Canada, francophone Africa and
# the Caribbean, and the French regions and cities common in these profiles. Most of these
# already fail to geocode; the filter catches the ones that pick up a stray state token
# ("Neuchâtel, NE" reads as Nebraska) or a French postcode that is also a US ZIP. The tail
# of the list was added after reading every state-geocoded fr location on 2026-10-19.
FR_FOREIGN = re.compile(
    r"\b(?:france|fran[çc]aise?|belgi(?:que|um|e)|bruxelles|brussels|wallonie|li[èe]ge|namur|suisse|"
    r"switzerland|gen[èe]ve|lausanne|neuch[âa]tel|fribourg|valais|vaud|luxembourg|monaco|qu[ée]bec|qc|"
    r"montr[ée]al|gatineau|laval|sherbrooke|trois-rivi[èe]res|saguenay|l[ée]vis|outaouais|acadie|"
    r"nouveau-brunswick|moncton|canada|ha[ïi]ti|port-au-prince|martinique|guadeloupe|r[ée]union|"
    r"guyane|mayotte|nouvelle-cal[ée]donie|polyn[ée]sie|tahiti|s[ée]n[ée]gal|dakar|c[ôo]te d.ivoire|"
    r"abidjan|cameroun|douala|yaound[ée]|mali|bamako|burkina|ouagadougou|niger|niamey|b[ée]nin|"
    r"cotonou|togo|lom[ée]|guin[ée]e|conakry|gabon|libreville|congo|rdc|kinshasa|brazzaville|"
    r"madagascar|antananarivo|maroc|morocco|casablanca|rabat|alg[ée]rie|algeria|alger|tunisie|"
    r"tunisia|tunis|paris|lyon|marseille|toulouse|nice|nantes|bordeaux|lille|rennes|strasbourg|"
    r"montpellier|toulon|amiens|caen|brest|angers|rouen|metz|nancy|grenoble|dijon|reims|"
    r"(?<!new )orl[ée]ans|bretagne|normandie|alsace|lorraine|provence|occitanie|aquitaine|"
    r"auvergne|bourgogne|picardie|champagne|ile-de-france|[îi]le de france|pays de la loire|"
    r"pays basque|corse|savoie|rh[ôo]ne|maine-et-loire|du maine|penn ar bed|finist[èe]re|cannes|"
    r"limoges|prahecq|chatelaillon|collonges|bogot[áa]|colombia|"
    # French phrases the geocoder reads as a state code ("ICI et LA", "ETATS DE CHOC")
    r"ici et la|etats de choc|they,she,chi)\b", re.I)
# French names of US places, put into English before geocoding the fr stream only: the
# geocoder strips accents and knows only English names, so "Louisiane" would never match.
FR_US = [(r"\b[ée]tats[- ]unis(?: d'am[ée]rique)?\b", "USA"), (r"\blouisiane\b", "Louisiana"),
         (r"\bfloride\b", "Florida"), (r"\bcalifornie\b", "California"), (r"\bg[ée]orgie\b", "Georgia"),
         (r"\bcaroline du nord\b", "North Carolina"), (r"\bcaroline du sud\b", "South Carolina"),
         (r"\bvirginie-occidentale\b", "West Virginia"), (r"\bvirginie\b", "Virginia"),
         (r"\bpennsylvanie\b", "Pennsylvania"), (r"\bnouveau-mexique\b", "New Mexico"),
         (r"\bdakota du nord\b", "North Dakota"), (r"\bdakota du sud\b", "South Dakota"),
         (r"\bnouvelle-orl[ée]ans\b", "New Orleans, LA"), (r"\bnouveau-hampshire\b", "New Hampshire"),
         (r"\bnouveau-jersey\b", "New Jersey"), (r"\bhawa[ïi]\b", "Hawaii")]
FOREIGN_BY_STREAM = {"es": ES_FOREIGN, "fr": FR_FOREIGN}

# bios that go to the LLM labeller (recovered verbatim from the 2026-09-28 prior-stream run)
FARM = (r"\b(farm\w*|ranch\w*|cattle\w*|cowboy|cowgirl|dairy\w*|poultry|broiler\w*|egg|eggs|turkey|turkeys|"
        r"grower|homestead\w*|chicken\w*|hens?|flock|coop|ducks?|goats?|sheep|livestock|4-h|ffa|agvocate|ag|"
        r"agri\w*|agronom\w*|crops?|harvest|tractor\w*|hog|swine|pork|beef)\b")
INST = (r"\b(extension|veterinar\w*|vet|dvm|animal health|usda|aphis|department of agriculture|dept of ag|"
        r"farm bureau)\b")
# Spanish equivalents, used only for the es stream
FARM_ES = (r"\b(granj\w*|ganad\w*|rancho\w*|ranchero\w*|av[íi]col\w*|avicult\w*|gallin\w*|pollo\w*|"
           r"aves de corral|huevos?|lecher\w*|campesin\w*|agricult\w*|agropecuari\w*|agr[óo]nom\w*|"
           r"cosecha\w*|cerdos?|porcin\w*|veterinari\w*|senasa|sagarpa|sader)\b")
# French equivalents, used only for the fr stream
FARM_FR = (r"\b(agricult\w*|[ée]leveu\w*|[ée]levage\w*|ferme|fermes|fermier\w*|exploitant\w*|paysan\w*|"
           r"volaill\w*|avicult\w*|avicole\w*|poule\w*|poulet\w*|canards?|foie gras|[œo]e?ufs?|laiti\w*|"
           r"bovin\w*|porcin\w*|porcs?|ovin\w*|agronom\w*|agroalimentaire|v[ée]t[ée]rinaire\w*|anses|mapaq|"
           r"fnsea|chambre d'agriculture|coop[ée]rative agricole)\b")
FARM_BY_STREAM = {"es": FARM_ES, "fr": FARM_FR}


def _term(path: pathlib.Path) -> str:
    m = re.match(r"_(.+?)_ ", path.name)
    return m.group(1) if m else path.stem


def load_raw() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW_DIR.glob("*.csv")):
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        frames.append(df[[c for c in KEEP if c in df.columns]].assign(term=_term(f)))
    raw = pd.concat(frames, ignore_index=True)
    raw["ts"] = pd.to_datetime(raw.createdAt, errors="coerce", utc=True)
    bad = raw.ts.isna() | ~raw["type"].isin(["original", "reply", "quote"])
    print(f"dropping {bad.sum()} malformed rows (unparseable date or type) of {len(raw)}")
    return raw[~bad]


def load_db(corpus: str, start: str) -> pd.DataFrame:
    """x_post_unique rows for one twitterapi corpus, renamed to the CSV export's columns.
    Originals and replies only: the prior cohort has no quote posts (Amendment 1, item 1)."""
    from h5n1.db import get_engine

    sql = f"""
        SELECT post_id AS "_id", user_id AS "userId", user_name AS "userName", alias,
               coalesce(raw->'author'->'profile_bio'->>'description', '') AS bio,
               followers::text AS followers, verified::text AS verified,
               coalesce(user_location, '') AS "userLocation", lang, post_type AS type,
               post_text AS text, created_at AS ts, retweets::text AS "totalRetweets",
               favorites::text AS favorites, replies::text AS replies,
               impressions::text AS impressions, url,
               array_to_string(source_queries, '|') AS term
          FROM x_post_unique
         WHERE corpus = '{corpus}' AND post_type IN ('original', 'reply')
           AND created_at >= '{start}'
    """
    raw = pd.read_sql(sql, get_engine())
    raw["ts"] = pd.to_datetime(raw.ts, utc=True)
    raw["createdAt"] = raw.ts.dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return raw.fillna({c: "" for c in ["alias", "userName", "text", "url"]})


def build(stream: str = "prior") -> None:
    spec = STREAMS[stream]
    raw = load_raw() if spec["corpus"] is None else load_db(spec["corpus"], spec["start"])
    terms = raw.groupby("_id").term.agg(lambda t: "|".join(sorted(set("|".join(t).split("|")))))
    posts = raw.drop_duplicates("_id").drop(columns="term").merge(terms.rename("terms"), on="_id")
    loc = posts.userLocation
    if stream == "fr":
        for pat, en in FR_US:
            loc = loc.str.replace(pat, en, regex=True, flags=re.I)
    posts = posts.join(geocode_series(loc))
    if stream in FOREIGN_BY_STREAM:
        foreign = posts.userLocation.str.contains(FOREIGN_BY_STREAM[stream])
        print(f"{stream} filter: {int((foreign & posts.state.notna()).sum())} state-geocoded posts name "
              f"a country or region outside the US -> ungeocoded")
        posts.loc[foreign, ["state", "county_fips"]] = None

    key = (posts.text.str.lower().str.replace(r"https?://\S+|@\w+|\d+", "", regex=True)
           .str.replace(r"\W+", " ", regex=True).str.strip().str[:80])
    grp = posts.assign(key=key).groupby("key")
    n_posts, n_auth = grp._id.transform("size"), grp.userId.transform("nunique")
    posts["copypasta"] = (n_posts >= COPYPASTA_MIN) & (n_auth >= 2) & (key.str.len() >= 20)
    first = posts.assign(key=key).sort_values("ts").drop_duplicates("key")._id
    posts["copypasta_first"] = posts.copypasta & posts._id.isin(first)

    # n_posts_train is the hub-window count (column name kept so the frozen prior files hash the same)
    tr = posts[posts.ts < TRAIN_END] if spec["hub_pre2025"] else posts
    authors = (posts.sort_values("ts").groupby("userId")
               .agg(userName=("userName", "last"), bio=("bio", "last"), userLocation=("userLocation", "last"),
                    followers=("followers", "last"), verified=("verified", "last"),
                    n_posts=("_id", "size"), state=("state", "last"), geo_level=("geo_level", "last")))
    authors["n_posts_train"] = tr.groupby("userId").size().reindex(authors.index).fillna(0).astype(int)
    authors["volume_pctile"] = authors.n_posts_train.rank(pct=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    posts.to_parquet(OUT_DIR / "posts.parquet", index=False)
    authors.reset_index().to_parquet(OUT_DIR / "authors.parquet", index=False)
    print(f"posts {len(posts):,}  authors {len(authors):,}  "
          f"state-geocoded {posts.state.notna().mean():.3f}  copypasta {posts.copypasta.mean():.3f}")


def _labels(stream: str) -> pd.Series:
    """LLM bio labels: the prior stream's, then this stream's own (labels/bio_labels_*.csv)."""
    dirs = [XD / "labels"] + ([OUT_DIR / "labels"] if OUT_DIR != XD else [])
    files = [f for d in dirs for f in sorted(d.glob("bio_labels_*.csv"))]
    if not files:
        return pd.Series(dtype=str)
    labels = pd.concat(pd.read_csv(f, dtype=str) for f in files)
    labels["account_type"] = labels.account_type.str.strip()
    bad = ~labels.account_type.isin(ACCOUNT_TYPES)
    if bad.any():
        raise ValueError(f"unknown account types: {labels.account_type[bad].unique()}")
    return labels.drop_duplicates("userId").set_index("userId").account_type


def biobatch(stream: str, k: int = 5) -> None:
    """Write bios that need an LLM label -- ag-term bio or top-1% volume, not yet labelled --
    to labels/bio_batch_*.jsonl in the stream's directory, k interleaved batches."""
    if stream == "prior":
        raise SystemExit("prior-stream labels are frozen; biobatch is for en_test / es")
    a = pd.read_parquet(OUT_DIR / "authors.parquet")
    b = a.bio.fillna("").str.lower()
    ag = b.str.contains(FARM) | b.str.contains(INST)
    if stream in FARM_BY_STREAM:
        ag |= b.str.contains(FARM_BY_STREAM[stream])
    need = ((ag & (b.str.strip() != "")) | (a.volume_pctile >= 0.99)) & ~a.userId.isin(_labels(stream).index)
    s = a[need].sample(frac=1, random_state=0)[["userId", "userName", "bio"]]
    s["bio"] = s.bio.fillna("").str.replace(r"\s+", " ", regex=True).str.slice(0, 300)
    s["userName"] = s.userName.fillna("").str.slice(0, 60)
    lab = OUT_DIR / "labels"
    lab.mkdir(parents=True, exist_ok=True)
    for i in range(k):
        with open(lab / f"bio_batch_{i}.jsonl", "w", encoding="utf-8") as f:
            for r in s.iloc[i::k].itertuples(index=False):
                f.write(json.dumps({"userId": r.userId, "name": r.userName, "bio": r.bio}, ensure_ascii=False) + "\n")
    print(f"{len(s):,} bios to label in {k} batches under {lab}")


def waree_sentiment() -> pd.DataFrame:
    """(_id, sentiment) for every scored post: Waree's x_post_sentiment (cardiffnlp XLM-R,
    newline-only cleaning, 512 tokens), sentiment = positive_score - negative_score. The
    project's one sentiment score since 2026-10-19; the pipeline no longer scores posts itself.
    Posts missing from the table (French collected after 2026-10-15) have no score -- callers
    must leave them out, never fill them with 0."""
    from h5n1.db import get_engine

    return pd.read_sql("SELECT post_id AS _id, positive_score - negative_score AS sentiment "
                       "FROM x_post_sentiment", get_engine())


ACCOUNT_TYPES = ["farmer_keeper", "ag_institution", "media_aggregator", "general_public"]
MEDIA_FALLBACK = re.compile(r"\b(?:news|newspaper|journalist|reporter|editor|correspondent|anchor|magazine|"
                            r"radio|television|broadcast\w*|headlines|breaking news|alerts?|tracker|newsletter|"
                            r"press)\b", re.I)


def accounts(stream: str = "prior") -> None:
    authors = pd.read_parquet(OUT_DIR / "authors.parquet")
    labels = _labels(stream)
    authors["type_source"] = authors.userId.isin(labels.index).map({True: "llm_bio", False: "regex"})
    fallback = authors.bio.fillna("").str.contains(MEDIA_FALLBACK).map(
        {True: "media_aggregator", False: "general_public"})
    authors["account_type"] = authors.userId.map(labels).fillna(fallback)
    authors["hub"] = (authors.volume_pctile >= 0.99) & ~authors.account_type.isin(
        ["farmer_keeper", "ag_institution"])
    if stream == "en_test":
        prior_hubs = pd.read_parquet(XD / "authors.parquet", columns=["userId", "hub"]).query("hub").userId
        authors["hub"] |= authors.userId.isin(prior_hubs)
    authors.to_parquet(OUT_DIR / "authors.parquet", index=False)
    print(authors.groupby(["account_type", "type_source"]).size().unstack(fill_value=0))
    print(f"hubs: {authors.hub.sum()} authors, {authors.loc[authors.hub, 'n_posts'].sum():,} posts")


def subset() -> None:
    posts = pd.read_parquet(OUT_DIR / "posts.parquet")
    authors = pd.read_parquet(OUT_DIR / "authors.parquet", columns=["userId", "account_type", "hub"])
    posts = posts.merge(authors, on="userId", how="left")
    keep = posts.state.notna() & ~posts.hub & (~posts.copypasta | posts.copypasta_first)
    sub = posts[keep]
    sub.to_parquet(OUT_DIR / "topic_subset.parquet", index=False)
    print(f"topic subset {len(sub):,} posts of {len(posts):,}")
    print(sub.account_type.value_counts().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "biobatch", "accounts", "subset"])
    ap.add_argument("--stream", choices=list(STREAMS), default="prior")
    ap.add_argument("--out", help="override the stream's output directory (e.g. to rebuild prior files elsewhere)")
    a = ap.parse_args()
    OUT_DIR = pathlib.Path(a.out) if a.out else STREAMS[a.stream]["out"]
    if a.step == "subset":
        subset()
    else:
        {"build": build, "biobatch": biobatch, "accounts": accounts}[a.step](a.stream)
