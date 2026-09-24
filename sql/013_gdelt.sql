-- 013_gdelt.sql
-- GDELT GKG avian-influenza news mentions. Loaded by h5n1/sources/gdelt.py; see its
-- docstring for the filter, the cost model, and why this is news, not social media.
--
-- Three tables at their natural grain; aggregation to (fips, day) happens in
-- 014_feature_gdelt.sql, not here.
--
-- The one rule that must survive every future edit: a county FIPS is only ever
-- assigned to a TYPE-3 (US city) location. Type 2 is a US state geocoded to the
-- state's CENTROID -- point-in-polygoning it would credit every statewide story to
-- whichever county sits in the middle of the state. The CHECK below makes that a
-- database error rather than a silent data bug.

CREATE TABLE IF NOT EXISTS fact_gdelt_article (
    gkg_record_id      TEXT PRIMARY KEY,           -- e.g. 20240312150000-1234
    day                DATE NOT NULL REFERENCES dim_date,   -- from GKG DATE (processing batch), UTC
    crawl_ts           TIMESTAMP NOT NULL,
    source_domain      TEXT,
    url                TEXT,
    -- V2Tone, split. tone = pos - neg, in roughly [-100, 100]; NULL when absent.
    tone               DOUBLE PRECISION,
    tone_pos           DOUBLE PRECISION,
    tone_neg           DOUBLE PRECISION,
    tone_polarity      DOUBLE PRECISION,
    activity_density   DOUBLE PRECISION,
    self_group_density DOUBLE PRECISION,
    word_count         INTEGER,
    themes             TEXT[],                     -- which of the 4 avian themes matched
    -- Same day + identical V2Tone string => same wire copy (AP/Reuters reprints).
    syndication_key    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fact_gdelt_article_day  ON fact_gdelt_article (day);
CREATE INDEX IF NOT EXISTS ix_fact_gdelt_article_synd ON fact_gdelt_article (day, syndication_key);

-- One row per US location block in the article's V2Locations. Only country = US and
-- types 1 (country), 2 (state), 3 (city) are kept.
CREATE TABLE IF NOT EXISTS fact_gdelt_location (
    gkg_record_id TEXT     NOT NULL REFERENCES fact_gdelt_article ON DELETE CASCADE,
    loc_seq       SMALLINT NOT NULL,               -- position in V2Locations
    loc_type      SMALLINT NOT NULL CHECK (loc_type IN (1, 2, 3)),
    place_name    TEXT,
    adm1          TEXT,                            -- 'US' + state code, e.g. USIA
    adm2          TEXT,                            -- USPS + county code, e.g. IA153; PIP fallback
    feature_id    TEXT,
    state         TEXT,                            -- USPS; types 2 and 3 only
    fips          CHAR(5) REFERENCES dim_county,   -- point-in-polygon; type 3 only
    lat           DOUBLE PRECISION,
    lon           DOUBLE PRECISION,
    PRIMARY KEY (gkg_record_id, loc_seq),
    CONSTRAINT ck_gdelt_fips_city_only CHECK (fips IS NULL OR loc_type = 3),
    CONSTRAINT ck_gdelt_fips_vintage   CHECK (fips IS NULL OR substr(fips, 1, 3) <> '091')
);
CREATE INDEX IF NOT EXISTS ix_fact_gdelt_location_fips  ON fact_gdelt_location (fips) WHERE fips IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fact_gdelt_location_state ON fact_gdelt_location (state) WHERE state IS NOT NULL;

-- Normalization denominator: ALL US-located GKG articles (any theme) per (day, state),
-- plus a national row with state = 'US'. GDELT's monitored volume drifts over the
-- years, so raw mention counts are not comparable across time without it.
CREATE TABLE IF NOT EXISTS fact_gdelt_denominator (
    day        DATE    NOT NULL REFERENCES dim_date,
    state      TEXT    NOT NULL,                   -- USPS, or 'US' for national
    n_articles INTEGER NOT NULL,
    PRIMARY KEY (day, state)
);
