"""State-week features that are not in the baseline: WHISPers events (protocol 10) and GDELT
avian-influenza news volume (protocol 9). Local database only; no outcome data.

    uv run python -m h5n1.journal.extras build

Writes
    journal.gdelt_state_day     stories per state and day (for the first-public-report proxy)
    journal.state_week_extra    one row per state and week ending Sunday, the model_state_week grid

Windows follow the baseline's convention: a w-day window at Sunday d covers (d - w, d].

WHISPers. US avian morbidity or mortality events of ANY cause, dated by known_date_lb (a lower
bound on when the record existed), counted once per state the event touches. Own state and the
sum over neighbouring states, 7 / 21 / 60 days.

GDELT. A story is one syndication key (wire copies of one article count once) and is credited
to every US state it names. gdelt_n_7d is the state's story count; gdelt_share_7d is its share
of the 49-state total. GDELT has no records from 2025-06-15 to 2025-07-01; any 7-day window
touching the outage is NULL, never zero.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import assert_local, local_engine

OUTAGE = (pd.Timestamp("2025-06-15"), pd.Timestamp("2025-07-01"))
WINDOWS = (7, 21, 60)


def _trailing(daily: pd.DataFrame, w: int) -> pd.DataFrame:
    """daily: day x state counts on a complete daily index. Sum over (d - w, d]."""
    return daily.rolling(w, min_periods=1).sum()


def build() -> None:
    eng = local_engine()
    assert_local(eng)
    with eng.connect() as cn:
        grid = pd.read_sql(text("SELECT state, day FROM model_state_week"), cn, parse_dates=["day"])
        nbr = pd.read_sql(text(
            "SELECT DISTINCT a.state AS state, b.state AS nbr FROM county_adjacency x "
            "JOIN dim_county a ON a.fips = x.fips JOIN dim_county b ON b.fips = x.neighbor_fips "
            "WHERE a.state <> b.state"), cn)
        wh = pd.read_sql(text(
            "SELECT DISTINCT e.event_id, c.state, e.known_date_lb AS day FROM whispers_event e "
            "JOIN whispers_event_county ec USING (event_id) JOIN dim_county c ON c.fips = ec.fips "
            "WHERE e.us AND e.avian AND e.event_type = 'Mortality/Morbidity'"), cn, parse_dates=["day"])
        gd = pd.read_sql(text(
            "SELECT a.day, l.state, count(DISTINCT coalesce(a.syndication_key, a.gkg_record_id)) AS n_stories "
            "FROM fact_gdelt_article a JOIN fact_gdelt_location l USING (gkg_record_id) "
            "WHERE l.state IS NOT NULL GROUP BY 1, 2"), cn, parse_dates=["day"])
        gd_last = pd.read_sql(text("SELECT max(day) d FROM fact_gdelt_article"), cn, parse_dates=["d"]).d[0]
    states = sorted(grid.state.unique())
    sundays = pd.DatetimeIndex(sorted(grid.day.unique()))
    days = pd.date_range(sundays.min() - pd.Timedelta(days=70), sundays.max())
    nbr = nbr[nbr.state.isin(states) & nbr.nbr.isin(states)]
    A = pd.crosstab(nbr.state, nbr.nbr).reindex(index=states, columns=states, fill_value=0)

    def daily(d: pd.DataFrame, value: str | None) -> pd.DataFrame:
        d = d[d.state.isin(states) & d.day.between(days.min(), days.max())]
        s = d.groupby(["day", "state"]).size() if value is None else d.groupby(["day", "state"])[value].sum()
        return s.unstack(fill_value=0).reindex(index=days, columns=states, fill_value=0).astype(float)

    out = {}
    w_daily = daily(wh, None)
    for w in WINDOWS:
        own = _trailing(w_daily, w)
        out[f"whisp_{w}d"] = own
        out[f"nbr_whisp_{w}d"] = own @ A.T.astype(float)     # sum over each state's neighbours
    g_daily = daily(gd, "n_stories")
    g7 = _trailing(g_daily, 7)
    # NULL where the 7-day window touches the outage or runs past the last GDELT day
    win_start = pd.Series(days - pd.Timedelta(days=6), index=days)
    bad = ((win_start <= OUTAGE[1]) & (pd.Series(days, index=days) >= OUTAGE[0])) | (days > gd_last)
    g7[bad.to_numpy()] = np.nan
    out["gdelt_n_7d"] = g7
    out["gdelt_share_7d"] = g7.div(g7.sum(axis=1).where(lambda v: v > 0), axis=0)

    tab = pd.concat({k: v.reindex(sundays).stack(future_stack=True) for k, v in out.items()}, axis=1)
    tab.index.names = ["day", "state"]
    tab = tab.reset_index()
    for c in [c for c in tab.columns if "whisp" in c] + ["gdelt_n_7d"]:
        tab[f"log_{c}"] = np.log1p(tab[c])
    tab = grid.merge(tab, on=["state", "day"], how="left", validate="1:1")
    assert tab[[c for c in tab.columns if "whisp" in c]].notna().all().all()
    gsd = gd[gd.state.isin(states)].copy()

    with eng.begin() as cn:
        for name, df in (("state_week_extra", tab), ("gdelt_state_day", gsd)):
            cn.execute(text(f"DROP TABLE IF EXISTS journal.{name}"))
            df.assign(day=df.day.dt.date).to_sql(name, cn, schema="journal", index=False,
                                                 chunksize=2000, method="multi")
            cn.execute(text(f"ALTER TABLE journal.{name} ADD PRIMARY KEY (state, day)"))
    print(f"journal.state_week_extra: {len(tab):,} rows; journal.gdelt_state_day: {len(gsd):,} rows")
    print(f"WHISPers avian events used: {wh.event_id.nunique():,} (event-states {len(wh):,}); "
          f"GDELT last day {gd_last.date()}; state-weeks with NULL GDELT: {int(tab.gdelt_n_7d.isna().sum())}")
    print(tab.drop(columns=["state", "day"]).describe().T[["mean", "max"]].round(3).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build"])
    ap.parse_args()
    build()
