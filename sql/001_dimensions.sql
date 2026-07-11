-- 001_dimensions.sql
-- Conformed dimensions. Every fact table FKs to these two. Build first.

CREATE TABLE IF NOT EXISTS dim_county (
    fips           CHAR(5) PRIMARY KEY,        -- state(2) + county(3); the geo backbone
    state          TEXT NOT NULL,
    county_name    TEXT NOT NULL,
    centroid_lat   DOUBLE PRECISION,
    centroid_lon   DOUBLE PRECISION,
    population     INTEGER,
    land_area_sqmi DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS dim_date (
    day          DATE PRIMARY KEY,
    year         SMALLINT NOT NULL,
    month        SMALLINT NOT NULL,
    iso_week     SMALLINT NOT NULL,
    day_of_year  SMALLINT NOT NULL,
    season       TEXT,
    is_us_holiday BOOLEAN NOT NULL DEFAULT FALSE
);
