-- 014_feature_gdelt.sql
-- feature_gdelt_county_day: GDELT news-mention features, one row per (fips, day), on the
-- same dim_county x dim_date grid and horizon as feature_county_day (011).
--
-- DELIBERATELY SEPARATE from feature_county_day. That keeps the frozen no-sentiment
-- baseline (model_county_day) untouched for the incremental-lift comparison, and adding
-- a source doesn't force the ~23-minute 008 -> 009 -> 011 rebuild. The GDELT model
-- variant joins the two on (fips, day): sql/builds/model_county_day_gdelt.sql.
--
-- Created WITH NO DATA, because the fact tables are empty when migrations run. Populate
-- or refresh it with:
--     uv run python sql/rebuild_model_county_day.py --variant gdelt
--
-- CONVENTIONS (same as 011; do not "simplify" them away)
--   * PAST-ONLY windows [day-w, day-1], w in {7, 21}. A published article is a reported
--     event, like a detection. The weather exception (inclusive of today) does NOT apply.
--   * Counts coalesce to 0, but only INSIDE GDELT coverage. Before coverage start + w, or
--     after coverage end + 1, every column is NULL: the source didn't exist there, which
--     is not the same as no news.
--   * Tone is a MEASUREMENT: NULL when there are no stories to measure.
--   * Rates are per 10,000 articles in the matching denominator; NULL when it is 0.
--   * State features: any type-2 OR type-3 location in the state, broadcast to every
--     county in the state. County features: type-3 (city) point-in-polygon ONLY.
--   * Tone and negative share are computed over distinct STORIES (syndication_key), so
--     one AP piece reprinted 200 times is weighted once.

SET LOCAL work_mem = '64MB';

DROP MATERIALIZED VIEW IF EXISTS feature_gdelt_county_day;

CREATE MATERIALIZED VIEW feature_gdelt_county_day AS
WITH
coverage AS (
    SELECT MIN(day) AS lo, MAX(day) AS hi
    FROM fact_gdelt_denominator
    WHERE state = 'US'
),

-- Same horizon as 011, so the two views line up row for row.
horizon AS (
    SELECT GREATEST(
        (SELECT MAX(day) FROM fact_h5n1_outbreak),
        (SELECT MAX(day) FROM fact_wild_bird_detection),
        (SELECT MAX(day) FROM fact_weather),
        (SELECT MAX(day) FROM fact_bird_density)
    ) AS max_day
),

-- ---- state layer --------------------------------------------------------------
art_state AS (
    SELECT DISTINCT l.state, a.day, a.gkg_record_id, a.syndication_key, a.tone
    FROM fact_gdelt_location l
    JOIN fact_gdelt_article  a USING (gkg_record_id)
    WHERE l.loc_type IN (2, 3) AND l.state IS NOT NULL
),
state_daily AS (
    SELECT state, day,
           COUNT(*)                        AS n_articles,
           COUNT(DISTINCT syndication_key) AS n_stories
    FROM art_state
    GROUP BY state, day
),
-- One row per (state, day, story). tone is a function of syndication_key, so the
-- DISTINCT doesn't split a story.
state_story_daily AS (
    SELECT state, day,
           SUM(tone)                          AS tone_sum,
           COUNT(tone)                        AS n_toned,
           COUNT(*) FILTER (WHERE tone < 0)   AS n_neg
    FROM (SELECT DISTINCT state, day, syndication_key, tone FROM art_state) s
    GROUP BY state, day
),
-- Dense state x day grid across coverage (+1 day so the day after coverage ends still
-- sees a full trailing window). ROWS-based windows need this density to mean days.
state_grid AS (
    SELECT s.state, d.day
    FROM (SELECT DISTINCT state FROM dim_county) s
    CROSS JOIN dim_date d
    CROSS JOIN coverage c
    WHERE d.day BETWEEN c.lo AND c.hi + 1
),
state_win AS (
    SELECT g.state, g.day,
           SUM(COALESCE(sd.n_articles, 0)) OVER w7  AS art_7d,
           SUM(COALESCE(sd.n_articles, 0)) OVER w21 AS art_21d,
           SUM(COALESCE(sd.n_stories,  0)) OVER w7  AS stories_7d,
           SUM(COALESCE(sd.n_stories,  0)) OVER w21 AS stories_21d,
           SUM(COALESCE(dn.n_articles, 0)) OVER w7  AS den_7d,
           SUM(COALESCE(dn.n_articles, 0)) OVER w21 AS den_21d,
           SUM(ss.tone_sum)                OVER w21 AS tone_sum_21d,   -- NULL-skipping
           SUM(COALESCE(ss.n_toned, 0))    OVER w21 AS n_toned_21d,
           SUM(COALESCE(ss.n_neg,   0))    OVER w21 AS n_neg_21d
    FROM state_grid g
    LEFT JOIN state_daily            sd ON sd.state = g.state AND sd.day = g.day
    LEFT JOIN state_story_daily      ss ON ss.state = g.state AND ss.day = g.day
    LEFT JOIN fact_gdelt_denominator dn ON dn.state = g.state AND dn.day = g.day
    WINDOW w7  AS (PARTITION BY g.state ORDER BY g.day ROWS BETWEEN 7  PRECEDING AND 1 PRECEDING),
           w21 AS (PARTITION BY g.state ORDER BY g.day ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING)
),

-- ---- national layer: a GKG analogue of the prior cohort's national g_count ------
us_daily AS (
    SELECT day, COUNT(*) AS n_articles FROM fact_gdelt_article GROUP BY day
),
us_win AS (
    SELECT d.day,
           SUM(COALESCE(u.n_articles,  0)) OVER w7 AS art_7d,
           SUM(COALESCE(dn.n_articles, 0)) OVER w7 AS den_7d
    FROM dim_date d
    CROSS JOIN coverage c
    LEFT JOIN us_daily u               ON u.day = d.day
    LEFT JOIN fact_gdelt_denominator dn ON dn.day = d.day AND dn.state = 'US'
    WHERE d.day BETWEEN c.lo AND c.hi + 1
    WINDOW w7 AS (ORDER BY d.day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
),

-- ---- county layer: type-3 point-in-polygon only (the CHECK in 013 enforces it) ---
county_daily AS (
    SELECT l.fips, a.day,
           COUNT(DISTINCT a.gkg_record_id)   AS n_articles,
           COUNT(DISTINCT a.syndication_key) AS n_stories
    FROM fact_gdelt_location l
    JOIN fact_gdelt_article  a USING (gkg_record_id)
    WHERE l.fips IS NOT NULL
    GROUP BY l.fips, a.day
),
-- Sparse fan-out, the same shape 011 uses for targets: each county-day with news
-- contributes to the 21 following days' windows. Cheaper than a dense 5M-row grid.
county_win AS (
    SELECT c.fips, d.day,
           SUM(c.n_articles) FILTER (WHERE c.day >= d.day - 7) AS art_7d,
           SUM(c.n_articles)                                   AS art_21d,
           SUM(c.n_stories)                                    AS stories_21d
    FROM county_daily c
    JOIN dim_date d ON d.day BETWEEN c.day + 1 AND c.day + 21
    GROUP BY c.fips, d.day
),

grid AS (
    SELECT c.fips, c.state, d.day
    FROM dim_county c
    CROSS JOIN dim_date d
    WHERE d.day <= (SELECT max_day FROM horizon)
)

SELECT
    g.fips,
    g.day,

    -- ---- state layer, broadcast to every county in the state ----
    CASE WHEN v7  THEN sw.art_7d      END AS gdelt_state_articles_7d,
    CASE WHEN v21 THEN sw.art_21d     END AS gdelt_state_articles_21d,
    CASE WHEN v7  THEN sw.stories_7d  END AS gdelt_state_stories_7d,
    CASE WHEN v21 THEN sw.stories_21d END AS gdelt_state_stories_21d,
    CASE WHEN v7  THEN sw.art_7d::double precision  * 10000 / NULLIF(sw.den_7d, 0)  END
        AS gdelt_state_per10k_7d,
    CASE WHEN v21 THEN sw.art_21d::double precision * 10000 / NULLIF(sw.den_21d, 0) END
        AS gdelt_state_per10k_21d,
    CASE WHEN v21 THEN sw.tone_sum_21d / NULLIF(sw.n_toned_21d, 0) END
        AS gdelt_state_tone_mean_21d,
    CASE WHEN v21 THEN sw.n_neg_21d::double precision / NULLIF(sw.n_toned_21d, 0) END
        AS gdelt_state_neg_share_21d,

    -- ---- county layer (city mentions only) ----
    CASE WHEN v7  THEN COALESCE(cw.art_7d, 0)      END AS gdelt_county_articles_7d,
    CASE WHEN v21 THEN COALESCE(cw.art_21d, 0)     END AS gdelt_county_articles_21d,
    CASE WHEN v21 THEN COALESCE(cw.stories_21d, 0) END AS gdelt_county_stories_21d,

    -- ---- national ----
    CASE WHEN v7 THEN uw.art_7d::double precision * 10000 / NULLIF(uw.den_7d, 0) END
        AS gdelt_us_per10k_7d

FROM grid g
CROSS JOIN coverage c
CROSS JOIN LATERAL (
    SELECT g.day BETWEEN c.lo + 7  AND c.hi + 1 AS v7,
           g.day BETWEEN c.lo + 21 AND c.hi + 1 AS v21
) cov
LEFT JOIN state_win  sw ON sw.state = g.state AND sw.day = g.day
LEFT JOIN county_win cw ON cw.fips  = g.fips  AND cw.day = g.day
LEFT JOIN us_win     uw ON uw.day   = g.day
WITH NO DATA;

-- Unique on the grain: the assertion that the grain is what we claim, and what
-- REFRESH ... CONCURRENTLY requires.
CREATE UNIQUE INDEX ux_feature_gdelt_county_day ON feature_gdelt_county_day (fips, day);

COMMENT ON MATERIALIZED VIEW feature_gdelt_county_day IS
    'GDELT GKG avian news-mention features per (fips, day). Past-only windows; NULL '
    'outside GDELT coverage; county columns from US-city geocodes only. Refresh via '
    'sql/rebuild_model_county_day.py --variant gdelt.';
