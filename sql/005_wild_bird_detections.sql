-- 005_wild_bird_detections.sql
-- Wild-bird HPAI detections (USDA/APHIS surveillance), a predictor of poultry
-- spillover — distinct from fact_h5n1_outbreak (the poultry DV). Source rows are
-- one-per-sample; aggregated here to the canonical county x day grain so it joins
-- feature_county_day like every other fact.
CREATE TABLE IF NOT EXISTS fact_wild_bird_detection (
    fips            CHAR(5) NOT NULL REFERENCES dim_county,
    day             DATE    NOT NULL REFERENCES dim_date,
    detection_count INTEGER NOT NULL,   -- samples confirmed positive that county-day
    n_species       INTEGER,            -- distinct Bird Species that county-day
    PRIMARY KEY (fips, day)
);
