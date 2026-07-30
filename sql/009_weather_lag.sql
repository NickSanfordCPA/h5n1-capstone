-- 009_weather_lag.sql
-- Trailing 7-day weather aggregates, plus removal of the dead humidity_pct column.
--
-- WINDOW IS [day - 6, day], INCLUSIVE OF THE CURRENT DAY -- deliberately different
-- from every other trailing window in this schema, which are past-only [day - w, day - 1].
-- The reason is that weather is CONTEMPORANEOUSLY OBSERVED: today's temperature and
-- rainfall are known today, so including day D leaks nothing. The past-only convention
-- exists for REPORTED events (outbreak confirmations, wild-bird detections), where the
-- same-day value is not actually available at prediction time. Do not "make this
-- consistent" with the others without understanding that distinction.
--
-- WHY A SEPARATE MATERIALIZED VIEW rather than folding this into feature_spatial_lag:
-- weather is DENSE (7.4M county-days) while feature_spatial_lag is deliberately SPARSE
-- (3.4M rows, absent = zero). Merging them would densify the sparse table for no gain.
-- It is also computed with a window function over an ordered scan rather than a range
-- self-join, which is far cheaper -- one pass instead of a 7x row expansion.
--
-- COMPLETENESS COUNTERS. A 7-day precipitation total built from 3 reporting days is not
-- comparable to one built from 7, and SUM() silently ignores NULLs, so the naive column
-- would quietly mix the two. precip_days_7d / temp_days_7d expose how many days actually
-- contributed. Filter or normalize on them; do not assume 7.
--
-- humidity_pct: DROPPED. It was entirely NULL -- GHCN-Daily carries no relative humidity,
-- and the nearest-station method in h5n1/sources/noaa.py has nothing to populate it from.
-- Getting real humidity means a reanalysis product (ERA5), which is a separate source.
-- h5n1/sources/noaa.py is updated to stop inserting it.
--
-- Refresh after any weather load, BEFORE refreshing feature_county_day:
--     SET work_mem = '64MB';
--     REFRESH MATERIALIZED VIEW feature_weather_lag;

SET LOCAL work_mem = '64MB';
SET LOCAL maintenance_work_mem = '128MB';

-- feature_county_day (011) selects from fact_weather, so drop it first to free the
-- column dependency; 011 rebuilds it.
DROP MATERIALIZED VIEW IF EXISTS feature_county_day;

ALTER TABLE fact_weather DROP COLUMN IF EXISTS humidity_pct;

DROP MATERIALIZED VIEW IF EXISTS feature_weather_lag;

CREATE MATERIALIZED VIEW feature_weather_lag AS
SELECT
    fips,
    day,
    SUM(precip_mm)   OVER w AS precip_7d_mm,
    AVG(temp_min_c)  OVER w AS temp_min_7d_c,
    AVG(temp_max_c)  OVER w AS temp_max_7d_c,
    COUNT(precip_mm) OVER w AS precip_days_7d,
    COUNT(temp_max_c) OVER w AS temp_days_7d
FROM fact_weather
WINDOW w AS (
    PARTITION BY fips
    ORDER BY day
    RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
);

CREATE UNIQUE INDEX ux_feature_weather_lag ON feature_weather_lag (fips, day);
