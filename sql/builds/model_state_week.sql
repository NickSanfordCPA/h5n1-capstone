-- model_state_week.sql
-- The STATE baseline surface: one row per (CONUS state or DC, week ending Sunday). X has no
-- usable county signal, so all X analysis runs at this grain. This table carries no X data;
-- it is the no-X reference (arms S0 and S3 in docs/x_utility_spec.md).
--
-- Run only by:  uv run python sql/rebuild_model_county_day.py --variant state_week
-- Not a numbered migration, for the same reason as model_county_day_gdelt.sql.
--
-- TIMING. `day` is the Sunday the forecast is made at the end of. Every predictor window is
-- trailing and INCLUDES day: [day-w+1, day]. The county surface stops at day-1, but the X
-- windows end at day, so stopping the baseline a day earlier would let a post get credit
-- for an outbreak confirmed that same day. Nothing leaks: targets start at day+1.
--
-- KNOWN BIAS, in the baseline's favour. Wild-bird `day` is a collection date, published
-- later than it is dated, so recent wild detections were not all knowable at `day`. This
-- is inherited from the county surface and works against X, so it is left as it is.
--
-- NEIGHBORS. Two states are neighbors if any of their counties touch. Neighbor counts are
-- summed from STATE totals. Summing the county nbr_* columns instead would count a county
-- once for every county adjacent to it.
--
-- NULLS.
--   known_layer_inventory sums only reported counties, because SUM skips NULLs, so it is a
--   LOWER BOUND wherever NASS withheld values. layer_withheld_share reports how much of the
--   state's layer operations sit in withheld counties, and must travel with it. The column
--   is NULL only if every county was withheld. Do not COALESCE it to 0.
--   days_since_* NULL  -> right-censored, nothing in the trailing 365 days.
--   temp_*/precip_* NULL -> no county in the state reported. Median fills stay in Python.
--
-- WEIGHTS. Weather and migration pressure are operations-weighted county means, because
-- risk sits where the birds are. Where a state has no operations (DC) the plain mean is used.

SET LOCAL work_mem = '64MB';
SET LOCAL max_parallel_workers_per_gather = 0;

DROP TABLE IF EXISTS model_state_week;

CREATE TABLE model_state_week AS
WITH states AS (
    SELECT DISTINCT state FROM model_county_day WHERE is_conus
),
counties AS (
    SELECT DISTINCT state, fips FROM model_county_day WHERE is_conus
),
weeks AS (
    SELECT gs::date AS day
    FROM generate_series(DATE '2022-01-02',
                         (SELECT MAX(day) FROM model_county_day), INTERVAL '7 day') gs
),
grid AS (
    SELECT s.state, w.day FROM states s CROSS JOIN weeks w
),
adjacent AS (
    SELECT DISTINCT a.state, b.state AS nbr_state
    FROM county_adjacency x
    JOIN counties a ON a.fips = x.fips
    JOIN counties b ON b.fips = x.neighbor_fips
    WHERE a.state <> b.state
),
outbreak AS (
    SELECT c.state, o.fips, o.day
    FROM fact_h5n1_outbreak o
    JOIN counties c ON c.fips = o.fips
),
own AS (
    SELECT g.state, g.day,
           COUNT(o.day) FILTER (WHERE o.day > g.day - 7)                 AS outbreaks_7d,
           COUNT(o.day) FILTER (WHERE o.day > g.day - 21)                AS outbreaks_21d,
           COUNT(o.day) FILTER (WHERE o.day > g.day - 30)                AS outbreaks_30d,
           COUNT(o.day) FILTER (WHERE o.day > g.day - 60)                AS outbreaks_60d,
           COUNT(DISTINCT o.fips) FILTER (WHERE o.day > g.day - 7)       AS infected_counties_7d,
           COUNT(DISTINCT o.fips) FILTER (WHERE o.day > g.day - 21)      AS infected_counties_21d,
           COUNT(DISTINCT o.fips) FILTER (WHERE o.day > g.day - 60)      AS infected_counties_60d,
           g.day - MAX(o.day)                                            AS days_since_outbreak
    FROM grid g
    LEFT JOIN outbreak o
           ON o.state = g.state AND o.day > g.day - 365 AND o.day <= g.day
    GROUP BY g.state, g.day
),
wild AS (
    SELECT g.state, g.day,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day > g.day - 7),  0) AS wild_7d,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day > g.day - 21), 0) AS wild_21d,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day > g.day - 60), 0) AS wild_60d
    FROM grid g
    LEFT JOIN (SELECT c.state, f.day, f.detection_count
               FROM fact_wild_bird_detection f JOIN counties c ON c.fips = f.fips) w
           ON w.state = g.state AND w.day > g.day - 60 AND w.day <= g.day
    GROUP BY g.state, g.day
),
nbr AS (
    SELECT a.state, o.day,
           COUNT(*)                     AS n_neighbor_states,
           SUM(o.outbreaks_7d)          AS nbr_outbreaks_7d,
           SUM(o.outbreaks_21d)         AS nbr_outbreaks_21d,
           SUM(o.outbreaks_60d)         AS nbr_outbreaks_60d,
           SUM(o.infected_counties_7d)  AS nbr_infected_counties_7d,
           SUM(o.infected_counties_21d) AS nbr_infected_counties_21d,
           SUM(o.infected_counties_60d) AS nbr_infected_counties_60d,
           SUM(w.wild_7d)               AS nbr_wild_7d,
           SUM(w.wild_21d)              AS nbr_wild_21d,
           SUM(w.wild_60d)              AS nbr_wild_60d,
           MIN(o.days_since_outbreak)   AS days_since_outbreak_nbr
    FROM adjacent a
    JOIN own  o ON o.state = a.nbr_state
    JOIN wild w ON w.state = a.nbr_state AND w.day = o.day
    GROUP BY a.state, o.day
),
county AS (
    SELECT f.state, f.day,
           SUM(f.poultry_operations)  AS poultry_operations,
           SUM(f.broiler_operations)  AS broiler_operations,
           SUM(f.turkey_operations)   AS turkey_operations,
           SUM(f.duck_operations)     AS duck_operations,
           SUM(f.population)          AS population,
           SUM(f.layer_inventory)     AS known_layer_inventory,
           COALESCE(SUM(f.layer_operations) FILTER (WHERE f.layer_inventory IS NULL)::double precision
                    / NULLIF(SUM(f.layer_operations), 0), 0)                     AS layer_withheld_share,
           MAX(f.doy_sin)             AS doy_sin,
           MAX(f.doy_cos)             AS doy_cos,
           COALESCE(SUM(f.breeding_side_pressure * f.poultry_operations)
                      / NULLIF(SUM(f.poultry_operations), 0),
                    AVG(f.breeding_side_pressure))                               AS breeding_side_pressure,
           COALESCE(SUM(f.wintering_side_pressure * f.poultry_operations)
                      / NULLIF(SUM(f.poultry_operations), 0),
                    AVG(f.wintering_side_pressure))                              AS wintering_side_pressure,
           COALESCE(SUM(f.temp_min_7d_c * f.poultry_operations) FILTER (WHERE f.temp_min_7d_c IS NOT NULL)
                      / NULLIF(SUM(f.poultry_operations) FILTER (WHERE f.temp_min_7d_c IS NOT NULL), 0),
                    AVG(f.temp_min_7d_c))                                        AS temp_min_7d_c,
           COALESCE(SUM(f.temp_max_7d_c * f.poultry_operations) FILTER (WHERE f.temp_max_7d_c IS NOT NULL)
                      / NULLIF(SUM(f.poultry_operations) FILTER (WHERE f.temp_max_7d_c IS NOT NULL), 0),
                    AVG(f.temp_max_7d_c))                                        AS temp_max_7d_c,
           COALESCE(SUM(f.precip_7d_mm * f.poultry_operations) FILTER (WHERE f.precip_7d_mm IS NOT NULL)
                      / NULLIF(SUM(f.poultry_operations) FILTER (WHERE f.precip_7d_mm IS NOT NULL), 0),
                    AVG(f.precip_7d_mm))                                         AS precip_7d_mm
    FROM feature_county_day f
    JOIN weeks w ON w.day = f.day
    WHERE f.is_conus
    GROUP BY f.state, f.day
),
future AS (
    SELECT g.state, g.day,
           COALESCE(BOOL_OR(o.day <= g.day + 7),  FALSE) AS target_outbreak_next_7d,
           COALESCE(BOOL_OR(o.day <= g.day + 14), FALSE) AS target_outbreak_next_14d,
           COALESCE(BOOL_OR(o.day <= g.day + 30), FALSE) AS target_outbreak_next_30d
    FROM grid g
    LEFT JOIN outbreak o
           ON o.state = g.state AND o.day > g.day AND o.day <= g.day + 30
    GROUP BY g.state, g.day
),
last_outbreak AS (
    SELECT MAX(day) AS day FROM fact_h5n1_outbreak
)
SELECT
    g.state,
    g.day,
    -- targets, and whether each forward window is fully inside the loaded data
    t.target_outbreak_next_7d,
    t.target_outbreak_next_14d,
    t.target_outbreak_next_30d,
    (g.day + 7  <= l.day) AS target_complete_7d,
    (g.day + 14 <= l.day) AS target_complete_14d,
    (g.day + 30 <= l.day) AS target_complete_30d,
    (o.outbreaks_30d = 0) AS state_new_onset,

    -- S0: place, season, fixed exposure
    c.doy_sin,
    c.doy_cos,
    LN(1 + c.poultry_operations::double precision)    AS log_poultry_operations,
    LN(1 + c.broiler_operations::double precision)    AS log_broiler_operations,
    LN(1 + c.turkey_operations::double precision)     AS log_turkey_operations,
    LN(1 + c.duck_operations::double precision)       AS log_duck_operations,
    LN(1 + c.population::double precision)            AS log_population,
    -- CASE, not GREATEST: a NULL must stay NULL (see the LEAST/GREATEST trap in 012)
    CASE WHEN c.known_layer_inventory IS NULL THEN NULL
         ELSE LN(1 + c.known_layer_inventory::double precision) END AS log_known_layer_inventory,
    c.layer_withheld_share,
    COALESCE(n.n_neighbor_states, 0)                  AS n_neighbor_states,

    -- S3 adds: the state's own epidemic
    LN(1 + o.outbreaks_7d::double precision)          AS log_outbreaks_7d,
    LN(1 + o.outbreaks_21d::double precision)         AS log_outbreaks_21d,
    LN(1 + o.outbreaks_60d::double precision)         AS log_outbreaks_60d,
    LN(1 + o.infected_counties_7d::double precision)  AS log_infected_counties_7d,
    LN(1 + o.infected_counties_21d::double precision) AS log_infected_counties_21d,
    LN(1 + o.infected_counties_60d::double precision) AS log_infected_counties_60d,
    CASE WHEN o.days_since_outbreak IS NULL THEN NULL
         ELSE o.days_since_outbreak::double precision / 100.0 END AS days_since_outbreak,
    (o.days_since_outbreak IS NULL)::int              AS days_since_outbreak_censored,
    LN(1 + w.wild_7d::double precision)               AS log_wild_detections_7d,
    LN(1 + w.wild_21d::double precision)              AS log_wild_detections_21d,
    LN(1 + w.wild_60d::double precision)              AS log_wild_detections_60d,

    -- S3 adds: neighboring states
    LN(1 + COALESCE(n.nbr_outbreaks_7d, 0)::double precision)          AS log_nbr_outbreaks_7d,
    LN(1 + COALESCE(n.nbr_outbreaks_21d, 0)::double precision)         AS log_nbr_outbreaks_21d,
    LN(1 + COALESCE(n.nbr_outbreaks_60d, 0)::double precision)         AS log_nbr_outbreaks_60d,
    LN(1 + COALESCE(n.nbr_infected_counties_7d, 0)::double precision)  AS log_nbr_infected_counties_7d,
    LN(1 + COALESCE(n.nbr_infected_counties_21d, 0)::double precision) AS log_nbr_infected_counties_21d,
    LN(1 + COALESCE(n.nbr_infected_counties_60d, 0)::double precision) AS log_nbr_infected_counties_60d,
    LN(1 + COALESCE(n.nbr_wild_7d, 0)::double precision)               AS log_nbr_wild_detections_7d,
    LN(1 + COALESCE(n.nbr_wild_21d, 0)::double precision)              AS log_nbr_wild_detections_21d,
    LN(1 + COALESCE(n.nbr_wild_60d, 0)::double precision)              AS log_nbr_wild_detections_60d,
    CASE WHEN n.days_since_outbreak_nbr IS NULL THEN NULL
         ELSE n.days_since_outbreak_nbr::double precision / 100.0 END  AS days_since_outbreak_nbr,
    (n.days_since_outbreak_nbr IS NULL)::int                           AS days_since_outbreak_nbr_censored,

    -- S3 adds: migration and weather. Weather is trailing and inclusive, as everywhere.
    LN(1 + c.breeding_side_pressure)                  AS log_breeding_side_pressure,
    LN(1 + c.wintering_side_pressure)                 AS log_wintering_side_pressure,
    c.temp_min_7d_c,
    c.temp_max_7d_c,
    c.precip_7d_mm
FROM grid g
CROSS JOIN last_outbreak l
JOIN own    o ON o.state = g.state AND o.day = g.day
JOIN wild   w ON w.state = g.state AND w.day = g.day
JOIN future t ON t.state = g.state AND t.day = g.day
JOIN county c ON c.state = g.state AND c.day = g.day
LEFT JOIN nbr n ON n.state = g.state AND n.day = g.day;

CREATE UNIQUE INDEX ux_model_state_week     ON model_state_week (state, day);
CREATE INDEX        ix_model_state_week_day ON model_state_week (day);

ANALYZE model_state_week;

INSERT INTO model_build_log (table_name, feature_set, n_rows, n_predictors, source_max_day, notes)
SELECT 'model_state_week',
       'state-week baseline (S0 + S3 columns)',
       COUNT(*),
       (SELECT COUNT(*) FROM information_schema.columns
         WHERE table_name = 'model_state_week'
           AND column_name NOT IN ('state', 'day', 'state_new_onset')
           AND LEFT(column_name, 7) <> 'target_'),
       MAX(day),
       'Week ending Sunday; trailing windows include day; built from fact_h5n1_outbreak, '
       || 'fact_wild_bird_detection and feature_county_day. No X data.'
FROM model_state_week;

COMMENT ON TABLE model_state_week IS
    'Frozen state-week modeling surface for the X analysis, no X columns. Snapshot; rebuild with '
    'sql/rebuild_model_county_day.py --variant state_week; vintage in model_build_log.';
