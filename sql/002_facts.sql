-- 002_facts.sql
-- One conformed fact table per source, all at county x day grain.
-- Column sets are a strawman — adjust to the actual source files. The *shape*
-- (grain + FKs to the two dims) is the part to preserve.

-- Dependent variable: USDA/APHIS poultry detections
CREATE TABLE IF NOT EXISTS fact_h5n1_outbreak (
    fips           CHAR(5) NOT NULL REFERENCES dim_county,
    day            DATE    NOT NULL REFERENCES dim_date,
    flock_type     TEXT    NOT NULL DEFAULT 'unknown',  -- commercial / backyard / turkey / layer ...
    confirmed_flag BOOLEAN NOT NULL DEFAULT FALSE,
    birds_affected INTEGER,
    PRIMARY KEY (fips, day, flock_type)
);

-- NOAA weather
CREATE TABLE IF NOT EXISTS fact_weather (
    fips         CHAR(5) NOT NULL REFERENCES dim_county,
    day          DATE    NOT NULL REFERENCES dim_date,
    temp_min_c   DOUBLE PRECISION,
    temp_max_c   DOUBLE PRECISION,
    precip_mm    DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    PRIMARY KEY (fips, day)
);

-- USGS bird density (after lat/lon -> FIPS crosswalk + regression smoothing)
CREATE TABLE IF NOT EXISTS fact_bird_density (
    fips                CHAR(5) NOT NULL REFERENCES dim_county,
    day                 DATE    NOT NULL REFERENCES dim_date,
    band_count_smoothed DOUBLE PRECISION,
    PRIMARY KEY (fips, day)
);

-- Aggregated Modal sentiment output, rolled up to the canonical grain
CREATE TABLE IF NOT EXISTS fact_social_sentiment (
    fips                CHAR(5) NOT NULL REFERENCES dim_county,
    day                 DATE    NOT NULL REFERENCES dim_date,
    post_count          INTEGER,
    sentiment_mean      DOUBLE PRECISION,   -- [-1, 1]
    sentiment_neg_share DOUBLE PRECISION,
    lang_breakdown      JSONB,              -- {"en":0.7,"es":0.2,"fr":0.1}
    PRIMARY KEY (fips, day)
);

-- Economic impact: table-egg-laying hens. Grain may be national or state rather
-- than county depending on the USDA series; NULL fips = national aggregate.
CREATE TABLE IF NOT EXISTS fact_egg_economics (
    fips             CHAR(5) REFERENCES dim_county,
    day              DATE NOT NULL REFERENCES dim_date,
    laying_hen_pop   BIGINT,
    egg_production   BIGINT,
    UNIQUE (fips, day)
);
