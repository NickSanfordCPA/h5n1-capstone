"""Waree's county forest (C3, 35 features) rolled up to state-weeks on 2025+, scored against the
state baseline (S3) and the social-media arms (S4) on the same rows. Exploratory.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.county_rollup_2025

Question (Nick, 2026-11-12): the county model is what would be deployed. If, summarised to
state-weeks, it ranks outbreaks at least as well as the state model with social media, social
media is skipped. Only if S4 beats the roll-up is there a choice to make.

Roll-up: P(any county in the state has an outbreak in the next k days) = 1 - prod(1 - p) over the
state's CONUS counties on the forecast Sunday, as in x_baselines.compare(). C3 is trained through
2024-12-31 minus k exactly as in x_baselines.county_report(). The county surface's predictors stop
at day-1 while the state-week ones include day, a one-day handicap for the roll-up.

Comparisons, each with a paired week-block bootstrap (2,000 replicates):
  all      every 2025+ state-week: S3 vs roll-up (S3 refit here on all state-weeks)
  x        X Track A rows (final_oof_primary_k*): roll-up vs S3 and vs S4
  meta     Meta Track A rows (explore2025_oof_primary_k*): roll-up vs S3 and vs S4
Output: data/X_derived/x_utility/county_rollup_2025.csv and county_rollup_2025_preds.csv.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from h5n1.db import get_engine
from h5n1.models.x_baselines import (KS, OUT, ROOT, S3, TEST_START, _county_fit, county_columns, fit_predict,
                                     load_county, load_state_week, prepare_county, prepare_state)
from h5n1.models.x_lift import paired_boot

N_BOOT = 2000
SEED = 20261112
META_OUT = ROOT / "data" / "Meta_derived" / "meta_utility"
SOURCES = {"x": OUT / "final_oof_primary_k{k}.csv", "meta": META_OUT / "explore2025_oof_primary_k{k}.csv"}


def rollup(max_day: pd.Timestamp) -> pd.DataFrame:
    df = load_county(max_day)
    cols = county_columns(df)
    out = []
    for k in KS:
        yk = df[f"y{k}"].to_numpy().astype(float)
        train = (df.day + pd.Timedelta(days=k) < TEST_START).to_numpy()
        test = ((df.day >= TEST_START) & (df.day.dt.dayofweek == 6)
                & (df.day + pd.Timedelta(days=k) <= max_day)).to_numpy()
        X = prepare_county(df, cols, train).to_numpy()
        p = _county_fit(df, X, yk, train, test)
        pc = df.loc[test, ["state", "day"]].assign(p=p)
        r = (1 - (1 - pc.p).groupby([pc.state, pc.day]).prod()).rename("rollup").reset_index()
        out.append(r.assign(k=k))
        print(f"roll-up k={k}: {len(r)} state-weeks", flush=True)
    return pd.concat(out)


def s3_all(max_day: pd.Timestamp) -> pd.DataFrame:
    sw = load_state_week(max_day)
    out = []
    for k in KS:
        known = sw[f"y{k}"].notna()
        train = ((sw.day + pd.Timedelta(days=k) < TEST_START) & known).to_numpy()
        test = ((sw.day >= TEST_START) & known).to_numpy()
        X = prepare_state(sw, S3, train)
        p = fit_predict(X[train], sw.loc[train, f"y{k}"], X[test])
        out.append(sw.loc[test, ["state", "day", "state_new_onset", f"y{k}"]]
                   .rename(columns={f"y{k}": "y"}).assign(S3=p, k=k))
    return pd.concat(out)


def score(o: pd.DataFrame, rows: str, k: int, pairs: list[tuple[str, str]], rng) -> list[dict]:
    wk = o.day.dt.to_period("W").astype(str).to_numpy()
    res = []
    for sl in ("all", "state_new_onset"):
        m = np.ones(len(o), bool) if sl == "all" else o.state_new_onset.astype(bool).to_numpy()
        y = o.y.to_numpy()[m]
        aps = {a: average_precision_score(y, o[a].to_numpy()[m]) for a in {a for p in pairs for a in p}}
        for new, ref in pairs:
            lo, hi, pgt = paired_boot(y, o[new].to_numpy()[m], o[ref].to_numpy()[m], wk[m], rng, N_BOOT)
            res.append({"rows_from": rows, "k": k, "slice": sl, "comparison": f"{new} - {ref}", "rows": int(m.sum()),
                        "positives": int(y.sum()), "base_rate": y.mean(), "pr_auc_new": aps[new],
                        "pr_auc_ref": aps[ref], "delta": aps[new] - aps[ref], "d_lo": lo, "d_hi": hi, "P(d>0)": pgt})
    return res


def main() -> None:
    max_day = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
    ru = rollup(max_day)
    s3 = s3_all(max_day)
    preds = s3.merge(ru, on=["state", "day", "k"], how="left")
    preds.to_csv(OUT / "county_rollup_2025_preds.csv", index=False)
    rng = np.random.default_rng(SEED)
    rows = []
    for k in KS:
        a = preds[preds.k == k]
        assert a.rollup.notna().all(), "state-weeks without a roll-up"
        rows += score(a, "all", k, [("S3", "rollup")], rng)
        for src, path in SOURCES.items():
            o = pd.read_csv(str(path).format(k=k), parse_dates=["day"])
            o = o.merge(ru[ru.k == k][["state", "day", "rollup"]], on=["state", "day"], how="left")
            assert o.rollup.notna().all(), f"{src} k={k}: rows without a roll-up"
            rows += score(o, src, k, [("S3", "rollup"), ("S4", "rollup")], rng)
        print(f"k={k} scored", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "county_rollup_2025.csv", index=False)
    pd.set_option("display.width", 220)
    print(res.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
