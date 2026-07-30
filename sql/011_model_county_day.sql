-- 011_model_county_day.sql
-- THE model-ready join: exactly one row per (fips, day), every variable in one place.
-- Supersedes the strawman feature_county_day from 003 (dropped and rebuilt here) so
-- there is only ever one canonical modeling surface.
--
-- MATERIALIZED, not a plain view, for two reasons: (a) a plain view would recompute a
-- 10-way join over a 7.5M-row grid on every query, and (b) a materialized view takes
-- indexes -- so there is no reason to hand-maintain a table just to get them.
--
-- REBUILD ORDER MATTERS. This view sits on top of three upstream feature tables, and
-- refreshing it alone will silently carry stale lags:
--     SET work_mem = '64MB';                          -- instance default is 4MB
--     REFRESH MATERIALIZED VIEW feature_spatial_lag;  -- 008, neighbor + own history
--     REFRESH MATERIALIZED VIEW feature_weather_lag;  -- 009, trailing 7-day weather
--     REFRESH MATERIALIZED VIEW feature_county_day;   -- this file
-- All three have unique indexes, so REFRESH ... CONCURRENTLY works if you need to avoid
-- locking out a teammate mid-session.
--
-- ---------------------------------------------------------------------------
-- CONVENTIONS THAT MATTER (do not "simplify" these away)
-- ---------------------------------------------------------------------------
-- NULL vs 0. Sparse COUNT facts (outbreaks, wild-bird detections, bandings, migration
--   pressure, everything from feature_spatial_lag) are stored only on days something
--   happened, so absence means ZERO and is coalesced. Weather is a MEASUREMENT:
--   absence means MISSING and stays NULL. Suppressed NASS poultry values are also
--   MISSING and stay NULL -- see the poultry note below, it is the subtlest case here.
--
-- Leakage. Every event-derived predictor is PAST-ONLY: trailing windows are
--   [day - w, day - 1] with no same-day term, matching LAG_DAYS=21 in
--   h5n1/features/migration_pressure.py. The WEATHER windows are the deliberate
--   exception -- they are [day - 6, day], inclusive, because weather is
--   contemporaneously observed while reported detections are not. See 009 for the
--   full argument. The only forward-looking columns are prefixed target_.
--
-- Right censoring. target_outbreak_next_Nd near the data horizon is computed against a
--   truncated future, so its zeros are partly fictional. target_complete_Nd flags
--   whether the forward window fits inside the loaded data -- filter on it before
--   evaluating, or the tail of every split looks artificially quiet.
--
-- POULTRY EXPOSURE (010) -- three states, and collapsing them will bias the model:
--     *_operations = 0 and value = 0     -> county genuinely has none of that commodity
--     *_operations > 0 and value IS NULL -> has it, amount WITHHELD by NASS ("(D)")
--     *_operations > 0 and value > 0     -> fully reported
--   Operations counts are 0% suppressed and so are coalesced to 0; inventory and sales
--   values are 11-28% suppressed and are NEVER coalesced. Suppression is derivable as
--   (operations > 0 AND value IS NULL); no separate flag column is stored.
--   These are STATIC 2022 Census attributes repeated on every day of a county's rows.
--
-- KNOWN COVERAGE GAPS, exposed as flags rather than silent zeros:
--   is_conus           -- AK/HI have no weather, no migration axis, no proximity
--   has_weather        -- weather is GHCN nearest-station, CONUS only
--   precip_days_7d /
--   temp_days_7d       -- how many days actually fed each 7-day window. A 7-day
--                         precip total built from 3 reporting days is not comparable
--                         to one built from 7, and SUM() hides the difference.
--   has_band_coverage  -- fact_bird_density STOPS 2025-07-10. That is the NABBP
--                         source's own extent, not a load bug: releases are annual and
--                         the current one is "1960-2025 retrieved 2025-07-11". Without
--                         this flag the most recent year of banding reads as real zeros.
--   fact_social_sentiment and fact_egg_economics are EMPTY as of this migration. The
--   sentiment columns are wired in anyway so downstream code does not change when the
--   data lands. Egg economics is deliberately NOT joined: its fips is nullable
--   (NULL = national), so an equi-join would silently drop every national row.
--
-- NOTE on sql/run_migrations.py: it runs files through psycopg's placeholder parser,
--   so a literal percent sign anywhere in a migration -- comments included -- raises
--   "incomplete placeholder". Double it up if you ever need one.
--
-- TUNABLE PARAMETERS:
--   neighbor trailing windows  7 / 21 / 60 days -- set in 008_spatial_lag.sql
--   weather trailing window    7 days           -- set in 009_weather_lag.sql
--   forward target windows     7 / 14 days      -- set below
--   migration pressure         sector 90 deg / 300 km / exp(-d/150km) / 21d -- NOT set
--                              here; baked into feature_migration_pressure by
--                              h5n1/features/migration_pressure.py. Rebuild that table
--                              to change them, then REFRESH this view.

SET LOCAL work_mem = '64MB';
SET LOCAL maintenance_work_mem = '128MB';

DROP MATERIALIZED VIEW IF EXISTS feature_county_day;

CREATE MATERIALIZED VIEW feature_county_day AS
WITH
-- Last day any OBSERVATIONAL source carries data. Derived features are excluded on
-- purpose: feature_migration_pressure runs 21 days past the last detection because of
-- its trailing spread, which is not real observation.
horizon AS (
    SELECT GREATEST(
        (SELECT MAX(day) FROM fact_h5n1_outbreak),
        (SELECT MAX(day) FROM fact_wild_bird_detection),
        (SELECT MAX(day) FROM fact_weather),
        (SELECT MAX(day) FROM fact_bird_density)
    ) AS max_day,
    (SELECT MAX(day) FROM fact_bird_density) AS band_max_day
),

grid AS (
    SELECT c.fips, d.day
    FROM dim_county c
    CROSS JOIN dim_date d
    WHERE d.day <= (SELECT max_day FROM horizon)
),

-- Collapse flock_type so the DV is one row per county-day.
outbreak_day AS (
    SELECT fips, day,
           bool_or(confirmed_flag)                     AS confirmed,
           COUNT(*) FILTER (WHERE confirmed_flag)::int AS outbreak_events,
           SUM(birds_affected)                         AS birds_affected
    FROM fact_h5n1_outbreak
    GROUP BY fips, day
),

-- FORWARD TARGETS. The join window is the widest (14d); the 7d target is a FILTER.
fwd AS (
    SELECT o.fips, d.day,
           bool_or(o.day <= d.day + 7) AS next_7d,
           TRUE                        AS next_14d
    FROM outbreak_day o
    JOIN dim_date d ON o.day BETWEEN d.day + 1 AND d.day + 14
    WHERE o.confirmed
    GROUP BY o.fips, d.day
),

-- Static: how many counties border this one. Driven off dim_county, not adjacency, so
-- the five zero-neighbor counties get 0 rather than dropping out of the view.
nbr_count AS (
    SELECT c.fips, COUNT(a.neighbor_fips)::int AS n_neighbors
    FROM dim_county c
    LEFT JOIN county_adjacency a ON a.fips = c.fips
    GROUP BY c.fips
)

SELECT
    -- ---- keys ----
    g.fips,
    g.day,

    -- ---- static county attributes ----
    c.state,
    c.county_name,
    ax.flyway,
    c.centroid_lat,
    c.centroid_lon,
    c.population,
    c.land_area_sqmi,
    CASE WHEN c.land_area_sqmi > 0
         THEN c.population / c.land_area_sqmi END  AS pop_density_sqmi,
    nc.n_neighbors,                                -- (1) number of surrounding counties
    (LEFT(g.fips, 2) NOT IN ('02', '15'))          AS is_conus,

    -- ---- POULTRY EXPOSURE, static 2022 Census of Agriculture (010) ----
    -- Operations counts are never suppressed -> coalesce to 0.
    COALESCE(pc.poultry_operations, 0)             AS poultry_operations,
    COALESCE(pc.layer_operations,   0)             AS layer_operations,
    COALESCE(pc.broiler_operations, 0)             AS broiler_operations,
    COALESCE(pc.turkey_operations,  0)             AS turkey_operations,
    COALESCE(pc.duck_operations,    0)             AS duck_operations,
    -- Magnitudes ARE suppressed -> NULL means withheld, NOT zero. Do not coalesce.
    pc.layer_inventory,
    pc.broiler_sales_head,
    pc.turkey_inventory,
    pc.duck_inventory,
    pc.poultry_sales_usd,

    -- ---- calendar ----
    d.year,
    d.month,
    d.iso_week,
    d.day_of_year,
    d.season,
    d.is_us_holiday,
    SIN(2 * PI() * d.day_of_year / 365.25)         AS doy_sin,
    COS(2 * PI() * d.day_of_year / 365.25)         AS doy_cos,

    -- ---- DV, same day (legitimate as own-history; never as a target) ----
    COALESCE(o.confirmed, FALSE)                   AS outbreak,
    COALESCE(o.outbreak_events, 0)                 AS outbreak_events,
    COALESCE(o.birds_affected, 0)                  AS birds_affected,

    -- ---- TARGETS (forward-looking -- NEVER use these as predictors) ----
    COALESCE(f.next_7d, FALSE)                     AS target_outbreak_next_7d,
    COALESCE(f.next_14d, FALSE)                    AS target_outbreak_next_14d,
    (g.day + 7  <= h.max_day)                      AS target_complete_7d,
    (g.day + 14 <= h.max_day)                      AS target_complete_14d,

    -- ---- weather, same day (MEASUREMENT: NULL means missing, never 0) ----
    w.temp_min_c,
    w.temp_max_c,
    w.precip_mm,
    (w.fips IS NOT NULL)                           AS has_weather,

    -- ---- weather, trailing 7 days INCLUSIVE of today (009) ----
    wl.precip_7d_mm,
    wl.temp_min_7d_c,
    wl.temp_max_7d_c,
    wl.precip_days_7d,
    wl.temp_days_7d,

    -- ---- own county, same day ----
    COALESCE(wb.detection_count, 0)                AS wild_detections,
    COALESCE(wb.n_species, 0)                      AS wild_n_species,
    COALESCE(bd.band_count_smoothed, 0)            AS band_count,
    (g.day <= h.band_max_day)                      AS has_band_coverage,

    -- ---- own county, trailing past-only (008) ----
    COALESCE(sl.wild_detections_7d,  0)            AS wild_detections_7d,
    COALESCE(sl.wild_detections_21d, 0)            AS wild_detections_21d,
    COALESCE(sl.wild_detections_60d, 0)            AS wild_detections_60d,
    COALESCE(sl.band_count_7d,  0)                 AS band_count_7d,
    COALESCE(sl.band_count_21d, 0)                 AS band_count_21d,
    COALESCE(sl.band_count_60d, 0)                 AS band_count_60d,
    COALESCE(sl.own_outbreaks_7d,  0)              AS own_outbreaks_7d,
    COALESCE(sl.own_outbreaks_21d, 0)              AS own_outbreaks_21d,
    COALESCE(sl.own_outbreaks_60d, 0)              AS own_outbreaks_60d,

    -- ---- directional migration pressure (single implementation; params in builder) ----
    COALESCE(mp.breeding_side_pressure,  0)        AS breeding_side_pressure,
    COALESCE(mp.wintering_side_pressure, 0)        AS wintering_side_pressure,

    -- ---- NEIGHBOR AGGREGATES: queen adjacency, trailing, past-only (008) ----
    COALESCE(sl.nbr_infected_counties_7d,  0)      AS nbr_infected_counties_7d,   -- (2)
    COALESCE(sl.nbr_infected_counties_21d, 0)      AS nbr_infected_counties_21d,
    COALESCE(sl.nbr_infected_counties_60d, 0)      AS nbr_infected_counties_60d,
    COALESCE(sl.nbr_infections_7d,  0)             AS nbr_infections_7d,          -- (3)
    COALESCE(sl.nbr_infections_21d, 0)             AS nbr_infections_21d,
    COALESCE(sl.nbr_infections_60d, 0)             AS nbr_infections_60d,
    COALESCE(sl.nbr_wild_detections_7d,  0)        AS nbr_wild_detections_7d,
    COALESCE(sl.nbr_wild_detections_21d, 0)        AS nbr_wild_detections_21d,
    COALESCE(sl.nbr_wild_detections_60d, 0)        AS nbr_wild_detections_60d,
    COALESCE(sl.nbr_band_count_7d,  0)             AS nbr_band_count_7d,          -- (4)
    COALESCE(sl.nbr_band_count_21d, 0)             AS nbr_band_count_21d,
    COALESCE(sl.nbr_band_count_60d, 0)             AS nbr_band_count_60d,

    -- ---- recency (NULL = nothing in the trailing 365 days) ----
    sl.days_since_outbreak_county,
    sl.days_since_outbreak_neighbor,

    -- ---- social sentiment (table EMPTY as of this migration; wired for when it lands.
    --      post_count is a count so it coalesces to 0; the two scores stay NULL because
    --      the mean of zero posts is undefined, not neutral.) ----
    COALESCE(s.post_count, 0)                      AS post_count,
    s.sentiment_mean,
    s.sentiment_neg_share

FROM grid g
CROSS JOIN horizon h
JOIN dim_county c  ON c.fips  = g.fips
JOIN dim_date   d  ON d.day   = g.day
JOIN nbr_count  nc ON nc.fips = g.fips
LEFT JOIN county_migration_axis      ax ON ax.fips = g.fips
LEFT JOIN county_poultry_census      pc ON pc.fips = g.fips AND pc.census_year = 2022
LEFT JOIN outbreak_day               o  ON o.fips  = g.fips AND o.day  = g.day
LEFT JOIN fwd                        f  ON f.fips  = g.fips AND f.day  = g.day
LEFT JOIN fact_weather               w  ON w.fips  = g.fips AND w.day  = g.day
LEFT JOIN feature_weather_lag        wl ON wl.fips = g.fips AND wl.day = g.day
LEFT JOIN fact_wild_bird_detection   wb ON wb.fips = g.fips AND wb.day = g.day
LEFT JOIN fact_bird_density          bd ON bd.fips = g.fips AND bd.day = g.day
LEFT JOIN feature_migration_pressure mp ON mp.fips = g.fips AND mp.day = g.day
LEFT JOIN fact_social_sentiment      s  ON s.fips  = g.fips AND s.day  = g.day
LEFT JOIN feature_spatial_lag        sl ON sl.fips = g.fips AND sl.day = g.day;

-- UNIQUE is required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX ux_feature_county_day       ON feature_county_day (fips, day);
CREATE INDEX        ix_feature_county_day_day   ON feature_county_day (day);
CREATE INDEX        ix_feature_county_day_state ON feature_county_day (state, day);
