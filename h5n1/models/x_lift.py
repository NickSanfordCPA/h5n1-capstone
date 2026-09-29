"""Does X add out-of-time lift to the production baseline? The gdelt_lift.ipynb design, with
X state features in place of GDELT, at 7-, 14- and 30-day horizons.

EXPLORATORY. The prior cohort's X pull ends 2024-12-31, so the 2025+ test window the
baseline normally uses has no X data. Here: train 2022-07-01 .. (2024-01-01 - k days), test
2024-01-01 .. 2024-12-31. The train end is pulled back k days so no training outcome window
reaches into the test year. X posts start 2022-06-01; July 2022 is the first month with a
full trailing 30-day window.

BASELINE: Waree's production forest on model_county_day's 23 predictors, filled exactly as
gdelt_lift.prepare(): train-window median for weather + layer inventory, 4.0 for censored
recency. RF 300 trees / depth 6 / min_leaf 2, seeds 20260728..+2, seed-averaged.
Negatives hash-sampled at 5% on (fips, day) and weighted 1/0.05 in training; test is every
CONUS county-day.

TARGETS: outbreak in the county in (d, d+k], from fact_h5n1_outbreak. k=7 reproduces
model_county_day.target_outbreak_next_7d exactly (checked 2026-09-29: 343/343 on 2024-Q1).
k=14 and 30 are the same rule over longer windows. For each k the training set is that k's
positives plus the hash-sampled rows, so a row kept only because another horizon is positive
never enters as an unweighted negative.

X FEATURES (state, broadcast to every county in the state; past-only, windows end at d):
  x_firsthand_30d  count of screened, state-week-deduplicated firsthand illness reports in
                   the state over [d-29, d]
  x_sent_7d        mean TweetEval sentiment of the state's subset posts over [d-6, d];
                   filled with the training mean where the state had no posts
Media share is deliberately excluded: it measures news mix, and news (GDELT) was already null.

EVALUATION: PR-AUC on the test year, all positives and the new-onset slice
(log_own_outbreaks_60d == 0, as in gdelt_lift). Paired week-block bootstrap, 300 reps.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_lift          # exploratory (2024 test)

The pre-registered confirmatory run (docs/x_event_preregistration.md) on the full repull:

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_lift --train-start 2022-02-08 --test-start 2025-01-01 --test-end 2026-12-31 --boot 2000 --ci 99.1667 --tag confirmatory

Test rows are further limited per horizon to d + k <= the last loaded confirmation date.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from h5n1.db import get_engine

ROOT = pathlib.Path(__file__).resolve().parents[2]
XD = ROOT / "data" / "X_derived"
OUT = XD / "event_study"
TRAIN_START, TEST_START, TEST_END = "2022-07-01", "2024-01-01", "2024-12-31"
KS = (7, 14, 30)
NEG_HASH_BUCKETS, NEG_HASH_KEEP, NEG_SAMPLE_RATE = 10_000, 500, 0.05
RANDOM_STATE = 20260728
SEEDS = [RANDOM_STATE, RANDOM_STATE + 1, RANDOM_STATE + 2]
RF_PARAMS = dict(n_estimators=300, max_depth=6, min_samples_leaf=2, n_jobs=12)
N_BOOT = 300
KEYS = {"state", "fips", "day", "target_outbreak_next_7d", "target_complete_7d", "is_conus"}
X_COLS = ["x_firsthand_30d", "x_sent_7d"]


def load_panel() -> pd.DataFrame:
    targets = ",\n".join(
        f"EXISTS (SELECT 1 FROM fact_h5n1_outbreak o WHERE o.fips = m.fips "
        f"AND o.day BETWEEN m.day + 1 AND m.day + {k}) AS y{k}" for k in KS)
    q = f"""
    WITH t AS (
      SELECT m.*, {targets},
             mod(abs(hashtext(m.fips || m.day::text)), {NEG_HASH_BUCKETS}) < {NEG_HASH_KEEP} AS hashed
      FROM model_county_day m
      WHERE m.is_conus AND m.day BETWEEN '{TRAIN_START}' AND '{TEST_END}'
    )
    SELECT * FROM t WHERE day >= '{TEST_START}' OR hashed OR y7 OR y14 OR y30
    """
    df = pd.read_sql(q, get_engine())
    df["day"] = pd.to_datetime(df["day"])
    assert (df.y7 == df.target_outbreak_next_7d).all(), "recomputed 7d target disagrees"
    return df


def x_state_features() -> pd.DataFrame:
    """(state, day) -> x_firsthand_30d, x_sent_7d."""
    days = pd.date_range(pd.Timestamp(TRAIN_START) - pd.Timedelta(days=30), TEST_END)
    ev = pd.read_parquet(XD / "labels" / "illness_posts_screened.parquet")
    ev = ev[ev.exclude == ""].copy()
    ev["day"] = ev.ts.dt.tz_convert(None).dt.normalize()
    ev = ev.assign(week=ev.day.dt.to_period("W")).sort_values("day").drop_duplicates(["event_state", "week"])
    fh = (ev.groupby(["day", "event_state"]).size().unstack(fill_value=0)
          .reindex(days, fill_value=0).rolling(30, min_periods=1).sum())
    p = pd.read_parquet(XD / "topic_subset.parquet", columns=["_id", "state", "ts"]).merge(
        pd.read_parquet(XD / "sentiment_subset.parquet", columns=["_id", "sentiment"]), on="_id")
    p["day"] = p.ts.dt.tz_convert(None).dt.normalize()
    s = p.pivot_table(index="day", columns="state", values="sentiment", aggfunc="sum").reindex(days).fillna(0)
    n = p.pivot_table(index="day", columns="state", values="_id", aggfunc="count").reindex(days).fillna(0)
    sent = s.rolling(7, min_periods=1).sum() / n.rolling(7, min_periods=1).sum().where(lambda v: v > 0)
    out = pd.DataFrame({"x_firsthand_30d": fh.stack(), "x_sent_7d": sent.stack()}).reset_index()
    out.columns = ["day", "state", "x_firsthand_30d", "x_sent_7d"]
    return out


def prepare(df: pd.DataFrame, base: list[str], train: np.ndarray) -> pd.DataFrame:
    X = df[base + X_COLS].copy()
    for c in ["temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm", "log_layer_inventory"]:
        X[c] = X[c].fillna(X.loc[train, c].median())
    for c in ["days_since_outbreak_county", "days_since_outbreak_neighbor"]:
        X[c] = X[c].fillna(4.0)
    X["x_firsthand_30d"] = X.x_firsthand_30d.fillna(0)
    X["x_sent_7d"] = X.x_sent_7d.fillna(X.loc[train, "x_sent_7d"].mean())
    left = X.columns[X.isna().any()].tolist()
    assert not left, f"unexpected NULLs: {left}"
    return X


def paired_boot(y, pa, pb, wk, rng, n_boot=N_BOOT, ci=95.0):
    weeks = np.unique(wk)
    idx = {w: np.flatnonzero(wk == w) for w in weeks}
    d = []
    for _ in range(n_boot):
        ix = np.concatenate([idx[w] for w in rng.choice(weeks, size=len(weeks), replace=True)])
        if 0 < y[ix].sum() < len(ix):
            d.append(average_precision_score(y[ix], pa[ix]) - average_precision_score(y[ix], pb[ix]))
    d = np.array(d)
    tail = (100 - ci) / 2
    return np.percentile(d, tail), np.percentile(d, 100 - tail), (d > 0).mean()


def main(n_boot: int = N_BOOT, ci: float = 95.0, tag: str = "exploratory_2024") -> None:
    df = load_panel().merge(x_state_features(), on=["state", "day"], how="left")
    max_day = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(),
                          parse_dates=["d"]).d.iloc[0]
    base = [c for c in df.columns if c not in KEYS | set(X_COLS) | {"y7", "y14", "y30", "hashed"}]
    assert len(base) == 23, base
    rng = np.random.default_rng(RANDOM_STATE)
    rows, imps = [], []
    for k in KS:
        yk = df[f"y{k}"].to_numpy()
        tr_end = pd.Timestamp(TEST_START) - pd.Timedelta(days=k)
        train = ((df.day < tr_end) & (df.hashed | df[f"y{k}"])).to_numpy()
        # outcome window must be fully loaded: d + k <= last confirmation date (cf. target_complete_7d)
        test = ((df.day >= TEST_START) & (df.day <= TEST_END)
                & (df.day + pd.Timedelta(days=k) <= max_day)).to_numpy()
        X = prepare(df, base, train)
        w = np.where(yk[train] == 1, 1.0, 1.0 / NEG_SAMPLE_RATE)
        y_te = yk[test]
        preds = {}
        for arm, cols in {"A baseline": base, "B +X": base + X_COLS}.items():
            ps = []
            for seed in SEEDS:
                rf = RandomForestClassifier(random_state=seed, **RF_PARAMS)
                rf.fit(X.loc[train, cols], yk[train], sample_weight=w)
                ps.append(rf.predict_proba(X.loc[test, cols])[:, 1])
                if seed == SEEDS[0] and arm == "B +X":
                    imp = pd.Series(rf.feature_importances_, index=cols)
                    imps.append({"k": k, **imp[X_COLS].round(5).to_dict(),
                                 "x_share": round(imp[X_COLS].sum(), 5),
                                 "x_rank": [int((imp > imp[c]).sum()) + 1 for c in X_COLS]})
            preds[arm] = np.mean(ps, axis=0)
        wk = df.loc[test, "day"].dt.to_period("W").astype(str).to_numpy()
        onset = df.loc[test, "log_own_outbreaks_60d"].to_numpy() == 0
        for sl, m in {"all": np.ones_like(onset), "new_onset": onset}.items():
            yy = y_te[m]
            pa, pb = preds["B +X"][m], preds["A baseline"][m]
            lo, hi, pgt = paired_boot(yy, pa, pb, wk[m], rng, n_boot, ci)
            ap_a, ap_b = average_precision_score(yy, pb), average_precision_score(yy, pa)
            rows.append({"k": k, "slice": sl, "test_rows": int(m.sum()), "positives": int(yy.sum()),
                         "base_rate": yy.mean(), "pr_auc_A": ap_a, "pr_auc_B": ap_b, "lift_A_x": ap_a / yy.mean(),
                         "delta": ap_b - ap_a, "d_lo": lo, "d_hi": hi, "P(d>0)": pgt,
                         "roc_A": roc_auc_score(yy, pb), "roc_B": roc_auc_score(yy, pa)})
        print(f"k={k} done: train rows {train.sum():,} ({int(yk[train].sum())} pos), test {test.sum():,}", flush=True)
    res = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    res.assign(ci_level=ci, n_boot=n_boot, train_start=TRAIN_START, test_start=TEST_START,
               test_end=TEST_END, outbreak_max_day=max_day.date()).to_csv(
        OUT / f"x_lift_production_baseline_{tag}.csv", index=False)
    pd.set_option("display.width", 220)
    print(res.round(4).to_string(index=False))
    print("\nX feature impurity importance in arm B (seed 1), rank among 25:")
    print(pd.DataFrame(imps).to_string(index=False))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--train-start", default=TRAIN_START)
    ap.add_argument("--test-start", default=TEST_START)
    ap.add_argument("--test-end", default=TEST_END)
    ap.add_argument("--boot", type=int, default=N_BOOT)
    ap.add_argument("--ci", type=float, default=95.0)
    ap.add_argument("--tag", default="exploratory_2024")
    a = ap.parse_args()
    TRAIN_START, TEST_START, TEST_END = a.train_start, a.test_start, a.test_end  # module globals
    main(a.boot, a.ci, a.tag)
