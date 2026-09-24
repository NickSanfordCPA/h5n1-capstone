-- 012_model_county_day.sql
-- The MODELING SURFACE, narrowed. One row per (state, fips, day), carrying only the
-- variables the Random Forest selection in notebooks/regression_baseline.ipynb kept,
-- already transformed, plus the target and the two filter flags.
--
-- WHY A PHYSICAL TABLE AND NOT A MATERIALIZED VIEW.
--   feature_county_day (011) is a matview and gets refreshed whenever an upstream fact
--   lands. A model fitted last week and a model fitted today would then silently be
--   fitted on different data with no way to tell after the fact. This table is a FROZEN
--   SNAPSHOT with a vintage recorded in model_build_log, so a result is reproducible.
--   The cost is that it does NOT track feature_county_day: after any refresh of the
--   011 chain it is stale until deliberately rebuilt.
--
--   REBUILD:  uv run python sql/rebuild_model_county_day.py
--   (run_migrations.py applies each file once and then skips it, so re-running the
--    migration will NOT rebuild this table -- use the script.)
--
-- ---------------------------------------------------------------------------
-- WHICH VARIABLES, AND WHY THESE
-- ---------------------------------------------------------------------------
-- Section 10.2 of regression_baseline.ipynb ranks Random Forest feature importance and
-- sweeps a threshold. The retained set here is importance > 0.020, which section 12
-- then showed to be the best of four model x feature-set combinations (Random Forest +
-- RF features, PR-AUC 0.1315, 37.1x lift over base rate).
--
--   thr >= 0.050:  4 feats   PR-AUC 0.1191   lift 47.5x   (ROC collapses to 0.704)
--   thr >= 0.030: 11 feats   PR-AUC 0.1380   lift 55.0x
--   thr >= 0.020: 22 feats   PR-AUC 0.1428   lift 56.9x   <-- this table
--   thr >= 0.010: 29 feats   PR-AUC 0.1445   lift 57.6x
--   all           35 feats   PR-AUC 0.1452   lift 57.9x
--
-- COUNT DISCREPANCY, KNOWN AND UNRESOLVED. The sweep's own output says 22 features at
--   the 0.020 threshold; the importance table printed in sections 10.2 and 11 of the
--   same notebook lists 23 rows above 0.020. The two came from different executions of
--   the forest. This file carries all 23, because the boundary variable
--   (log_nbr_infected_counties_7d, importance 0.020029) is genuinely above the cut in
--   the table of record. If you re-run the forest, derive the list by thresholding
--   importances_forest rather than trusting either printed table -- including this one.
--
-- DELIBERATELY ABSENT, each for a stated reason:
--   log_layer_operations      collinear with log_poultry_operations (section 9 VIF);
--                             the notebook's rule is keep poultry, drop layer.
--   weather_missing           an indicator with no explanatory content of its own.
--                             Not needed here anyway: temp/precip stay NULL below, so
--                             the indicator is exactly (temp_max_7d_c IS NULL).
--   log_band_count_21d,       fact_bird_density stops 2025-07-10, inside the test
--   log_nbr_band_count_21d    window -- a train/test shift unrelated to epidemiology.
--   log_wild_detections_21d,  in the LOGISTIC selection, not the RF one. Add them back
--   the *_censored indicators,  if you fit the logistic variant off this table.
--   layer_inventory_suppressed
--   n_neighbors               in neither final selection.
--   post_count, sentiment_*   fact_social_sentiment is still empty. This table is the
--                             NO-SENTIMENT baseline surface on purpose -- sentiment
--                             joins on (fips, day) when it lands.
--
-- ---------------------------------------------------------------------------
-- WHAT IS TRANSFORMED HERE, AND WHAT IS DELIBERATELY LEFT TO PYTHON
-- ---------------------------------------------------------------------------
-- DONE HERE (deterministic, split-independent):
--   log1p on every count and exposure column, matching build_X() in the notebook.
--   Recency capped at 400 days and divided by 100, to sit on a comparable scale.
--
-- NOT DONE HERE (split-dependent -- doing it in SQL would leak):
--   The median fills. build_X() fills missing weather and suppressed layer inventory
--   with a median computed over the TRAINING window only. Storing those fills would
--   bake one particular SPLIT_DATE into the data and silently invalidate every model
--   fitted after the split moves. So temp_min_7d_c, temp_max_7d_c, precip_7d_mm and
--   log_layer_inventory STAY NULL here. Fill them in pandas, from train-window medians.
--
-- NULL therefore still means what it means everywhere else in this schema:
--   log_layer_inventory NULL         -> NASS withheld the value. Not zero. The
--                                       suppression flag is derivable by joining
--                                       county_poultry_census.layer_operations > 0.
--   temp_*/precip_* NULL             -> no station reported. Not zero degrees.
--   days_since_outbreak_* NULL       -> right-censored: nothing in the trailing 365
--                                       days. Not "an outbreak just happened."
--   every log_* count column         -> never NULL; absence of a sparse fact is a
--                                       real zero and 011 already coalesced it.
--                                       (Asserted against the database, not assumed.)
--
-- POSTGRES TRAP, and the reason several columns below are wrapped in a CASE. LEAST and
-- GREATEST IGNORE null arguments -- they return NULL only when EVERY argument is NULL.
-- So GREATEST(layer_inventory, 0) is 0 when the value was withheld, and
-- LEAST(days_since_outbreak_county, 400) is 400 when nothing happened in the trailing
-- year. Written the obvious way, this file zero-filled 830,676 suppressed inventory
-- rows and cap-filled 7,028,885 censored recency rows, silently, in exactly the two
-- places the project's invariants forbid it. Do not "simplify" the CASEs away.
--
-- MIGRATION GEOMETRY: the side-pressure columns inherit whatever
-- feature_migration_pressure is currently built to -- the production sector 90 deg
-- wide / 300 km / exp(-d/150km) / 21d. The section 7 sweep found the alternatives to be
-- within noise (48-51x lift across the whole grid), and at the nominal "optimum" both
-- side-pressure terms lose statistical significance. Rebuilding the pressure table
-- changes these columns' meaning without changing their names -- record it in
-- model_build_log.notes if you ever do.

-- ---------------------------------------------------------------------------
-- Vintage stamp. A frozen snapshot with no recorded provenance is worse than a view.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_build_log (
    build_id       BIGSERIAL PRIMARY KEY,
    table_name     TEXT        NOT NULL,
    built_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    feature_set    TEXT        NOT NULL,   -- which selection produced the column list
    n_rows         BIGINT,
    n_predictors   SMALLINT,
    source_max_day DATE,                   -- data horizon of feature_county_day at build
    notes          TEXT
);

DROP TABLE IF EXISTS model_county_day;

CREATE TABLE model_county_day AS
SELECT
    -- ---- keys: the requested grain ----
    f.state,
    f.fips,
    f.day,

    -- ---- target and the two flags that make it usable ----
    -- target_complete_7d is FALSE in the last 7 days of loaded data, where the forward
    -- window is truncated and the zeros are partly fictional. Filter on it before
    -- evaluating anything. is_conus excludes AK/HI, which have no weather, no migration
    -- axis and no adjacency, and would enter as structural NULLs.
    f.target_outbreak_next_7d,
    f.target_complete_7d,
    f.is_conus,

    -- ---- own-county outbreak history (past-only, [day-w, day-1]) ----
    LN(1 + GREATEST(f.own_outbreaks_7d,  0)::double precision) AS log_own_outbreaks_7d,
    LN(1 + GREATEST(f.own_outbreaks_21d, 0)::double precision) AS log_own_outbreaks_21d,
    LN(1 + GREATEST(f.own_outbreaks_60d, 0)::double precision) AS log_own_outbreaks_60d,

    -- ---- neighbor signal, queen adjacency, past-only ----
    LN(1 + GREATEST(f.nbr_infections_7d,  0)::double precision)        AS log_nbr_infections_7d,
    LN(1 + GREATEST(f.nbr_infections_21d, 0)::double precision)        AS log_nbr_infections_21d,
    LN(1 + GREATEST(f.nbr_infections_60d, 0)::double precision)        AS log_nbr_infections_60d,
    LN(1 + GREATEST(f.nbr_infected_counties_7d, 0)::double precision)  AS log_nbr_infected_counties_7d,
    LN(1 + GREATEST(f.nbr_wild_detections_21d,  0)::double precision)  AS log_nbr_wild_detections_21d,

    -- ---- directional migration pressure (production geometry; see header) ----
    LN(1 + GREATEST(f.breeding_side_pressure,  0)::double precision)   AS log_breeding_side_pressure,
    LN(1 + GREATEST(f.wintering_side_pressure, 0)::double precision)   AS log_wintering_side_pressure,

    -- ---- recency: right-censored. NULL = nothing in the trailing 365 days. ----
    -- The CASE is NOT redundant. Postgres LEAST/GREATEST IGNORE null arguments and
    -- return NULL only when every argument is NULL -- so LEAST(NULL, 400) is 400, which
    -- would fill 7.0M censored rows with the cap and destroy the censoring. Same trap
    -- on GREATEST below. Verified against this database, not assumed.
    CASE WHEN f.days_since_outbreak_county IS NULL THEN NULL
         ELSE LEAST(f.days_since_outbreak_county, 400)::double precision / 100.0
    END AS days_since_outbreak_county,
    CASE WHEN f.days_since_outbreak_neighbor IS NULL THEN NULL
         ELSE LEAST(f.days_since_outbreak_neighbor, 400)::double precision / 100.0
    END AS days_since_outbreak_neighbor,

    -- ---- poultry exposure: STATIC 2022 Census of Agriculture, repeated every day ----
    -- Operations counts are 0 pct suppressed and 011 coalesced them to 0, so a 0 here
    -- is a real absence, and the bare GREATEST clip is safe on them.
    LN(1 + GREATEST(f.poultry_operations, 0)::double precision) AS log_poultry_operations,
    LN(1 + GREATEST(f.broiler_operations, 0)::double precision) AS log_broiler_operations,
    LN(1 + GREATEST(f.turkey_operations,  0)::double precision) AS log_turkey_operations,
    LN(1 + GREATEST(f.duck_operations,    0)::double precision) AS log_duck_operations,
    LN(1 + GREATEST(f.population,         0)::double precision) AS log_population,
    -- layer_inventory is the opposite case and needs the explicit CASE: NULL means NASS
    -- WITHHELD, and GREATEST(NULL, 0) would return 0 -- "this county has no layers" --
    -- for 830,676 rows that demonstrably behave like mid-to-large layer operations.
    CASE WHEN f.layer_inventory IS NULL THEN NULL
         ELSE LN(1 + GREATEST(f.layer_inventory, 0)::double precision)
    END AS log_layer_inventory,

    -- ---- weather, trailing 7 days INCLUSIVE of today. ----
    -- The one deliberate leakage asymmetry in the project: weather is contemporaneously
    -- observed, reported detections are not. Do not "fix" it. Raw, not logged --
    -- temperature can be negative.
    f.temp_min_7d_c,
    f.temp_max_7d_c,
    f.precip_7d_mm,

    -- ---- seasonality ----
    f.doy_sin,
    f.doy_cos

FROM feature_county_day f;

-- ---------------------------------------------------------------------------
-- Indexes. UNIQUE on the grain is the assertion that the grain is what we claim.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX ux_model_county_day       ON model_county_day (fips, day);
CREATE INDEX        ix_model_county_day_day   ON model_county_day (day);
CREATE INDEX        ix_model_county_day_state ON model_county_day (state, day);
-- The temporal split reads "day < SPLIT_DATE AND is_conus AND target_complete_7d" on
-- every fit; this partial index covers that filter.
CREATE INDEX        ix_model_county_day_split ON model_county_day (day)
    WHERE is_conus AND target_complete_7d;

ANALYZE model_county_day;

INSERT INTO model_build_log (table_name, feature_set, n_rows, n_predictors, source_max_day, notes)
SELECT 'model_county_day',
       'regression_baseline.ipynb section 10.2, RF importance > 0.020',
       COUNT(*),
       23,
       MAX(day),
       'Transformed (log1p, recency cap/scale) but NOT median-filled: weather and '
       || 'log_layer_inventory stay NULL because build_X fills them from train-window '
       || 'medians. Migration pressure at production geometry 90deg/300km/150km decay.'
FROM model_county_day;

COMMENT ON TABLE model_county_day IS
    'Frozen modeling surface, one row per (state, fips, day). RF-selected predictors '
    'only, log1p-transformed, NULLs preserved. Snapshot -- does NOT track '
    'feature_county_day. Rebuild with sql/rebuild_model_county_day.py; vintage in '
    'model_build_log.';
