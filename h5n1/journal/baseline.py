"""The state baseline: hyperparameter search (protocol 4.3) and the effect of re-dating the
wild-bird features (protocol 4.2 and 8.6).

    uv run --extra modeling python -m h5n1.journal.baseline search   # development folds only
    uv run --extra modeling python -m h5n1.journal.baseline report   # 2025+ test

`search` uses 2022-2024 only: three forward-chaining folds, S3, k = 14, new-onset state-weeks,
mean PR-AUC. Ties go to the shallower forest, then the larger leaf. The chosen setting is
written to data/journal/models/chosen_state.json and is then used for every state-week arm.

`report` scores S0 and S3 on the test period under both wild-bird datings, with the searched
setting and with Waree's fixed setting, and gives the paired difference between datings.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from h5n1.journal import models as M


def search() -> None:
    M.OUT.mkdir(parents=True, exist_ok=True)
    df = M.load_state_week("detected")
    k = 14
    rows = []
    for i, params in enumerate(M.GRID):
        rec = {"setting": i, **params}
        for fold in M.FOLDS:
            train, val = M.fold_masks(df, k, fold)
            X = M.prepare(df, M.S3, train)
            p = M.fit_predict(X[train], df.loc[train, f"y{k}"], X[val], params).mean(axis=0)
            y = df.loc[val, f"y{k}"].to_numpy()
            on = df.loc[val, "new_onset"].to_numpy()
            rec[f"{fold}_new_onset"] = average_precision_score(y[on], p[on])
            rec[f"{fold}_all"] = average_precision_score(y, p)
            rec[f"{fold}_events_new_onset"] = int(y[on].sum())
        rec["mean_new_onset"] = float(np.mean([rec[f"{f}_new_onset"] for f in M.FOLDS]))
        rec["mean_all"] = float(np.mean([rec[f"{f}_all"] for f in M.FOLDS]))
        rows.append(rec)
        print(f"setting {i}: depth {params['max_depth']} leaf {params['min_samples_leaf']} "
              f"mf {params['max_features']!s:.4} trees {params['n_estimators']}  "
              f"new-onset {rec['mean_new_onset']:.4f}  all {rec['mean_all']:.4f}", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(M.OUT / "search_state.csv", index=False)
    # criterion: mean new-onset PR-AUC; ties (to 4 decimals) -> shallower, then larger leaf
    res["crit"] = res["mean_new_onset"].round(4)
    best = res.sort_values(["crit", "max_depth", "min_samples_leaf"],
                           ascending=[False, True, False]).iloc[0]
    params = M.GRID[int(best["setting"])]
    waree = res.iloc[-1]
    (M.OUT / "chosen_state.json").write_text(json.dumps({
        "params": params, "criterion": "mean PR-AUC, k=14, new-onset, folds F1-F3",
        "mean_new_onset": float(best["mean_new_onset"]),
        "waree_mean_new_onset": float(waree["mean_new_onset"])}, indent=2))
    print(f"\nchosen: {params}  ({best['mean_new_onset']:.4f}; Waree's {waree['mean_new_onset']:.4f})")


def report() -> None:
    settings = {"searched": M.chosen_params("state"), "waree": M.WAREE}
    data = {w: M.load_state_week(w) for w in ("detected", "coll")}
    rows, diffs, rel = [], [], []
    for sname, params in settings.items():
        for k in M.KS:
            preds = {}
            for wild, df in data.items():
                train, test = M.dev_masks(df, k)
                for arm, cols in (("S0", M.S0), ("S3", M.S3)):
                    if arm == "S0" and wild == "coll":
                        continue                      # S0 has no wild-bird column
                    X = M.prepare(df, cols, train)
                    ps = M.fit_predict(X[train], df.loc[train, f"y{k}"], X[test], params)
                    preds[(arm, wild)] = ps
                    y = df.loc[test, f"y{k}"].to_numpy()
                    on = df.loc[test, "new_onset"].to_numpy()
                    for sl, m in (("all", np.ones(len(y), bool)), ("new_onset", on)):
                        rows.append({"setting": sname, "k": k, "arm": arm, "wild": wild, "slice": sl,
                                     "train_rows": int(train.sum()),
                                     "train_events": int(df.loc[train, f"y{k}"].sum()),
                                     **M.score_row(y[m], ps[:, m])})
                    if sname == "searched":
                        rel.append(M.reliability(y, ps.mean(axis=0)).assign(k=k, arm=arm, wild=wild))
            df = data["detected"]
            _, test = M.dev_masks(df, k)
            y = df.loc[test, f"y{k}"].to_numpy()
            on = df.loc[test, "new_onset"].to_numpy()
            wk = M.week_of(df.loc[test, "day"])
            a, b = preds[("S3", "detected")].mean(axis=0), preds[("S3", "coll")].mean(axis=0)
            for sl, m in (("all", np.ones(len(y), bool)), ("new_onset", on)):
                lo, hi, pgt, reps = M.paired_boot(y[m], a[m], b[m], wk[m])
                diffs.append({"setting": sname, "k": k, "slice": sl,
                              "pr_auc_detected": average_precision_score(y[m], a[m]),
                              "pr_auc_collection": average_precision_score(y[m], b[m]),
                              "detected_minus_collection":
                                  average_precision_score(y[m], a[m]) - average_precision_score(y[m], b[m]),
                              "lo": lo, "hi": hi, "P(d>0)": pgt, "boot_reps": reps})
            print(f"{sname} k={k} done", flush=True)
    res, dif = pd.DataFrame(rows), pd.DataFrame(diffs)
    res.to_csv(M.OUT / "baseline_state.csv", index=False)
    dif.to_csv(M.OUT / "baseline_state_redating.csv", index=False)
    pd.concat(rel).to_csv(M.OUT / "baseline_state_reliability.csv", index=False)
    pd.set_option("display.width", 250)
    print(res.round(4).to_string(index=False))
    print(dif.round(4).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["search", "report"])
    {"search": search, "report": report}[ap.parse_args().what]()
