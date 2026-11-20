"""The county baseline C3 (Waree's 35 features on model_county_day_35, re-dated wild birds) and
its roll-up to state-weeks (protocol 4.3 and 8.4).

    uv run --extra modeling python -m h5n1.journal.county sample    # cache the modelling rows
    uv run --extra modeling python -m h5n1.journal.county search    # development folds only
    uv run --extra modeling python -m h5n1.journal.county rollup    # 2025+ test, needs tracks a

Rows. Contiguous-US county-days from 2022-02-08 to the last outbreak day. Training uses every
positive plus a 5% hash sample of the rest, negatives weighted 20, as in the earlier county
code. Validation and test rows are every county on every SUNDAY (the forecast days of the
state-week grid), unsampled. The county surface's predictors stop at day - 1 while the
state-week ones include the day: a one-day handicap for the county model.

search. Protocol 4.3: the 24-setting grid plus Waree's setting, three forward-chaining folds
inside 2022-2024, k = 7, all rows, mean PR-AUC, ties to the shallower forest then the larger
leaf. One seed per fit (the protocol does not fix the number; five seeds are used for every
reported score). Resumable: finished settings are kept in county_search.csv.

rollup. C3 fitted through 2024-12-31 minus k with five seeds; P(any county in the state has an
outbreak in the next k days) = 1 - prod(1 - p) over the state's counties on the Sunday. Scored
against S3 on every test state-week and against S3 and S4 on each platform's Track A rows,
paired week-block bootstrap. Read as in protocol 8.4: S3 answers "which state", C3 "which
county"; the comparison does not by itself decide whether social media is used.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score
from sqlalchemy import text

from h5n1.journal import models as M
from h5n1.journal.localdb import local_engine

KEYS = {"state", "fips", "day", "target_outbreak_next_7d", "target_complete_7d", "is_conus",
        "y7", "y14", "y28", "hashed"}
MEDIAN_FILL = ["temp_min_7d_c", "temp_max_7d_c", "precip_7d_mm", "log_layer_inventory"]
RECENCY_FILL = ["days_since_outbreak_county", "days_since_outbreak_neighbor"]
HASH_BUCKETS, HASH_KEEP, NEG_RATE = 10_000, 500, 0.05
FIRST_DAY = "2022-02-08"
SEARCH_K = 7
TABLES = {"detected": "model_county_day_35", "coll": "model_county_day_35_coll"}


def sample_path(wild: str):
    return M.OUT / f"county_sample_{wild}.parquet"


def sample(wild: str) -> pd.DataFrame:
    """The modelling rows, cached. Targets are recomputed from the outbreak table."""
    path = sample_path(wild)
    if path.exists():
        return pd.read_parquet(path)
    targets = ",\n".join(
        f"EXISTS (SELECT 1 FROM fact_h5n1_outbreak o WHERE o.fips = m.fips "
        f"AND o.day BETWEEN m.day + 1 AND m.day + {k}) AS y{k}" for k in M.KS)
    q = f"""
    WITH t AS (
      SELECT m.*, {targets},
             mod(abs(hashtext(m.fips || m.day::text)), {HASH_BUCKETS}) < {HASH_KEEP} AS hashed
      FROM {TABLES[wild]} m
      WHERE m.is_conus AND m.day BETWEEN '{FIRST_DAY}' AND (SELECT max(day) FROM fact_h5n1_outbreak)
    )
    SELECT * FROM t WHERE hashed OR y7 OR y14 OR y28 OR extract(isodow FROM day) = 7
    """
    with local_engine().connect() as cn:
        df = pd.read_sql(text(q), cn, parse_dates=["day"])
    assert (df.y7 == df.target_outbreak_next_7d).all(), "recomputed 7-day target disagrees"
    assert not df.fips.str.startswith("091").any(), "Connecticut planning-region FIPS"
    M.OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    print(f"{wild}: {len(df):,} rows cached ({int(df.hashed.sum()):,} hashed, "
          f"{int((df.day.dt.dayofweek == 6).sum()):,} Sundays, {int(df.y7.sum()):,} y7)", flush=True)
    return df


def columns(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in KEYS]
    assert len(cols) == 35, cols
    return cols


def prepare(df: pd.DataFrame, cols: list[str], train: np.ndarray) -> np.ndarray:
    """Fills from the training rows only; suppressed layer inventory is median-filled here and
    carries its own flag column; it is never set to zero."""
    X = df[cols].copy()
    for c in MEDIAN_FILL:
        X[c] = X[c].fillna(X.loc[train, c].median())
    for c in RECENCY_FILL:
        X[c] = X[c].fillna(4.0)
    left = X.columns[X.isna().any()].tolist()
    assert not left, f"unexpected NULLs: {left}"
    return X.to_numpy()


def fit_predict(X, y, train, test, params, seeds) -> np.ndarray:
    """(n_seeds, n_test). Negatives in the 5% sample are weighted back up."""
    w = np.where(y[train] == 1, 1.0, 1.0 / NEG_RATE)
    return np.vstack([RandomForestClassifier(random_state=s, n_jobs=12, **params)
                      .fit(X[train], y[train], sample_weight=w).predict_proba(X[test])[:, 1] for s in seeds])


def masks(df: pd.DataFrame, k: int, train_through: pd.Timestamp, last: pd.Timestamp):
    """Training rows (sampled, whole window inside training) and all complete Sundays."""
    y = df[f"y{k}"].to_numpy()
    train = ((df.day + pd.Timedelta(days=k) <= train_through) & (df.hashed | (y == 1))).to_numpy()
    sunday = ((df.day.dt.dayofweek == 6) & (df.day + pd.Timedelta(days=k) <= last)).to_numpy()
    return y.astype(float), train, sunday


def search() -> None:
    df = sample("detected")
    cols, k = columns(df), SEARCH_K
    path = M.OUT / "county_search.csv"
    done = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["setting"])
    rows = done.to_dict("records")
    for i, params in enumerate(M.GRID):
        if i in set(done.setting):
            continue
        rec = {"setting": i, **params}
        for fold, (tr_end, v0, v1) in M.FOLDS.items():
            y, train, sunday = masks(df, k, pd.Timestamp(tr_end), M.DEV_END)
            val = sunday & df.day.between(v0, v1).to_numpy()
            p = fit_predict(prepare(df, cols, train), y, train, val, params, M.SEEDS[:1])[0]
            rec[fold] = average_precision_score(y[val], p)
            rec[f"{fold}_events"] = int(y[val].sum())
        rec["mean"] = float(np.mean([rec[f] for f in M.FOLDS]))
        rows.append(rec)
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"setting {i}: depth {params['max_depth']} leaf {params['min_samples_leaf']} "
              f"mf {params['max_features']!s:.4} trees {params['n_estimators']}  mean {rec['mean']:.4f}", flush=True)
    res = pd.DataFrame(rows).sort_values("setting").reset_index(drop=True)
    res["crit"] = res["mean"].round(4)
    best = res.sort_values(["crit", "max_depth", "min_samples_leaf"], ascending=[False, True, False]).iloc[0]
    params = M.GRID[int(best["setting"])]
    (M.OUT / "chosen_county.json").write_text(json.dumps({
        "params": params, "criterion": "mean PR-AUC, k=7, all county-Sundays, folds F1-F3, one seed",
        "mean": float(best["mean"]), "waree_mean": float(res.iloc[-1]["mean"])}, indent=2))
    print(f"\nchosen: {params}  ({best['mean']:.4f}; Waree's {res.iloc[-1]['mean']:.4f})")


def rollup() -> None:
    with local_engine().connect() as cn:
        last = pd.read_sql(text("SELECT max(day) d FROM fact_h5n1_outbreak"), cn, parse_dates=["d"]).d[0]
    settings = {"searched": M.chosen_params("county"), "waree": M.WAREE}
    runs = [("detected", "searched"), ("detected", "waree"), ("coll", "searched")]
    scores, rel, rolled, county = [], [], [], []
    for wild, sname in runs:
        df = sample(wild)
        cols = columns(df)
        for k in M.KS:
            y, train, sunday = masks(df, k, M.DEV_END, last)
            test = sunday & (df.day >= M.TEST_FIRST).to_numpy()
            ps = fit_predict(prepare(df, cols, train), y, train, test, settings[sname], M.SEEDS)
            base = {"wild": wild, "setting": sname, "k": k, "train_rows": int(train.sum()),
                    "train_events": int(y[train].sum())}
            scores.append({**base, **M.score_row(y[test], ps)})
            p = ps.mean(axis=0)
            rel.append(M.reliability(y[test], p).assign(**base))
            pc = df.loc[test, ["state", "day"]].assign(p=p)
            county.append(df.loc[test, ["state", "fips", "day"]].assign(
                p=p, y=y[test].astype("int8"), wild=wild, setting=sname, k=k))
            r =(1 - (1 - pc.p).groupby([pc.state, pc.day]).prod()).rename("rollup").reset_index()
            rolled.append(r.assign(wild=wild, setting=sname, k=k))
            print(f"{wild} {sname} k={k}: county PR-AUC {scores[-1]['pr_auc']:.4f}, {len(r)} state-weeks", flush=True)
    pd.DataFrame(scores).to_csv(M.OUT / "county_scores.csv", index=False)
    pd.concat(rel).to_csv(M.OUT / "county_reliability.csv", index=False)
    ru = pd.concat(rolled)
    ru.to_csv(M.OUT / "county_rollup_preds.csv", index=False)
    # one row per county-Sunday, run and horizon: the mean of the five seeds, with the outcome
    pd.concat(county).to_parquet(M.OUT / "county_preds.parquet", index=False)

    params = M.chosen_params("state")
    sw = M.load_state_week("detected")
    rows = []

    def score(o: pd.DataFrame, base: dict, pairs) -> None:
        wk = M.week_of(o.day)
        for sl, m in (("all", np.ones(len(o), bool)), ("new_onset", o.new_onset.to_numpy(bool))):
            y = o.y.to_numpy()[m]
            for new, ref in pairs:
                a, b = o[new].to_numpy()[m], o[ref].to_numpy()[m]
                lo, hi, pgt, reps = M.paired_boot(y, a, b, wk[m])
                rows.append({**base, "slice": sl, "comparison": f"{new} - {ref}", "rows": int(m.sum()),
                             "events": int(y.sum()), "pr_auc_new": average_precision_score(y, a),
                             "pr_auc_ref": average_precision_score(y, b),
                             "delta": average_precision_score(y, a) - average_precision_score(y, b),
                             "lo": lo, "hi": hi, "P(d>0)": pgt, "boot_reps": reps})

    for k in M.KS:
        train, test = M.dev_masks(sw, k)
        X = M.prepare(sw, M.S3, train)
        s3 = M.fit_predict(X[train], sw.loc[train, f"y{k}"], X[test], params).mean(axis=0)
        allrows = sw.loc[test, ["state", "day", "new_onset"]].assign(y=sw.loc[test, f"y{k}"].to_numpy(), S3=s3)
        for wild, sname in runs:
            r = ru[(ru.wild == wild) & (ru.setting == sname) & (ru.k == k)][["state", "day", "rollup"]]
            o = allrows.merge(r, on=["state", "day"], how="left")
            assert o.rollup.notna().all(), "state-weeks without a roll-up"
            base = {"k": k, "county_wild": wild, "county_setting": sname}
            score(o, {**base, "rows_from": "all state-weeks"}, [("S3", "rollup")])
            if (wild, sname) != ("detected", "searched"):
                continue
            for plat in ("meta", "x"):
                o = pd.read_csv(M.OUT / f"track_a_oof_{plat}_primary_k{k}.csv", parse_dates=["day"])
                o = o.merge(r, on=["state", "day"], how="left")
                assert o.rollup.notna().all(), f"{plat} k={k}: rows without a roll-up"
                score(o, {**base, "rows_from": f"{plat} Track A rows"}, [("S3", "rollup"), ("S4", "rollup")])
        print(f"k={k} scored", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(M.OUT / "county_rollup.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(pd.DataFrame(scores).round(4).to_string(index=False))
    print(res.round(4).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["sample", "search", "rollup"])
    what = ap.parse_args().what
    if what == "sample":
        for w in TABLES:
            sample(w)
    else:
        {"search": search, "rollup": rollup}[what]()
