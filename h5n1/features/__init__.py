"""Feature builder — assembles feature_county_day from the fact_* tables.

LEFT JOINs every source on (fips, day) so a missing source is NULL, not a dropped
row. This materialized table is what the models consume.
"""
