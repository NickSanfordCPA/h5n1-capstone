"""Clean transforms — raw snapshot -> conformed fact tables at county x day grain.

Reads from the staging mirror of the raw pull, applies crosswalks (e.g. lat/lon ->
FIPS) and type/units normalization, and loads a fact_* table. Deterministic and
re-runnable: a cleaning bug is fixed here and replayed without re-pulling the source.
"""
