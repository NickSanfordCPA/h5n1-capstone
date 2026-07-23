-- 007_migration_pressure.sql
-- First-cut directional migration-pressure feature at the canonical county x day grain.
-- For each county-day, the distance-weighted, trailing-window sum of WILD-BIRD HPAI
-- detections in the sector toward the breeding grounds (fall-arrival source) and toward
-- the wintering grounds (spring-arrival source). Wild birds are the migrating vector, so
-- they — not poultry — carry infection into a county. Stored sparse: absent county-days
-- are zero. Parameters (sector width, radius, decay, lag window) are recorded in the
-- builder h5n1/features/migration_pressure.py and are provisional/tunable.
CREATE TABLE IF NOT EXISTS feature_migration_pressure (
    fips                   CHAR(5) NOT NULL REFERENCES dim_county,
    day                    DATE    NOT NULL REFERENCES dim_date,
    breeding_side_pressure DOUBLE PRECISION,   -- upstream in fall (toward breeding grounds)
    wintering_side_pressure DOUBLE PRECISION,  -- upstream in spring (toward wintering grounds)
    PRIMARY KEY (fips, day)
);
