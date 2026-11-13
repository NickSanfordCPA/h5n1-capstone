"""The no-X baselines for the X utility analysis: the state baseline (S3), its floor (S0), and the
county 35-feature forest (C3). See docs/x_utility_spec.md.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_baselines compare   # S3 vs roll-up, folds
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_baselines county    # C3 on 2025+

`compare` is the check that S3 is the best available state model: in the forward-chaining folds it
scores S3 against the county forest rolled up to state-weeks as 1 - prod(1 - p). `county` reports
C3 on its own. It contains no X, so running it does not touch the 2025+ X gate.

Waree's shallow forest (300 trees / depth 6 / min_leaf 2) with no search anywhere: tuning on a
temporal validation window hurt out of time on the county model.

Place enters S0 through fixed exposure, not a state ID: the exposure columns already identify each
state, and 49 one-hot columns would only dilute the forest's feature sampling.
"""
from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from h5n1.db import get_engine
from h5n1.models.x_lift import N_BOOT, NEG_HASH_BUCKETS, NEG_HASH_KEEP, NEG_SAMPLE_RATE, RF_PARAMS, SEEDS, paired_boot

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "X_derived" / "x_utility"
KS = (7, 14, 30)
# First Sunday whose 56d trailing volume window starts on or after the first X post (2022-06-01).
START = "2022-08-07"
EXPLORE_END = pd.Timestamp("2024-12-31")
TEST_START = pd.Timestamp("2025-01-01")
FOLDS = {  # name: (train through, validate from, validate through)
    "F1": ("2023-09-30", "2023-10-01", "2024-03-31"),
    "F2": ("2024-03-31", "2024-04-01", "2024-09-30"),
    "F3": ("2024-09-30", "2024-10-01", "2024-12-31"),
}

S0 = ["doy_sin", "doy_cos", "log_poultry_operations", "log_broiler_operations", "log_turkey_operations",
      "log_duck_operations", "log_population", "log_known_layer_inventory", "layer_withheld_share",
      "n_neighbor_states"]
S3 = S0 + [
    "log_outbreaks_7d", "log_outbreaks_21d", "log_outbreaks_60d",
    "log_infected_counties_7d", "log_infected_counties_21d", "log_infected_counties_60d",
    "days_since_outbreak", "days_since_outbreak_censored",
    "log_wild_detections_7d", "log_wild_detections_21d", "log_wild_detections_60d",
    "log_nbr_outbreaks_7d", "log_nbr_outbreaks_21d", "log_nbr_outbreaks_60d",
    "log_nbr_infected_counties_7d", "log_nbr_infected_counties_21d", "log_nbr_infected_counties_60d",
    "log_nbr_wild_detections_7d", "log_nbr_wild_detections_21d", "log_nbr_wild_detections_60d",
    "days_since_outbreak_nbr", "days_since_outbreak_nbr_censored",
    "log_breeding_side_pressure", "log_wintering_side_pressure",
    "temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm",
]
MEDIAN_FILL = ["log_known_layer_inventory", "temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm"]
RECENCY_FILL = ["days_since_outbreak", "days_since_outbreak_nbr"]

C3_EXTRA = ["log_wild_detections_7d", "log_wild_detections_21d", "log_wild_detections_60d",
            "log_nbr_wild_detections_7d", "log_nbr_wild_detections_60d",
            "log_nbr_infected_counties_21d", "log_nbr_infected_counties_60d", "n_neighbors",
            "days_since_outbreak_county_censored", "days_since_outbreak_neighbor_censored",
            "layer_inventory_suppressed", "weather_missing"]
C3_KEYS = {"state", "fips", "day", "target_outbreak_next_7d", "target_complete_7d", "is_conus",
           "y7", "y14", "y30", "hashed"}
C3_MEDIAN_FILL = ["temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm", "log_layer_inventory"]
C3_RECENCY_FILL = ["days_since_outbreak_county", "days_since_outbreak_neighbor"]


def load_state_week(end: pd.Timestamp = EXPLORE_END) -> pd.DataFrame:
    """State-weeks from START to `end`. The default stops before the one-shot window. With the default
    end, targets past `end` are nulled below, so no 2025+ outcome can enter exploration."""
    df = pd.read_sql(f"SELECT * FROM model_state_week WHERE day BETWEEN '{START}' AND '{end.date()}'",
                     get_engine(), parse_dates=["day"])
    for k in KS:
        df[f"y{k}"] = df[f"target_outbreak_next_{k}d"].astype(float)
        hidden = (df.day + pd.Timedelta(days=k) > end) | ~df[f"target_complete_{k}d"]
        df.loc[hidden, f"y{k}"] = np.nan
    return df


def prepare_state(df: pd.DataFrame, cols: list[str], train: np.ndarray) -> pd.DataFrame:
    """Fills, from the training rows only. NULL recency is censored: 4.0 sits past the 365-day window
    and the censored flag travels with it, as on the county surface."""
    X = df[cols].copy()
    for c in cols:
        if c in MEDIAN_FILL:
            X[c] = X[c].fillna(X.loc[train, c].median())
        elif c in RECENCY_FILL:
            X[c] = X[c].fillna(4.0)
    left = X.columns[X.isna().any()].tolist()
    assert not left, f"unexpected NULLs: {left}"
    return X


def fit_predict(Xtr, ytr, Xte, seeds=SEEDS, weight=None) -> np.ndarray:
    ps = [RandomForestClassifier(random_state=s, **RF_PARAMS).fit(Xtr, ytr, sample_weight=weight)
          .predict_proba(Xte)[:, 1] for s in seeds]
    return np.mean(ps, axis=0)


def fold_masks(df: pd.DataFrame, k: int, fold: str) -> tuple[np.ndarray, np.ndarray]:
    """Train rows have their whole outcome window inside training; validate rows are complete."""
    tr_end, v0, v1 = (pd.Timestamp(x) for x in FOLDS[fold])
    known = df[f"y{k}"].notna()
    train = (df.day + pd.Timedelta(days=k) <= tr_end) & known
    val = df.day.between(v0, v1) & known
    return train.to_numpy(), val.to_numpy()


def load_county(end: pd.Timestamp) -> pd.DataFrame:
    """Hash-sampled negatives plus every positive, as x_lift.load_panel(), and every Sunday (the
    roll-up) and every test day after TEST_START when end allows it."""
    targets = ",\n".join(
        f"EXISTS (SELECT 1 FROM fact_h5n1_outbreak o WHERE o.fips = m.fips "
        f"AND o.day BETWEEN m.day + 1 AND m.day + {k}) AS y{k}" for k in KS)
    q = f"""
    WITH t AS (
      SELECT m.*, {targets},
             mod(abs(hashtext(m.fips || m.day::text)), {NEG_HASH_BUCKETS}) < {NEG_HASH_KEEP} AS hashed
      FROM model_county_day_35 m
      WHERE m.is_conus AND m.day BETWEEN '2022-02-08' AND '{end.date()}'
    )
    SELECT * FROM t
    WHERE hashed OR y7 OR y14 OR y30 OR extract(isodow FROM day) = 7 OR day >= '{TEST_START.date()}'
    """
    df = pd.read_sql(q, get_engine(), parse_dates=["day"])
    assert (df.y7 == df.target_outbreak_next_7d).all(), "recomputed 7d target disagrees"
    return df


def county_columns(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in C3_KEYS]
    assert len(cols) == 35, cols
    return cols


def prepare_county(df: pd.DataFrame, cols: list[str], train: np.ndarray) -> pd.DataFrame:
    X = df[cols].copy()
    for c in C3_MEDIAN_FILL:
        X[c] = X[c].fillna(X.loc[train, c].median())
    for c in C3_RECENCY_FILL:
        X[c] = X[c].fillna(4.0)
    left = X.columns[X.isna().any()].tolist()
    assert not left, f"unexpected NULLs: {left}"
    return X


def _county_fit(df, X, yk, train_mask, test_mask):
    train = train_mask & (df.hashed | df_y(df, yk)).to_numpy()
    w = np.where(yk[train] == 1, 1.0, 1.0 / NEG_SAMPLE_RATE)
    return fit_predict(X[train], yk[train], X[test_mask], weight=w)


def df_y(df, yk):
    return pd.Series(yk == 1, index=df.index)


def compare() -> pd.DataFrame:
    """S0, S3 and the county roll-up on all state-weeks in the folds."""
    sw = load_state_week()
    county = load_county(EXPLORE_END)
    cols = county_columns(county)
    rows = []
    for k in KS:
        yc = county[f"y{k}"].to_numpy().astype(float)
        oof = []
        for fold in FOLDS:
            train, val = fold_masks(sw, k, fold)
            part = sw.loc[val, ["state", "day", f"y{k}", "state_new_onset"]].rename(columns={f"y{k}": "y"})
            for name, arm in {"S0": S0, "S3": S3}.items():
                X = prepare_state(sw, arm, train)
                part[name] = fit_predict(X[train], sw.loc[train, f"y{k}"], X[val])
            tr_end, _, _ = (pd.Timestamp(x) for x in FOLDS[fold])
            c_tr = (county.day + pd.Timedelta(days=k) <= tr_end).to_numpy()
            c_te = county.day.isin(part.day.unique()).to_numpy()
            Xc = prepare_county(county, cols, c_tr).to_numpy()
            pc = county.loc[c_te, ["state", "day"]].assign(p=_county_fit(county, Xc, yc, c_tr, c_te))
            roll = (1 - (1 - pc.p).groupby([pc.state, pc.day]).prod()).rename("rollup").reset_index()
            part = part.merge(roll, on=["state", "day"], how="left")
            part["fold"] = fold
            oof.append(part)
        o = pd.concat(oof)
        o.to_csv(OUT / f"baseline_oof_k{k}.csv", index=False)
        wk = o.day.dt.to_period("W").astype(str).to_numpy()
        rng = np.random.default_rng(20260728)
        for sl, m in {"all": np.ones(len(o), bool), "state_new_onset": o.state_new_onset.to_numpy()}.items():
            y = o.y.to_numpy()[m]
            s3 = o.S3.to_numpy()[m]
            ru = o.rollup.to_numpy()[m]
            lo, hi, pgt = paired_boot(y, s3, ru, wk[m], rng, N_BOOT)
            rows.append({"k": k, "slice": sl, "rows": int(m.sum()), "positives": int(y.sum()),
                         "base_rate": y.mean(),
                         "S0": average_precision_score(y, o.S0.to_numpy()[m]),
                         "S3": average_precision_score(y, s3), "rollup": average_precision_score(y, ru),
                         "S3_minus_rollup": average_precision_score(y, s3) - average_precision_score(y, ru),
                         "d_lo": lo, "d_hi": hi, "P(S3>rollup)": pgt})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "baseline_compare_folds.csv", index=False)
    print(res.round(4).to_string(index=False))
    return res


def county_report() -> pd.DataFrame:
    """C3 alone on 2025+: train through 2024-12-31 minus k, every CONUS county-day after."""
    max_day = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
    df = load_county(max_day)
    cols = county_columns(df)
    onset = (df.log_own_outbreaks_60d == 0).to_numpy()
    rows = []
    for k in KS:
        yk = df[f"y{k}"].to_numpy().astype(float)
        train = (df.day + pd.Timedelta(days=k) < TEST_START).to_numpy()
        test = ((df.day >= TEST_START) & (df.day + pd.Timedelta(days=k) <= max_day)).to_numpy()
        X = prepare_county(df, cols, train).to_numpy()
        p = _county_fit(df, X, yk, train, test)
        for sl, m in {"all": np.ones(test.sum(), bool), "new_onset": onset[test]}.items():
            y = yk[test][m]
            ap = average_precision_score(y, p[m])
            rows.append({"k": k, "slice": sl, "test_rows": int(m.sum()), "positives": int(y.sum()),
                         "base_rate": y.mean(), "pr_auc": ap, "lift_x": ap / y.mean(),
                         "roc": roc_auc_score(y, p[m])})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "county_c3_2025.csv", index=False)
    print(res.round(4).to_string(index=False))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["compare", "county"])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    {"compare": compare, "county": county_report}[a.what]()
