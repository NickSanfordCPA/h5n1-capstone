-- model_county_day_gdelt.sql
-- The frozen baseline surface (model_county_day, 23 RF predictors) PLUS the GDELT news
-- features from feature_gdelt_county_day. One row per (state, fips, day), same rows as
-- the baseline, so any lift difference is attributable to the added columns alone.
--
-- WHY THIS FILE IS NOT A NUMBERED MIGRATION. run_migrations.py globs sql/*.sql and
-- applies every file once. At migration time the GDELT facts are empty and
-- feature_gdelt_county_day is created WITH NO DATA, so a CREATE TABLE AS here would
-- fail ("materialized view has not been populated"). This subdirectory is outside that
-- glob; the file is executed only by:
--     uv run python sql/rebuild_model_county_day.py --variant gdelt
-- which refreshes feature_gdelt_county_day first.
--
-- The baseline table is read, never modified. Rebuild it first if it is stale.
--
-- Transforms mirror 012: log1p on counts and rates (all non-negative by construction,
-- so no GREATEST clip -- and GREATEST would zero-fill the NULLs outside GDELT
-- coverage, see the LEAST/GREATEST trap noted in 012). Tone and negative share stay raw.

SET LOCAL work_mem = '64MB';
-- A parallel hash join of two 7.5M-row relations asks for a 128MB dynamic shared-memory
-- segment, which db-g1-small's /dev/shm can't grant. Postgres reports that as
-- "DiskFull: could not resize shared memory segment" even though storage is fine.
-- Serial is slower but fits.
SET LOCAL max_parallel_workers_per_gather = 0;

DROP TABLE IF EXISTS model_county_day_gdelt;

CREATE TABLE model_county_day_gdelt AS
SELECT
    m.*,
    LN(1 + g.gdelt_state_articles_7d::double precision)   AS log_gdelt_state_articles_7d,
    LN(1 + g.gdelt_state_articles_21d::double precision)  AS log_gdelt_state_articles_21d,
    LN(1 + g.gdelt_state_stories_7d::double precision)    AS log_gdelt_state_stories_7d,
    LN(1 + g.gdelt_state_stories_21d::double precision)   AS log_gdelt_state_stories_21d,
    LN(1 + g.gdelt_state_per10k_7d)                       AS log_gdelt_state_per10k_7d,
    LN(1 + g.gdelt_state_per10k_21d)                      AS log_gdelt_state_per10k_21d,
    g.gdelt_state_tone_mean_21d,
    g.gdelt_state_neg_share_21d,
    LN(1 + g.gdelt_county_articles_7d::double precision)  AS log_gdelt_county_articles_7d,
    LN(1 + g.gdelt_county_articles_21d::double precision) AS log_gdelt_county_articles_21d,
    LN(1 + g.gdelt_county_stories_21d::double precision)  AS log_gdelt_county_stories_21d,
    LN(1 + g.gdelt_us_per10k_7d)                          AS log_gdelt_us_per10k_7d
FROM model_county_day m
LEFT JOIN feature_gdelt_county_day g USING (fips, day);

CREATE UNIQUE INDEX ux_model_county_day_gdelt       ON model_county_day_gdelt (fips, day);
CREATE INDEX        ix_model_county_day_gdelt_day   ON model_county_day_gdelt (day);
CREATE INDEX        ix_model_county_day_gdelt_state ON model_county_day_gdelt (state, day);
CREATE INDEX        ix_model_county_day_gdelt_split ON model_county_day_gdelt (day)
    WHERE is_conus AND target_complete_7d;

ANALYZE model_county_day_gdelt;

INSERT INTO model_build_log (table_name, feature_set, n_rows, n_predictors, source_max_day, notes)
SELECT 'model_county_day_gdelt',
       'rf23+gdelt',
       COUNT(*),
       23 + 12,
       MAX(day),
       'Baseline model_county_day build_id '
       || (SELECT MAX(build_id) FROM model_build_log WHERE table_name = 'model_county_day')
       || ' + 12 GDELT GKG columns (state 8, county 3, national 1). GDELT columns are '
       || 'NULL outside GDELT coverage (2022-01-01 + window); fill or filter in Python.'
FROM model_county_day_gdelt;

COMMENT ON TABLE model_county_day_gdelt IS
    'Frozen modeling surface = model_county_day + GDELT news features. Snapshot; '
    'rebuild with sql/rebuild_model_county_day.py --variant gdelt; vintage in model_build_log.';
