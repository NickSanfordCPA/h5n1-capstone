-- 004_county_adjacency.sql
-- County contiguity (queen adjacency): each county paired with the counties it
-- shares a border with. Symmetric — both directions are stored, so "neighbors of
-- X" is a single-column filter (WHERE fips = X). Self-pairs are excluded.
--
-- Both endpoints FK dim_county. That's the guardrail: adjacency can only ever
-- reference counties that exist in the master, which keeps the two vintage-locked.
-- If a FIPS vintage mismatch is ever reintroduced, the load fails loudly here
-- rather than leaving silent gaps in the spatial structure.
CREATE TABLE IF NOT EXISTS county_adjacency (
    fips          CHAR(5) NOT NULL REFERENCES dim_county,
    neighbor_fips CHAR(5) NOT NULL REFERENCES dim_county,
    PRIMARY KEY (fips, neighbor_fips)
);

-- The PK already indexes (fips, ...); add the reverse for neighbor-keyed lookups.
CREATE INDEX IF NOT EXISTS ix_county_adjacency_neighbor
    ON county_adjacency (neighbor_fips);
