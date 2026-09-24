"""GDELT GKG avian-influenza news mentions -> fact_gdelt_article / fact_gdelt_location /
fact_gdelt_denominator.

WHAT THIS IS, AND WHAT IT IS NOT. GDELT is NEWS, not social media. It enters the model
as a comparator ("does news coverage add lift beyond spatial lag?") or a declared
substitute for the social arm -- never as a silent stand-in for it. And most local
coverage FOLLOWS an APHIS confirmation, so raw correlation with outbreaks proves
nothing; the test is incremental lift over feature_spatial_lag + own history.

SOURCE. `gdelt-bq.gdeltv2.gkg_partitioned` (21.9 TB public table). Filter: any of the
four avian themes (there is NO H5N1 theme) AND at least one US-geocoded location.

    TAX_DISEASE_BIRD_FLU, TAX_DISEASE_AVIAN_FLU, TAX_DISEASE_AVIAN_INFLUENZA,
    TAX_DISEASE_POULTRY_DISEASES

COST MODEL -- read before running anything. BigQuery bills every byte of each
referenced column across every partition in range, regardless of how many rows match.
Filtering on V2Themes therefore costs the whole V2Themes column. Every real query here
dry-runs first, refuses to run above CEILING_BYTES, sets maximum_bytes_billed to
1.05x the dry run, and needs --yes. The table is partitioned on INGESTION time
(_PARTITIONTIME); the day key is the DATE column (the 15-minute processing batch), and
the partition bound only exists to prune.

    uv run python -m h5n1.sources.gdelt dry-run                 # free: bytes + est. cost
    uv run python -m h5n1.sources.gdelt extract --yes           # billed; -> BQ staging -> GCS
    uv run python -m h5n1.sources.gdelt denominator --yes       # billed; -> BQ staging -> GCS
    uv run python -m h5n1.sources.gdelt load                    # GCS parquet -> Postgres

GEOGRAPHY -- the hazardous part. V2Locations type codes: 1 country, 2 US state,
3 US city, 4 world city, 5 world state. A type-2 location is geocoded to the STATE
CENTROID; point-in-polygoning it would credit every Kansas mention to whichever county
sits at Kansas's middle. So only type 3 is ever assigned a county. That is enforced
three times: here (check_location_guards), in the fact table's CHECK constraint, and in
the unit tests. County assignment is point-in-polygon against the Census 2021
cartographic-boundary counties, which is the same 2021 vintage as dim_county (legacy
Connecticut 09001-09015, not the 091xx planning regions -- guarded as everywhere else),
with GDELT's own ADM2 county code as the fallback for points PIP misses (water features
outside the shoreline-clipped polygons). See adm2_to_fips for the measured agreement.

SYNDICATION. GKG stores no article text, only a URL, and one AP story reprinted by 200
outlets would otherwise count 200 times. Proxy: identical V2Tone strings on the same
day. V2Tone carries seven floats including the word count, so two different texts
colliding on all seven is improbable; the same wire copy colliding is near-certain.
Both raw article and distinct-story counts are kept.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import pathlib
import tempfile

import pandas as pd
from google.cloud import bigquery, storage

PROJECT = "harvard-capstone-499102"
STAGE_DATASET = "gdelt_stage"          # must be US multi-region, like the public table
ARTICLE_TABLE = "gkg_avian_us"
DENOM_TABLE = "gkg_us_denominator"
GKG = "gdelt-bq.gdeltv2.gkg_partitioned"

RAW_BUCKET = "h5n1-raw"
GCS_PREFIX = "gdelt"
MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "manifests" / "gdelt_gkg.json"

TIGER_URL = "https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_2021_us_county_500k.zip"
TIGER_OBJECT = "tiger/cb_2021_us_county_500k.zip"
TIGER_MANIFEST = MANIFEST.parent / "tiger_county_2021.json"

DEFAULT_START = dt.date(2022, 1, 1)
DEFAULT_END = dt.date(2026, 7, 21)     # model_county_day horizon
PARTITION_LAG_DAYS = 3                 # ingestion can trail the DATE batch
CEILING_BYTES = 1_100_000_000_000      # per query; override with --ceiling-tb
USD_PER_TIB = 6.25

THEMES = (
    "TAX_DISEASE_BIRD_FLU",
    "TAX_DISEASE_AVIAN_FLU",
    "TAX_DISEASE_AVIAN_INFLUENZA",
    "TAX_DISEASE_POULTRY_DISEASES",
)
# V2Themes entries look like "THEME,charoffset;". The trailing comma stops a match on a
# longer theme name that merely starts with one of ours.
THEME_RE = r"(" + "|".join(THEMES) + r"),"

TONE_FIELDS = (
    "tone", "tone_pos", "tone_neg", "tone_polarity",
    "activity_density", "self_group_density", "word_count",
)
US_LOC_TYPES = {1, 2, 3}


# ---------------------------------------------------------------------------
# Pure parsing -- unit tested, no I/O
# ---------------------------------------------------------------------------

def crawl_day(date_int: int | str) -> dt.date:
    """GKG DATE is YYYYMMDDHHMMSS as an integer."""
    s = str(date_int)
    return dt.date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def parse_tone(v2_tone: str | None) -> dict:
    """V2Tone -> the seven named floats; all None when missing or malformed."""
    out = dict.fromkeys(TONE_FIELDS)
    if not v2_tone:
        return out
    parts = v2_tone.split(",")
    if len(parts) < len(TONE_FIELDS):
        return out
    try:
        vals = [float(p) for p in parts[: len(TONE_FIELDS)]]
    except ValueError:
        return out
    out.update(zip(TONE_FIELDS, vals))
    out["word_count"] = int(vals[6])
    return out


def parse_locations(v2_locations: str | None) -> list[dict]:
    """V2Locations -> one dict per location block.

    Block layout: type#fullname#countrycode#adm1#adm2#lat#lon#featureid#charoffset.
    Blocks that don't parse are skipped, not guessed at.
    """
    out: list[dict] = []
    if not v2_locations:
        return out
    for seq, block in enumerate(v2_locations.split(";")):
        f = block.split("#")
        if len(f) < 8:
            continue
        try:
            loc_type = int(f[0])
            lat = float(f[5]) if f[5] else None
            lon = float(f[6]) if f[6] else None
        except ValueError:
            continue
        out.append({
            "loc_seq": seq,
            "loc_type": loc_type,
            "place_name": f[1] or None,
            "country": f[2] or None,
            "adm1": f[3] or None,
            "adm2": f[4] or None,
            "lat": lat,
            "lon": lon,
            "feature_id": f[7] or None,
        })
    return out


def usps_from_adm1(adm1: str | None, valid_states: set[str]) -> str | None:
    """GDELT US ADM1 is 'US' + the FIPS 10-4 state code, which equals the USPS code."""
    if not isinstance(adm1, str) or len(adm1) != 4 or not adm1.startswith("US"):
        return None
    st = adm1[2:]
    return st if st in valid_states else None


# GDELT's ADM2 codes predate some county changes. 1:1 renames map forward; a SPLIT
# (Valdez-Cordova 02261 -> Chugach 02063 + Copper River 02066, 2019) has no single
# successor, so the fallback leaves the county NULL rather than guess. The state-level
# credit for those mentions is unaffected.
ADM2_RENAMES = {
    "02270": "02158",   # Wade Hampton -> Kusilvak, AK (2015)
    "46113": "46102",   # Shannon -> Oglala Lakota, SD (2015)
}


def adm2_to_fips(adm2: str | None, state_fp: dict[str, str]) -> str | None:
    """GDELT US ADM2 is USPS + the 3-digit county code (TN157 -> 47157). Measured on the
    2024-03 smoke load: 100% well-formed, and 99.75% agreement with point-in-polygon
    where both resolve (1,603 points; the one miss was a national forest spanning
    parishes)."""
    # Empty fields arrive from pandas as NaN (a float), not None.
    if not isinstance(adm2, str) or len(adm2) != 5 or not adm2[2:].isdigit():
        return None
    sp = state_fp.get(adm2[:2])
    return sp + adm2[2:] if sp else None


def syndication_key(day: dt.date, v2_tone: str | None, gkg_record_id: str) -> str:
    """Same day + identical V2Tone => same wire copy. With no tone there is nothing to
    match on, so the record is its own story rather than collapsing into one bucket."""
    basis = f"{day.isoformat()}|{v2_tone}" if v2_tone else f"rec|{gkg_record_id}"
    return hashlib.sha1(basis.encode()).hexdigest()[:20]


def check_location_guards(loc: pd.DataFrame, valid_fips: set[str]) -> None:
    """Abort, don't warn: each of these silently corrupts county features downstream."""
    bad_type = loc["fips"].notna() & (loc["loc_type"] != 3)
    if bad_type.any():
        raise RuntimeError(
            f"{int(bad_type.sum())} non-city (e.g. state-centroid) locations were assigned a "
            "county FIPS. Only type-3 US city points may be point-in-polygoned."
        )
    fips = loc["fips"].dropna()
    ct = fips.str.startswith("091")
    if ct.any():
        raise RuntimeError(
            f"{int(ct.sum())} locations resolved to 091xx Connecticut planning regions; "
            "dim_county is 2021 vintage (09001-09015). Wrong boundary vintage."
        )
    orphan = ~fips.isin(valid_fips)
    if orphan.any():
        raise RuntimeError(
            f"{int(orphan.sum())} assigned FIPS are not in dim_county, e.g. "
            f"{sorted(fips[orphan].unique())[:5]}"
        )


def assign_counties(loc: pd.DataFrame, counties) -> pd.Series:
    """Point-in-polygon for type-3 rows only; returns a fips Series aligned to `loc`.

    `counties` is a GeoDataFrame with a `fips` column in EPSG:4269/4326 (lon/lat).
    """
    import geopandas as gpd

    fips = pd.Series(pd.NA, index=loc.index, dtype="object")
    city = loc[(loc["loc_type"] == 3) & loc["lat"].notna() & loc["lon"].notna()]
    if city.empty:
        return fips
    # Many mentions share a point (every "Des Moines" is the same GNIS feature), so
    # join the distinct points and map back.
    pts = city[["lat", "lon"]].drop_duplicates().reset_index(drop=True)
    gpts = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts["lon"], pts["lat"]),
                            crs=counties.crs)
    hit = gpd.sjoin(gpts, counties[["fips", "geometry"]], how="left", predicate="within")
    # A point exactly on a shared border can match two counties; keep one, deterministically.
    hit = hit.sort_values("fips").drop_duplicates(["lat", "lon"])
    lookup = hit.set_index(["lat", "lon"])["fips"]
    keys = pd.MultiIndex.from_frame(city[["lat", "lon"]])
    fips.loc[city.index] = lookup.reindex(keys).to_numpy()
    return fips.where(fips.notna(), None)


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

def _day_expr() -> str:
    return "SAFE.PARSE_DATE('%Y%m%d', SUBSTR(CAST(DATE AS STRING), 1, 8))"


def _partition_where(start: dt.date, end: dt.date) -> str:
    hi = end + dt.timedelta(days=1 + PARTITION_LAG_DAYS)
    return (f"_PARTITIONTIME >= TIMESTAMP('{start.isoformat()}') "
            f"AND _PARTITIONTIME < TIMESTAMP('{hi.isoformat()}')")


def extract_sql(start: dt.date, end: dt.date) -> str:
    return f"""
SELECT
  GKGRECORDID       AS gkg_record_id,
  DATE              AS crawl_date,
  SourceCommonName  AS source_domain,
  DocumentIdentifier AS url,
  V2Locations       AS v2_locations,
  V2Tone            AS v2_tone,
  ARRAY(SELECT DISTINCT t FROM UNNEST(REGEXP_EXTRACT_ALL(V2Themes, r'{THEME_RE}')) t
        ORDER BY t) AS themes
FROM `{GKG}`
WHERE {_partition_where(start, end)}
  AND {_day_expr()} BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
  AND REGEXP_CONTAINS(V2Themes, r'{THEME_RE}')
  AND V2Locations LIKE '%#US#%'
"""


def denominator_sql(start: dt.date, end: dt.date) -> str:
    """Articles per (day, state) over ALL US-located GKG records, plus a national row
    keyed state='US'. One scan: each article's state list is built inline, so the table
    is not referenced twice (which would bill twice)."""
    return f"""
SELECT day, key AS state, COUNT(*) AS n_articles
FROM (
  SELECT
    {_day_expr()} AS day,
    ARRAY_CONCAT(['US'], ARRAY(
      SELECT DISTINCT SUBSTR(SPLIT(loc, '#')[SAFE_OFFSET(3)], 3, 2)
      FROM UNNEST(SPLIT(V2Locations, ';')) loc
      WHERE SPLIT(loc, '#')[SAFE_OFFSET(0)] IN ('2', '3')
        AND SPLIT(loc, '#')[SAFE_OFFSET(2)] = 'US'
        AND LENGTH(SPLIT(loc, '#')[SAFE_OFFSET(3)]) = 4
    )) AS keys
  FROM `{GKG}`
  WHERE {_partition_where(start, end)}
    AND V2Locations LIKE '%#US#%'
), UNNEST(keys) AS key
WHERE day BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
GROUP BY day, state
"""


def _bq() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def dry_run_bytes(client: bigquery.Client, sql: str) -> int:
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return client.query(sql, job_config=cfg).total_bytes_processed


def _fmt(nbytes: int) -> str:
    tib = nbytes / 2**40
    return f"{nbytes / 1e9:,.1f} GB ({tib:.3f} TiB, ~${tib * USD_PER_TIB:,.2f} at on-demand)"


def run_to_stage(client: bigquery.Client, sql: str, table: str, ceiling: int) -> dict:
    """Dry-run, enforce the ceiling, then run into gdelt_stage.<table> with a hard
    maximum_bytes_billed, and export to GCS as parquet."""
    est = dry_run_bytes(client, sql)
    print(f"  dry run: {_fmt(est)}")
    if est > ceiling:
        raise SystemExit(f"refusing: {est:,} bytes exceeds ceiling {ceiling:,}")

    ds = bigquery.Dataset(f"{PROJECT}.{STAGE_DATASET}")
    ds.location = "US"
    client.create_dataset(ds, exists_ok=True)

    dest = f"{PROJECT}.{STAGE_DATASET}.{table}"
    cfg = bigquery.QueryJobConfig(
        destination=dest,
        write_disposition="WRITE_TRUNCATE",
        maximum_bytes_billed=int(est * 1.05),
        use_query_cache=False,
    )
    job = client.query(sql, job_config=cfg)
    job.result()
    n_rows = client.get_table(dest).num_rows
    print(f"  billed {_fmt(job.total_bytes_billed or 0)}; {n_rows:,} rows -> {dest}")

    # Per-run prefix: a second run on the same day must not mix its parts with the first.
    run_tag = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{GCS_PREFIX}/{table}/{run_tag}"
    uri = f"gs://{RAW_BUCKET}/{prefix}/part-*.parquet"
    ext = bigquery.ExtractJobConfig(destination_format="PARQUET")
    client.extract_table(dest, uri, job_config=ext, location="US").result()
    print(f"  exported -> {uri}")

    return {
        "bq_table": dest,
        "gcs_prefix": f"gs://{RAW_BUCKET}/{prefix}/",
        "row_count": n_rows,
        "bytes_processed": job.total_bytes_processed,
        "bytes_billed": job.total_bytes_billed,
        "job_id": job.job_id,
        "query_sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "capture_date": dt.date.today().isoformat(),
    }


def _write_manifest(key: str, entry: dict, start: dt.date, end: dt.date) -> None:
    doc = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {
        "source": f"GDELT GKG 2.0 via BigQuery public table {GKG}",
        "source_url": "https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/",
        "notes": (
            "Filter: V2Themes contains any of " + ", ".join(THEMES) + " (there is no H5N1 "
            "theme) AND V2Locations has a US location. Day key = DATE (processing batch), "
            "not _PARTITIONTIME (ingestion). Staging tables live in BQ dataset "
            f"{PROJECT}.{STAGE_DATASET} (US multi-region); query text is reproducible from "
            "h5n1/sources/gdelt.py and pinned by query_sha256."
        ),
        "tables": {},
    }
    entry = {**entry, "start": start.isoformat(), "end": end.isoformat()}
    doc["tables"][key] = entry
    MANIFEST.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"  manifest -> {MANIFEST.name}")


# ---------------------------------------------------------------------------
# GCS -> Postgres
# ---------------------------------------------------------------------------

def _read_parquet_prefix(gcs_prefix: str) -> pd.DataFrame:
    bucket_name, prefix = gcs_prefix.removeprefix("gs://").split("/", 1)
    bucket = storage.Client().bucket(bucket_name)
    frames = [
        pd.read_parquet(io.BytesIO(b.download_as_bytes()))
        for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".parquet")
    ]
    if not frames:
        raise SystemExit(f"no parquet under {gcs_prefix}")
    return pd.concat(frames, ignore_index=True)


def _counties_gdf(valid_fips: set[str]):
    """Census 2021 cartographic-boundary counties, archived to GCS on first use."""
    import geopandas as gpd
    import requests

    blob = storage.Client().bucket(RAW_BUCKET).blob(TIGER_OBJECT)
    if blob.exists():
        raw = blob.download_as_bytes()
    else:
        resp = requests.get(TIGER_URL, timeout=120)
        resp.raise_for_status()
        raw = resp.content
        blob.upload_from_string(raw, content_type="application/zip")
        TIGER_MANIFEST.write_text(json.dumps({
            "source": "Census 2021 cartographic boundary file, counties, 1:500k",
            "source_url": TIGER_URL,
            "capture_date": dt.date.today().isoformat(),
            "gcs_path": f"gs://{RAW_BUCKET}/{TIGER_OBJECT}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "notes": "Used for GDELT US-city point-in-polygon. 2021 vintage to match "
                     "dim_county: legacy Connecticut counties 09001-09015, not 091xx.",
        }, indent=2) + "\n")
        print(f"  archived TIGER -> gs://{RAW_BUCKET}/{TIGER_OBJECT}")

    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "counties.zip"
        path.write_bytes(raw)
        gdf = gpd.read_file(f"zip://{path}")
    gdf["fips"] = gdf["GEOID"].str.zfill(5)
    if gdf["fips"].str.startswith("091").any():
        raise RuntimeError("TIGER file carries 091xx planning regions -- wrong vintage")
    # Territories (PR, GU, ...) are not in dim_county; drop rather than orphan.
    gdf = gdf[gdf["fips"].isin(valid_fips)][["fips", "geometry"]].reset_index(drop=True)
    return gdf


def _pg_array(xs) -> str | None:
    if xs is None or len(xs) == 0:
        return None
    return "{" + ",".join(xs) + "}"


def build_frames(raw: pd.DataFrame, valid_states: set[str], counties,
                 state_fp: dict[str, str],
                 valid_fips: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Staging rows -> (article, location) frames at the fact-table grain.

    County = point-in-polygon first, GDELT's ADM2 as the fallback for type-3 points PIP
    misses. The misses are water features (Chesapeake Bay, Puget Sound, Farallon
    Islands) that fall outside the shoreline-clipped cartographic boundaries, and are
    exactly where wild-bird die-offs get reported, so dropping them would bias the
    county layer against the most relevant mentions."""
    raw = raw.drop_duplicates("gkg_record_id")
    tone = pd.DataFrame([parse_tone(t) for t in raw["v2_tone"]], index=raw.index)
    days = raw["crawl_date"].map(crawl_day)
    art = pd.DataFrame({
        "gkg_record_id": raw["gkg_record_id"],
        "day": days,
        "crawl_ts": pd.to_datetime(raw["crawl_date"].astype(str), format="%Y%m%d%H%M%S"),
        "source_domain": raw["source_domain"],
        "url": raw["url"],
    }).join(tone)
    art["themes"] = raw["themes"].map(_pg_array)
    art["syndication_key"] = [
        syndication_key(d, t, r) for d, t, r in zip(days, raw["v2_tone"], raw["gkg_record_id"])
    ]

    rows = []
    for rec, locs in zip(raw["gkg_record_id"], raw["v2_locations"]):
        for loc in parse_locations(locs):
            if loc["country"] == "US" and loc["loc_type"] in US_LOC_TYPES:
                loc["gkg_record_id"] = rec
                rows.append(loc)
    loc = pd.DataFrame(rows)
    loc["state"] = [
        usps_from_adm1(a, valid_states) if t in (2, 3) else None
        for a, t in zip(loc["adm1"], loc["loc_type"])
    ]
    loc["fips"] = assign_counties(loc, counties)
    fallback = (loc["loc_type"] == 3) & loc["fips"].isna()
    fb = pd.Series([adm2_to_fips(a, state_fp) for a in loc.loc[fallback, "adm2"]],
                   index=loc.index[fallback], dtype="object")
    fb = fb.map(lambda f: ADM2_RENAMES.get(f, f) if f else f)
    # Only the FALLBACK is filtered to dim_county: a stale ADM2 code is expected here.
    # A PIP result outside dim_county would be a real bug and still aborts in
    # check_location_guards.
    stale = fb.notna() & ~fb.isin(valid_fips)
    if stale.any():
        print(f"  ADM2 fallback codes with no 1:1 successor in dim_county, left NULL: "
              f"{fb[stale].value_counts().to_dict()}")
    loc.loc[fb.index, "fips"] = fb.where(~stale)
    loc["fips"] = loc["fips"].where(loc["fips"].notna(), None)
    n_fb = int(loc.loc[fb.index, "fips"].notna().sum())
    print(f"  type-3 PIP misses: {int(fallback.sum()):,}; resolved by ADM2 fallback: {n_fb:,}")
    return art, loc


def _report(art: pd.DataFrame, loc: pd.DataFrame, counties_state: dict[str, str]) -> None:
    print(f"  articles {len(art):,}; distinct stories {art['syndication_key'].nunique():,}")
    by_type = loc.groupby("loc_type")["gkg_record_id"].agg(["size", "nunique"])
    print("  US locations by type (rows / distinct articles):")
    for t, r in by_type.iterrows():
        print(f"    type {t}: {r['size']:,} / {r['nunique']:,}")
    city = loc[loc["loc_type"] == 3]
    if len(city):
        miss = city["fips"].isna().mean()
        print(f"  type-3 points with no county hit: {miss:.2%}")
        both = city.dropna(subset=["fips", "state"])
        mism = (both["fips"].map(counties_state) != both["state"]).mean() if len(both) else 0
        print(f"  type-3 PIP state != ADM1 state: {mism:.2%}")
        # ADM2 cross-check, unverified format: show what it looks like next to the PIP fips.
        sample = city.dropna(subset=["fips"]).head(8)[["place_name", "adm2", "fips"]]
        print("  ADM2 vs PIP sample:\n" + sample.to_string(index=False))
    no_state = loc[loc["loc_type"].isin([2, 3]) & loc["state"].isna()]
    if len(no_state):
        print(f"  state/city rows with unmapped ADM1: {len(no_state):,} "
              f"e.g. {no_state['adm1'].value_counts().head(5).to_dict()}")


ARTICLE_COLS = ["gkg_record_id", "day", "crawl_ts", "source_domain", "url", *TONE_FIELDS,
                "themes", "syndication_key"]
LOCATION_COLS = ["gkg_record_id", "loc_seq", "loc_type", "place_name", "adm1", "adm2",
                 "feature_id", "state", "fips", "lat", "lon"]
DENOM_COLS = ["day", "state", "n_articles"]


def _copy_upsert(cur, df: pd.DataFrame, table: str, cols: list[str], key: list[str]) -> None:
    """COPY into a temp table, then one INSERT .. ON CONFLICT: idempotent and fast
    enough for millions of rows over the proxy (executemany is not)."""
    tmp = f"tmp_{table}"
    cur.execute(f"CREATE TEMP TABLE {tmp} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
    collist = ", ".join(cols)
    with cur.copy(f"COPY {tmp} ({collist}) FROM STDIN (FORMAT csv)") as cp:
        for start in range(0, len(df), 200_000):
            buf = io.StringIO()
            df.iloc[start:start + 200_000][cols].to_csv(buf, index=False, header=False)
            cp.write(buf.getvalue())
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key)
    cur.execute(
        f"INSERT INTO {table} ({collist}) SELECT {collist} FROM {tmp} "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
    )
    print(f"  {table}: {cur.rowcount:,} rows upserted")


def load() -> None:
    from h5n1.db import get_engine

    if not MANIFEST.exists():
        raise SystemExit("no manifest -- run extract and denominator first")
    tables = json.loads(MANIFEST.read_text())["tables"]
    engine = get_engine()

    dims = pd.read_sql("SELECT fips, state FROM dim_county", engine)
    valid_fips = set(dims["fips"])
    valid_states = set(dims["state"])
    counties_state = dict(zip(dims["fips"], dims["state"]))
    state_fp = dict(zip(dims["state"], dims["fips"].str[:2]))

    print("reading staging parquet ...")
    raw = _read_parquet_prefix(tables[ARTICLE_TABLE]["gcs_prefix"])
    den = _read_parquet_prefix(tables[DENOM_TABLE]["gcs_prefix"])
    for name, df, expect in ((ARTICLE_TABLE, raw, tables[ARTICLE_TABLE]["row_count"]),
                             (DENOM_TABLE, den, tables[DENOM_TABLE]["row_count"])):
        if len(df) != expect:
            raise RuntimeError(f"{name}: parquet has {len(df):,} rows, manifest says {expect:,}")

    print("parsing + point-in-polygon ...")
    art, loc = build_frames(raw, valid_states, _counties_gdf(valid_fips), state_fp,
                            valid_fips)
    check_location_guards(loc, valid_fips)
    _report(art, loc, counties_state)

    # Keep 'US' (national) plus real states; the denominator SQL can emit junk ADM1s.
    den = den[den["state"].isin(valid_states | {"US"})]

    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        _copy_upsert(cur, art, "fact_gdelt_article", ARTICLE_COLS, ["gkg_record_id"])
        _copy_upsert(cur, loc, "fact_gdelt_location", LOCATION_COLS,
                     ["gkg_record_id", "loc_seq"])
        _copy_upsert(cur, den, "fact_gdelt_denominator", DENOM_COLS, ["day", "state"])
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()
    print("done. Next: uv run python sql/rebuild_model_county_day.py --variant gdelt")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dry-run", "extract", "denominator", "load"])
    ap.add_argument("--start", type=dt.date.fromisoformat, default=DEFAULT_START)
    ap.add_argument("--end", type=dt.date.fromisoformat, default=DEFAULT_END)
    ap.add_argument("--ceiling-tb", type=float, default=CEILING_BYTES / 1e12)
    ap.add_argument("--yes", action="store_true",
                    help="actually run the billed query (otherwise: dry run only)")
    args = ap.parse_args()
    ceiling = int(args.ceiling_tb * 1e12)

    if args.cmd == "load":
        load()
        return

    client = _bq()
    queries = {
        "extract": (ARTICLE_TABLE, extract_sql(args.start, args.end)),
        "denominator": (DENOM_TABLE, denominator_sql(args.start, args.end)),
    }
    if args.cmd == "dry-run" or not args.yes:
        for name, (_, sql) in queries.items():
            if args.cmd in ("dry-run", name):
                print(f"{name} {args.start}..{args.end}: {_fmt(dry_run_bytes(client, sql))}")
        if args.cmd != "dry-run":
            print("dry run only; pass --yes to run the billed query")
        return

    table, sql = queries[args.cmd]
    print(f"{args.cmd} {args.start}..{args.end} -> {STAGE_DATASET}.{table}")
    _write_manifest(table, run_to_stage(client, sql, table, ceiling), args.start, args.end)


if __name__ == "__main__":
    main()
