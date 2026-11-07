-- model_county_day_35.sql
-- The frozen baseline surface (model_county_day, 23 RF predictors) PLUS the 12 predictors
-- the RF selection dropped, giving the full 35-feature set of regression_baseline.ipynb
-- section 10 (the list printed with importances_forest; build_X() defines the transforms).
-- Same rows as the baseline. This is the county baseline reported on its own (arm C3) and
-- the source of the county roll-up reference for the state baseline. It carries no X data.
--
-- Why 35: on new-onset positives the 35-feature forest beat the selected 23, because the
-- importance cut dropped variables that help detect a county's first outbreak.
--
-- Not a numbered migration, for the same reason as model_county_day_gdelt.sql: it reads
-- populated tables, and it must rebuild on demand. Run only by:
--     uv run python sql/rebuild_model_county_day.py --variant rf35
-- The baseline table is read, never modified. Rebuild it first if it is stale.
--
-- Split-dependent median fills stay in Python, exactly as in 012. log_layer_operations is
-- absent on purpose: the 35 exclude it (VIF, collinear with log_poultry_operations).
-- The three indicators are stored explicitly so the 35 can be selected by name, though
-- the two *_censored columns are derivable from the NULLs in the recency columns and
-- weather_missing from temp_max_7d_c IS NULL.

SET LOCAL work_mem = '64MB';
-- Serial on purpose: see the note in model_county_day_gdelt.sql (db-g1-small cannot grant
-- the shared memory a parallel hash join of two 7.5M-row relations asks for).
SET LOCAL max_parallel_workers_per_gather = 0;

DROP TABLE IF EXISTS model_county_day_35;

CREATE TABLE model_county_day_35 AS
SELECT
    m.*,
    -- counts below were coalesced in 011, so NULL cannot occur and 0 is a real absence
    LN(1 + f.wild_detections_7d::double precision)         AS log_wild_detections_7d,
    LN(1 + f.wild_detections_21d::double precision)        AS log_wild_detections_21d,
    LN(1 + f.wild_detections_60d::double precision)        AS log_wild_detections_60d,
    LN(1 + f.nbr_wild_detections_7d::double precision)     AS log_nbr_wild_detections_7d,
    LN(1 + f.nbr_wild_detections_60d::double precision)    AS log_nbr_wild_detections_60d,
    LN(1 + f.nbr_infected_counties_21d::double precision)  AS log_nbr_infected_counties_21d,
    LN(1 + f.nbr_infected_counties_60d::double precision)  AS log_nbr_infected_counties_60d,
    f.n_neighbors,
    (f.days_since_outbreak_county   IS NULL)::int          AS days_since_outbreak_county_censored,
    (f.days_since_outbreak_neighbor IS NULL)::int          AS days_since_outbreak_neighbor_censored,
    -- withheld, not absent: NASS suppressed the value for a county that has layer operations
    (f.layer_inventory IS NULL AND f.layer_operations > 0)::int AS layer_inventory_suppressed,
    (f.temp_max_7d_c IS NULL)::int                         AS weather_missing
FROM model_county_day m
JOIN feature_county_day f USING (fips, day);

CREATE UNIQUE INDEX ux_model_county_day_35       ON model_county_day_35 (fips, day);
CREATE INDEX        ix_model_county_day_35_day   ON model_county_day_35 (day);
CREATE INDEX        ix_model_county_day_35_state ON model_county_day_35 (state, day);

ANALYZE model_county_day_35;

INSERT INTO model_build_log (table_name, feature_set, n_rows, n_predictors, source_max_day, notes)
SELECT 'model_county_day_35',
       'regression_baseline.ipynb section 10, all 35 RF candidates',
       COUNT(*),
       23 + 12,
       MAX(day),
       'Baseline model_county_day build_id '
       || (SELECT MAX(build_id) FROM model_build_log WHERE table_name = 'model_county_day')
       || ' + the 12 predictors below the importance cut. No X data.'
FROM model_county_day_35;

COMMENT ON TABLE model_county_day_35 IS
    'Frozen modeling surface = model_county_day + 12 predictors, the full 35-feature RF set. '
    'Snapshot; rebuild with sql/rebuild_model_county_day.py --variant rf35; vintage in model_build_log.';
