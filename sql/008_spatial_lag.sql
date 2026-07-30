-- 008_spatial_lag.sql
-- Trailing-window spatial-lag and own-county-history features, at the canonical
-- (fips, day) grain. Split out of 009_model_county_day.sql for one hard reason:
-- Cloud SQL db-g1-small ships temp_file_limit = 1 GB and work_mem = 4 MB, and doing
-- these range joins inside the same statement as the 7.5M-row modeling grid blows
-- past it ("temporary file size exceeds temp_file_limit"). Each piece is cheap in
-- isolation; the accumulation is what fails. Keeping them separate also makes the
-- neighbor features independently inspectable and independently rebuildable.
--
-- STORED SPARSE: only county-days where at least one contributing window is non-empty
-- get a row. 009 coalesces the misses to 0. Do not "fix" that by densifying here --
-- the dense product is 7.5M rows and that is precisely what does not fit.
--
-- NEIGHBORHOOD = QUEEN ADJACENCY (county_adjacency; symmetric, self excluded, mean
-- 5.9 neighbors). Five counties have zero neighbors (HI 15001/15003/15007, AK
-- 02063/02066) and simply never appear here.
--
-- ALL WINDOWS ARE PAST-ONLY, [day - w, day - 1], no same-day term -- matching
-- LAG_DAYS=21 in h5n1/features/migration_pressure.py. Changing a window means editing
-- the literals below and re-running:
--     REFRESH MATERIALIZED VIEW feature_spatial_lag;
--     REFRESH MATERIALIZED VIEW feature_county_day;      -- 009 reads this one
--
-- TUNABLE: trailing windows 7 / 21 / 60 days; days_since_* lookback 365 days.

-- The instance default work_mem is 4 MB, which makes every hash node here spill and
-- then trips temp_file_limit. SET LOCAL is transaction-scoped, so it reverts on commit
-- and never leaks into a teammate's session. Repeat this before any manual REFRESH.
SET LOCAL work_mem = '64MB';
SET LOCAL maintenance_work_mem = '128MB';

DROP MATERIALIZED VIEW IF EXISTS feature_county_day;   -- 009 rebuilds it on top of this
DROP MATERIALIZED VIEW IF EXISTS feature_spatial_lag;

CREATE MATERIALIZED VIEW feature_spatial_lag AS
WITH
-- Collapse flock_type: the DV is one row per county-day, not per flock type.
outbreak_day AS (
    SELECT fips, day, COUNT(*)::int AS outbreak_events
    FROM fact_h5n1_outbreak
    WHERE confirmed_flag
    GROUP BY fips, day
),

-- (target county, infected neighbor, day that neighbor was infected)
nbr_ob_pairs AS (
    SELECT a.fips, a.neighbor_fips, o.day, o.outbreak_events
    FROM county_adjacency a
    JOIN outbreak_day o ON o.fips = a.neighbor_fips
),

-- Neighbor poultry outbreaks. COUNT(DISTINCT neighbor_fips) is required for the
-- "how many neighbors are infected" variable: a neighbor hit on three days is
-- still one county.
nbr_ob AS (
    SELECT p.fips, d.day,
           COUNT(DISTINCT p.neighbor_fips) FILTER (WHERE p.day >= d.day - 7)::int  AS counties_7,
           COUNT(DISTINCT p.neighbor_fips) FILTER (WHERE p.day >= d.day - 21)::int AS counties_21,
           COUNT(DISTINCT p.neighbor_fips)::int                                    AS counties_60,
           COALESCE(SUM(p.outbreak_events) FILTER (WHERE p.day >= d.day - 7), 0)::int  AS events_7,
           COALESCE(SUM(p.outbreak_events) FILTER (WHERE p.day >= d.day - 21), 0)::int AS events_21,
           SUM(p.outbreak_events)::int                                                 AS events_60
    FROM nbr_ob_pairs p
    JOIN dim_date d ON p.day BETWEEN d.day - 60 AND d.day - 1
    GROUP BY p.fips, d.day
),

-- Neighbor wild-bird detections. These are infections too, and they are the migrating
-- vector feature_migration_pressure is built on, so they belong beside the poultry
-- count rather than being folded into it. Drop this CTE for strict poultry-only.
nbr_wild AS (
    SELECT a.fips, d.day,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day >= d.day - 7), 0)::int  AS det_7,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day >= d.day - 21), 0)::int AS det_21,
           SUM(w.detection_count)::int                                                 AS det_60
    FROM county_adjacency a
    JOIN fact_wild_bird_detection w ON w.fips = a.neighbor_fips
    JOIN dim_date d ON w.day BETWEEN d.day - 60 AND d.day - 1
    GROUP BY a.fips, d.day
),

-- Neighbor banded birds. EFFORT-BIASED: this counts where banders worked, not where
-- birds are. A zero means nobody banded nearby, not that no waterfowl were present.
nbr_band AS (
    SELECT a.fips, d.day,
           COALESCE(SUM(b.band_count_smoothed) FILTER (WHERE b.day >= d.day - 7), 0)  AS band_7,
           COALESCE(SUM(b.band_count_smoothed) FILTER (WHERE b.day >= d.day - 21), 0) AS band_21,
           SUM(b.band_count_smoothed)                                                 AS band_60
    FROM county_adjacency a
    JOIN fact_bird_density b ON b.fips = a.neighbor_fips
    JOIN dim_date d ON b.day BETWEEN d.day - 60 AND d.day - 1
    GROUP BY a.fips, d.day
),

-- Own-county trailing history.
own_wild AS (
    SELECT w.fips, d.day,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day >= d.day - 7), 0)::int  AS w7,
           COALESCE(SUM(w.detection_count) FILTER (WHERE w.day >= d.day - 21), 0)::int AS w21,
           SUM(w.detection_count)::int                                                 AS w60
    FROM fact_wild_bird_detection w
    JOIN dim_date d ON w.day BETWEEN d.day - 60 AND d.day - 1
    GROUP BY w.fips, d.day
),

own_band AS (
    SELECT b.fips, d.day,
           COALESCE(SUM(b.band_count_smoothed) FILTER (WHERE b.day >= d.day - 7), 0)  AS w7,
           COALESCE(SUM(b.band_count_smoothed) FILTER (WHERE b.day >= d.day - 21), 0) AS w21,
           SUM(b.band_count_smoothed)                                                 AS w60
    FROM fact_bird_density b
    JOIN dim_date d ON b.day BETWEEN d.day - 60 AND d.day - 1
    GROUP BY b.fips, d.day
),

own_outbreak AS (
    SELECT o.fips, d.day,
           COALESCE(SUM(o.outbreak_events) FILTER (WHERE o.day >= d.day - 7), 0)::int  AS w7,
           COALESCE(SUM(o.outbreak_events) FILTER (WHERE o.day >= d.day - 21), 0)::int AS w21,
           SUM(o.outbreak_events)::int                                                 AS w60
    FROM outbreak_day o
    JOIN dim_date d ON o.day BETWEEN d.day - 60 AND d.day - 1
    GROUP BY o.fips, d.day
),

-- Days since the last confirmed outbreak, capped at a 365-day lookback. NULL means
-- "none in the past year", which is a more usable feature than an unbounded age and
-- also bounds the row expansion here.
own_last_ob AS (
    SELECT o.fips, d.day, (d.day - MAX(o.day))::int AS days_since
    FROM outbreak_day o
    JOIN dim_date d ON d.day > o.day AND d.day <= o.day + 365
    GROUP BY o.fips, d.day
),

nbr_last_ob AS (
    SELECT p.fips, d.day, (d.day - MAX(p.day))::int AS days_since
    FROM (SELECT DISTINCT fips, day FROM nbr_ob_pairs) p
    JOIN dim_date d ON d.day > p.day AND d.day <= p.day + 365
    GROUP BY p.fips, d.day
),

-- The sparse key set: every county-day any of the above touches.
keys AS (
    SELECT fips, day FROM nbr_ob
    UNION SELECT fips, day FROM nbr_wild
    UNION SELECT fips, day FROM nbr_band
    UNION SELECT fips, day FROM own_wild
    UNION SELECT fips, day FROM own_band
    UNION SELECT fips, day FROM own_outbreak
    UNION SELECT fips, day FROM own_last_ob
    UNION SELECT fips, day FROM nbr_last_ob
)

SELECT
    k.fips,
    k.day,

    -- neighbor: poultry outbreaks
    COALESCE(nb.counties_7,  0) AS nbr_infected_counties_7d,
    COALESCE(nb.counties_21, 0) AS nbr_infected_counties_21d,
    COALESCE(nb.counties_60, 0) AS nbr_infected_counties_60d,
    COALESCE(nb.events_7,  0)   AS nbr_infections_7d,
    COALESCE(nb.events_21, 0)   AS nbr_infections_21d,
    COALESCE(nb.events_60, 0)   AS nbr_infections_60d,

    -- neighbor: wild-bird detections
    COALESCE(nw.det_7,  0)      AS nbr_wild_detections_7d,
    COALESCE(nw.det_21, 0)      AS nbr_wild_detections_21d,
    COALESCE(nw.det_60, 0)      AS nbr_wild_detections_60d,

    -- neighbor: banded birds
    COALESCE(nbd.band_7,  0)    AS nbr_band_count_7d,
    COALESCE(nbd.band_21, 0)    AS nbr_band_count_21d,
    COALESCE(nbd.band_60, 0)    AS nbr_band_count_60d,

    -- own county
    COALESCE(ow.w7,  0)         AS wild_detections_7d,
    COALESCE(ow.w21, 0)         AS wild_detections_21d,
    COALESCE(ow.w60, 0)         AS wild_detections_60d,
    COALESCE(ob.w7,  0)         AS band_count_7d,
    COALESCE(ob.w21, 0)         AS band_count_21d,
    COALESCE(ob.w60, 0)         AS band_count_60d,
    COALESCE(oo.w7,  0)         AS own_outbreaks_7d,
    COALESCE(oo.w21, 0)         AS own_outbreaks_21d,
    COALESCE(oo.w60, 0)         AS own_outbreaks_60d,

    -- recency (NULL = nothing in the trailing 365 days)
    olo.days_since              AS days_since_outbreak_county,
    nlo.days_since              AS days_since_outbreak_neighbor

FROM keys k
LEFT JOIN nbr_ob      nb  ON nb.fips  = k.fips AND nb.day  = k.day
LEFT JOIN nbr_wild    nw  ON nw.fips  = k.fips AND nw.day  = k.day
LEFT JOIN nbr_band    nbd ON nbd.fips = k.fips AND nbd.day = k.day
LEFT JOIN own_wild    ow  ON ow.fips  = k.fips AND ow.day  = k.day
LEFT JOIN own_band    ob  ON ob.fips  = k.fips AND ob.day  = k.day
LEFT JOIN own_outbreak oo ON oo.fips  = k.fips AND oo.day  = k.day
LEFT JOIN own_last_ob olo ON olo.fips = k.fips AND olo.day = k.day
LEFT JOIN nbr_last_ob nlo ON nlo.fips = k.fips AND nlo.day = k.day;

CREATE UNIQUE INDEX ux_feature_spatial_lag ON feature_spatial_lag (fips, day);
