"""dim_date generator — the conformed time backbone.

Pure calendar computation over a date span; no external source. Every fact_*
table foreign-keys dim_date, so its span must cover every day any source can
report an observation for. Extending it later is safe: the seed upserts by day.
"""
from __future__ import annotations

from datetime import date, timedelta

import holidays
import pandas as pd

# Meteorological seasons (Northern Hemisphere): whole-month buckets.
_SEASON = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}


def date_dimension(start: date, end: date) -> pd.DataFrame:
    """One dim_date row per day in [start, end], inclusive."""
    us_holidays = holidays.US(years=range(start.year, end.year + 1))
    rows = []
    d = start
    while d <= end:
        iso = d.isocalendar()
        rows.append(
            {
                "day": d,
                "year": d.year,
                "month": d.month,
                "iso_week": iso.week,
                "day_of_year": d.timetuple().tm_yday,
                "season": _SEASON[d.month],
                "is_us_holiday": d in us_holidays,
            }
        )
        d += timedelta(days=1)
    return pd.DataFrame(rows)
