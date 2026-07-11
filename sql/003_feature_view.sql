-- 003_feature_view.sql
-- The model-ready join, made concrete. Materialized for speed. LEFT JOIN so a
-- missing source is NULL, not a dropped county-day. Refresh after loads with:
--   REFRESH MATERIALIZED VIEW feature_county_day;

DROP MATERIALIZED VIEW IF EXISTS feature_county_day;

CREATE MATERIALIZED VIEW feature_county_day AS
SELECT
    grid.fips,
    grid.day,
    COALESCE(o.confirmed_flag, FALSE) AS outbreak,
    o.birds_affected,
    w.temp_min_c,
    w.temp_max_c,
    w.precip_mm,
    w.humidity_pct,
    b.band_count_smoothed,
    s.post_count,
    s.sentiment_mean,
    s.sentiment_neg_share
FROM (SELECT c.fips, d.day FROM dim_county c CROSS JOIN dim_date d) AS grid
LEFT JOIN (
    -- collapse flock_type so the grain is one row per county-day for modeling
    SELECT fips, day,
           bool_or(confirmed_flag) AS confirmed_flag,
           SUM(birds_affected)     AS birds_affected
    FROM fact_h5n1_outbreak
    GROUP BY fips, day
) o  ON o.fips = grid.fips AND o.day = grid.day
LEFT JOIN fact_weather         w ON w.fips = grid.fips AND w.day = grid.day
LEFT JOIN fact_bird_density    b ON b.fips = grid.fips AND b.day = grid.day
LEFT JOIN fact_social_sentiment s ON s.fips = grid.fips AND s.day = grid.day;

CREATE INDEX IF NOT EXISTS ix_feature_county_day ON feature_county_day (fips, day);
