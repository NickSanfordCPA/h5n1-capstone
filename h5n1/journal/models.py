"""Shared modelling core for the journal rebuild (protocol sections 3, 4.3 and 8.5).

Everything reads the LOCAL journal database. State-week grain, week ending Sunday.

    KS            horizons 7, 14, 28 days
    DEV           Sundays 2022-08-07 .. 2024-12-31 minus k   (training / search)
    TEST          Sundays from 2025-01-05 with a complete outcome window
    FOLDS         three forward-chaining folds inside the development period
    SEEDS         five; the reported score uses the mean prediction of the five forests

Functions here fit and score models against outcomes. They exist only after the protocol
commit (ef852d2).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss
from sqlalchemy import text

from h5n1.journal.localdb import DATA_DIR, local_engine

OUT = DATA_DIR / "models"
KS = (7, 14, 28)
START = pd.Timestamp("2022-08-07")
DEV_END = pd.Timestamp("2024-12-31")
TEST_FIRST = pd.Timestamp("2025-01-05")
FOLDS = {  # name: (train through, validate from, validate through)
    "F1": ("2023-09-30", "2023-10-01", "2024-03-31"),
    "F2": ("2024-03-31", "2024-04-01", "2024-09-30"),
    "F3": ("2024-09-30", "2024-10-01", "2024-12-31"),
}
SEEDS = [20261117, 20261118, 20261119, 20261120, 20261121]
N_BOOT = 2000
BOOT_SEED = 20261117
WAREE = {"n_estimators": 300, "max_depth": 6, "min_samples_leaf": 2, "max_features": "sqrt"}
GRID = [{"n_estimators": 500, "max_depth": d, "min_samples_leaf": leaf, "max_features": mf}
        for d in (4, 6, 8, 12) for leaf in (2, 10, 50) for mf in ("sqrt", 1 / 3)] + [WAREE]

S0 = ["doy_sin", "doy_cos", "log_poultry_operations", "log_broiler_operations",
      "log_turkey_operations", "log_duck_operations", "log_population",
      "log_known_layer_inventory", "layer_withheld_share", "n_neighbor_states"]
S3 = S0 + [
    "log_outbreaks_7d", "log_outbreaks_21d", "log_outbreaks_60d",
    "log_infected_counties_7d", "log_infected_counties_21d", "log_infected_counties_60d",
    "days_since_outbreak", "days_since_outbreak_censored",
    "log_wild_detections_7d", "log_wild_detections_21d", "log_wild_detections_60d",
    "log_nbr_outbreaks_7d", "log_nbr_outbreaks_21d", "log_nbr_outbreaks_60d",
    "log_nbr_infected_counties_7d", "log_nbr_infected_counties_21d",
    "log_nbr_infected_counties_60d",
    "log_nbr_wild_detections_7d", "log_nbr_wild_detections_21d", "log_nbr_wild_detections_60d",
    "days_since_outbreak_nbr", "days_since_outbreak_nbr_censored",
    "log_breeding_side_pressure", "log_wintering_side_pressure",
    "temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm",
]
MEDIAN_FILL = ["log_known_layer_inventory", "temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm"]
RECENCY_FILL = ["days_since_outbreak", "days_since_outbreak_nbr"]
SLICES = ("all", "new_onset")


def load_state_week(wild: str = "detected", onset_days: int = 28) -> pd.DataFrame:
    """State-weeks from START. y{k} is NaN where the outcome window is not complete."""
    table = {"detected": "model_state_week", "coll": "model_state_week_coll"}[wild]
    with local_engine().connect() as cn:
        df = pd.read_sql(text(f"SELECT * FROM {table} WHERE day >= :d ORDER BY state, day"), cn,
                         params={"d": START.date()}, parse_dates=["day"])
    for k in KS:
        df[f"y{k}"] = df[f"target_outbreak_next_{k}d"].astype(float)
        df.loc[~df[f"target_complete_{k}d"], f"y{k}"] = np.nan
    df["new_onset"] = df["state_new_onset"] if onset_days == 28 else df["state_new_onset_30d"]
    return df


def prepare(df: pd.DataFrame, cols: list[str], train: np.ndarray) -> pd.DataFrame:
    """Fills from the training rows only. A censored recency is 4.0 (past the 365-day window)
    and its censored flag travels with it. Suppressed layer inventory is median-filled here
    and nowhere earlier; it is never set to zero."""
    X = df[cols].copy()
    for c in cols:
        if c in MEDIAN_FILL:
            X[c] = X[c].fillna(X.loc[train, c].median())
        elif c in RECENCY_FILL:
            X[c] = X[c].fillna(4.0)
    left = X.columns[X.isna().any()].tolist()
    assert not left, f"unexpected NULLs: {left}"
    return X


def fit_predict(Xtr, ytr, Xte, params: dict, seeds=SEEDS) -> np.ndarray:
    """Predictions of each seed's forest, shape (n_seeds, n_test). Average over axis 0 for
    the reported score."""
    return np.vstack([RandomForestClassifier(random_state=s, n_jobs=12, **params).fit(Xtr, ytr)
                      .predict_proba(Xte)[:, 1] for s in seeds])


def dev_masks(df: pd.DataFrame, k: int) -> tuple[np.ndarray, np.ndarray]:
    """(train, test): the train rows' whole outcome window ends by DEV_END."""
    known = df[f"y{k}"].notna()
    train = known & (df.day + pd.Timedelta(days=k) <= DEV_END)
    test = known & (df.day >= TEST_FIRST)
    return train.to_numpy(), test.to_numpy()


def fold_masks(df: pd.DataFrame, k: int, fold: str) -> tuple[np.ndarray, np.ndarray]:
    tr_end, v0, v1 = (pd.Timestamp(x) for x in FOLDS[fold])
    known = df[f"y{k}"].notna() & (df.day + pd.Timedelta(days=k) <= DEV_END)
    train = known & (df.day + pd.Timedelta(days=k) <= tr_end)
    val = known & df.day.between(v0, v1)
    return train.to_numpy(), val.to_numpy()


def week_of(day: pd.Series) -> np.ndarray:
    return day.dt.to_period("W").astype(str).to_numpy()


def paired_boot(y, pa, pb, wk, n_boot=N_BOOT, seed=BOOT_SEED):
    """Week-block bootstrap of PR-AUC(a) - PR-AUC(b). Returns lo, hi, P(d > 0), reps used."""
    rng = np.random.default_rng(seed)
    weeks = np.unique(wk)
    idx = {w: np.flatnonzero(wk == w) for w in weeks}
    d = []
    for _ in range(n_boot):
        ix = np.concatenate([idx[w] for w in rng.choice(weeks, size=len(weeks), replace=True)])
        if 0 < y[ix].sum() < len(ix):
            d.append(average_precision_score(y[ix], pa[ix]) - average_precision_score(y[ix], pb[ix]))
    d = np.array(d)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)), float((d > 0).mean()), len(d)


def score_row(y: np.ndarray, p_seeds: np.ndarray) -> dict:
    """Metrics for one model on one slice. p_seeds has shape (n_seeds, n)."""
    p = p_seeds.mean(axis=0)
    ap = average_precision_score(y, p)
    per_seed = [average_precision_score(y, ps) for ps in p_seeds]
    return {"rows": len(y), "events": int(y.sum()), "base_rate": float(y.mean()),
            "pr_auc": ap, "lift": ap / y.mean(), "pr_auc_seed_sd": float(np.std(per_seed, ddof=1)),
            "brier": brier_score_loss(y, p), "mean_pred": float(p.mean())}


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Ten equal-count bins of predicted risk: mean prediction and observed rate in each."""
    q = pd.qcut(pd.Series(p).rank(method="first"), bins, labels=False)
    g = pd.DataFrame({"bin": q, "p": p, "y": y}).groupby("bin")
    return g.agg(n=("y", "size"), mean_pred=("p", "mean"), observed=("y", "mean")).reset_index()


def chosen_params(grain: str = "state") -> dict:
    """The setting chosen by the search (baseline.py search). Fails if the search has not run."""
    return json.loads((OUT / f"chosen_{grain}.json").read_text())["params"]
