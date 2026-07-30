-- 010_poultry_exposure.sql
-- Poultry EXPOSURE at county grain: how much poultry is at risk in each county.
--
-- WHY THIS EXISTS. Every other predictor in this schema describes infection PRESSURE
-- (detections nearby, migration geometry, weather). None of them describe how much
-- there is to infect. Without an exposure term the model's single strongest learnable
-- pattern is simply "where the poultry farms are", and human population -- the only
-- denominator dim_county carried -- is actively misleading for a poultry DV: a county
-- with three commercial layer complexes and 5,000 residents is a high-risk county.
--
-- SOURCE: USDA NASS Census of Agriculture 2022, county level, from the keyless bulk
-- Quick Stats file (see h5n1/sources/nass_poultry.py and manifests/nass_poultry.json).
-- The Census runs every 5 years, so 2022 is a STATIC single-vintage attribute for a
-- 2020-2026 study period -- the same decision made for dim_county.population (2020
-- Decennial). census_year is in the PK anyway so 2017 or 2027 can be added later
-- without a migration.
--
-- ---------------------------------------------------------------------------
-- THE SUPPRESSION SEMANTICS ARE THE WHOLE POINT OF THIS TABLE -- READ THIS
-- ---------------------------------------------------------------------------
-- NASS withholds county values that would disclose an individual operation, printing
-- "(D)". Measured on the actual 2022 file, the split is stark:
--     OPERATIONS counts:  0% suppressed, every measure
--     INVENTORY values:   11.4% (layers), 22.2% (broilers), 28.3% (turkeys),
--                         15.9% (ducks), 28.1% (poultry sales $)
-- So the OPERATIONS columns are the RELIABLE exposure measure and the INVENTORY
-- columns are the richer but holey one. Prefer operations for anything that must be
-- complete; use inventory where magnitude matters and accept the missingness.
--
-- Three distinct states are encoded, and conflating them will bias the model:
--   operations = 0  AND inventory = 0     -> county genuinely has none of that commodity
--                                            (93 of 3,143 counties have no poultry at all;
--                                             they are densified in, not left absent)
--   operations > 0  AND inventory IS NULL -> county HAS the commodity, amount WITHHELD.
--                                            This is MISSING, not zero. Never coalesce it.
--   operations > 0  AND inventory > 0     -> fully reported
-- No separate _suppressed flags are stored because suppression is exactly derivable as
-- (operations > 0 AND inventory IS NULL).
--
-- COMMODITY CHOICE. Layers, broilers and turkeys are the three commercial commodities
-- APHIS tracks and the ones that drive US H5N1 depopulations; ducks are included as the
-- domestic-waterfowl bridge host. For BROILERS the measure is SALES IN HEAD, not
-- inventory: broilers are grown in ~6-week cycles, so a census-day headcount badly
-- understates annual throughput. For layers and turkeys, inventory is the right measure.

CREATE TABLE IF NOT EXISTS county_poultry_census (
    fips                CHAR(5)  NOT NULL REFERENCES dim_county,
    census_year         SMALLINT NOT NULL,

    -- broadest exposure; 0% suppressed, so this is the column to lean on
    poultry_operations  INTEGER,          -- POULTRY TOTALS - OPERATIONS WITH INVENTORY

    layer_operations    INTEGER,          -- CHICKENS, LAYERS - OPERATIONS WITH INVENTORY
    layer_inventory     BIGINT,           -- CHICKENS, LAYERS - INVENTORY          (NULL = withheld)

    broiler_operations  INTEGER,          -- CHICKENS, BROILERS - OPERATIONS WITH SALES
    broiler_sales_head  BIGINT,           -- CHICKENS, BROILERS - SALES, MEASURED IN HEAD

    turkey_operations   INTEGER,          -- TURKEYS - OPERATIONS WITH INVENTORY
    turkey_inventory    BIGINT,           -- TURKEYS - INVENTORY                   (NULL = withheld)

    duck_operations     INTEGER,          -- DUCKS - OPERATIONS WITH INVENTORY
    duck_inventory      BIGINT,           -- DUCKS - INVENTORY                     (NULL = withheld)

    poultry_sales_usd   BIGINT,           -- POULTRY TOTALS, INCL EGGS - SALES, MEASURED IN $

    PRIMARY KEY (fips, census_year)
);
