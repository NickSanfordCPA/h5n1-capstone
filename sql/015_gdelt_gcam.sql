-- 015_gdelt_gcam.sql
-- GCAM affect counts for the GDELT avian articles in fact_gdelt_article, loaded by
-- `h5n1.sources.gdelt load-gcam`. Only the five dimensions PRE-REGISTERED in
-- docs/gcam_preregistration.md are stored; the full GCAM string stays in the BigQuery
-- staging table for reproducibility only.
--
-- Raw COUNTS, not rates: rate = count / gcam_wc is computed downstream. gcam_wc NULL
-- means GDELT had nothing to score (no GCAM, or wc <= 0), and then every count is NULL
-- too -- unmeasured, not zero. With gcam_wc present, a 0 count is a real zero (GCAM
-- omits dimensions with no match).

CREATE TABLE IF NOT EXISTS fact_gdelt_gcam (
    gkg_record_id    TEXT PRIMARY KEY REFERENCES fact_gdelt_article ON DELETE CASCADE,
    gcam_wc          INTEGER,     -- GCAM total word count
    gcam_anxiety     INTEGER,     -- c5.33  LIWC Anxiety
    gcam_tentative   INTEGER,     -- c5.26  LIWC Tentative
    gcam_death       INTEGER,     -- c5.2   LIWC Death
    gcam_uncertainty INTEGER,     -- c6.6   Loughran-McDonald Uncertainty
    gcam_negative    INTEGER,     -- c3.1   Lexicoder Sentiment NEGATIVE
    CONSTRAINT ck_gcam_measured CHECK (
        (gcam_wc IS NULL AND gcam_anxiety IS NULL AND gcam_tentative IS NULL
             AND gcam_death IS NULL AND gcam_uncertainty IS NULL AND gcam_negative IS NULL)
        OR gcam_wc > 0)
);
