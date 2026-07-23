-- 006_migration_geometry.sql
-- Static spatial structure for directional (migration-driven) spatial-lag features.
-- Both tables are derived from dim_county centroids + a flyway assignment; they hold
-- geometry only, so the sector half-width and radius stay query-time parameters.

-- Per-county migration axis: flyway + great-circle bearings toward that flyway's
-- breeding (north) and wintering (south) anchors. The "toward breeding" bearing is
-- the fall-arrival / upstream direction; "toward wintering" is the spring-arrival
-- direction. Curves by region because the four flyways anchor differently.
CREATE TABLE IF NOT EXISTS county_migration_axis (
    fips                  CHAR(5) PRIMARY KEY REFERENCES dim_county,
    flyway                TEXT NOT NULL,
    breeding_bearing_deg  DOUBLE PRECISION NOT NULL,   -- bearing toward breeding grounds (fall source)
    wintering_bearing_deg DOUBLE PRECISION NOT NULL    -- bearing toward wintering grounds (spring source)
);

-- Directed county pairs within a max radius, with great-circle distance and bearing
-- from `fips` to `neighbor_fips`. Both directions are stored (bearing A->B != B->A).
-- Stored radius is wider than the operating radius so R can be tuned down (and the
-- sector half-width applied) at feature-build time without regenerating this table.
CREATE TABLE IF NOT EXISTS county_proximity (
    fips          CHAR(5) NOT NULL REFERENCES dim_county,
    neighbor_fips CHAR(5) NOT NULL REFERENCES dim_county,
    distance_km   DOUBLE PRECISION NOT NULL,
    bearing_deg   DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (fips, neighbor_fips)
);

CREATE INDEX IF NOT EXISTS ix_county_proximity_neighbor ON county_proximity (neighbor_fips);
