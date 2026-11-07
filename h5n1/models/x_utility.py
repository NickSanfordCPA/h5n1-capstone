"""Track A of the X utility analysis: do X sentiment and volume add to a state baseline? Nested
forests on covered state-weeks, at k = 7, 14, 30, all rows and state new-onset. Design in
docs/x_utility_spec.md.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_utility explore
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_utility final       # once, after the gate

`explore` sees 2024-12-31 at most, and hides any outcome dated later (x_baselines.load_state_week).
`final` refuses to run unless docs/x_utility_spec.md is committed.

ARMS (same rows in every comparison):
    S0   place, season, fixed exposure          S1   X only        S2   S0 + X
    S3   state baseline                         S4   S3 + X        S4v / S4s   S3 + volume / sentiment
Reading: S2 and S4 both help = new information; S2 only = real but redundant; neither = no
detectable signal at this grain.

SIGNIFICANCE. A forest gives no significance, so:
  offset logistic   logit p = logit(cross-fitted S3 p) + b * x, with SEs clustered on state and
                    week: x's effect conditional on the whole baseline, free of forest dilution.
  permutation       shuffle X as a block across weeks within state x season, on held-out rows.
  residualized X    the part of each X that S3's columns can't explain (fit on train), then S4 again.
  deviance split    unique to baseline, unique to X, shared.
"""
from __future__ import annotations

import argparse
import subprocess

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score
from statsmodels.stats.outliers_influence import variance_inflation_factor

from h5n1.db import get_engine
from h5n1.features.x_state import SENTIMENT, VOLUME, X_FEATURES, build_features, estimate_pseudocount, load_posts
from h5n1.models.x_baselines import (EXPLORE_END, FOLDS, KS, OUT, ROOT, S0, S3, fit_predict, fold_masks,
                                     load_state_week, prepare_state)
from h5n1.models.x_lift import RF_PARAMS, SEEDS, paired_boot

SPEC = ROOT / "docs" / "x_utility_spec.md"
N_BOOT_EXPLORE, N_BOOT_FINAL, N_PERM = 500, 2000, 300
SEED = 20260728
TEST_FIRST = pd.Timestamp("2025-01-05")  # first Sunday of the test
ARMS = {"S0": S0, "S1": X_FEATURES, "S2": S0 + X_FEATURES, "S3": S3, "S4": S3 + X_FEATURES,
        "S4v": S3 + VOLUME, "S4s": S3 + SENTIMENT}
FROZEN_VOLUME, FROZEN_SENTIMENT = ["x_share_7d"], ["x_sent_7d", "x_sent_chg"]  # docs/x_utility_spec.md
FROZEN = FROZEN_VOLUME + FROZEN_SENTIMENT
FROZEN_ARMS = {"S0": S0, "S1": FROZEN, "S2": S0 + FROZEN, "S3": S3, "S4": S3 + FROZEN,
               "S4v": S3 + FROZEN_VOLUME, "S4s": S3 + FROZEN_SENTIMENT}
COMPARISONS = [("S2", "S0"), ("S4", "S3"), ("S4v", "S3"), ("S4s", "S3")]
SLICES = ("all", "state_new_onset")


def panel(posts: pd.DataFrame, sw: pd.DataFrame, m: float, end_lag: int = 0) -> pd.DataFrame:
    """Baseline rows with X features attached. Rows are not yet limited to covered ones."""
    states = sorted(sw.state.unique())
    sundays = pd.DatetimeIndex(sorted(sw.day.unique()))
    return sw.merge(build_features(posts, states, sundays, m, end_lag), on=["state", "day"], how="left")


def season(day: pd.Series) -> pd.Series:
    """July-June outbreak season, labelled by its start year."""
    return day.dt.year - (day.dt.month < 7).astype(int)


def design(df: pd.DataFrame, cols: list[str], train: np.ndarray) -> pd.DataFrame:
    base = [c for c in cols if not c.startswith(("x_", "log_x_"))]
    X = prepare_state(df, base, train) if base else pd.DataFrame(index=df.index)
    return pd.concat([X, df[[c for c in cols if c not in base]]], axis=1)[cols]


def score_arms(df: pd.DataFrame, k: int, splits, arms: dict[str, list[str]]) -> pd.DataFrame:
    """Held-out predictions of every arm over the splits. `splits` is a list of (name, train, val)."""
    parts = []
    for name, train, val in splits:
        part = df.loc[val, ["state", "day", "state_new_onset", f"y{k}"]].rename(columns={f"y{k}": "y"})
        part["fold"] = name
        for arm, cols in arms.items():
            X = design(df, cols, train)
            part[arm] = fit_predict(X[train], df.loc[train, f"y{k}"], X[val])
        parts.append(part)
    return pd.concat(parts)


def explore_splits(df: pd.DataFrame, k: int) -> list:
    return [(f, *fold_masks(df, k, f)) for f in FOLDS]


def slice_mask(o: pd.DataFrame, sl: str) -> np.ndarray:
    return np.ones(len(o), bool) if sl == "all" else o.state_new_onset.to_numpy()


def lift_rows(o: pd.DataFrame, k: int, n_boot: int, ci: float, variant: str) -> list[dict]:
    rng = np.random.default_rng(SEED)
    wk = o.day.dt.to_period("W").astype(str).to_numpy()
    rows = []
    for sl in SLICES:
        m = slice_mask(o, sl)
        y = o.y.to_numpy()[m]
        if y.sum() == 0:
            continue
        scores = {a: average_precision_score(y, o[a].to_numpy()[m]) for a in o.columns if a in ARMS}
        for new, ref in COMPARISONS:
            if new not in o or ref not in o:
                continue
            lo, hi, pgt = paired_boot(y, o[new].to_numpy()[m], o[ref].to_numpy()[m], wk[m], rng, n_boot, ci)
            rows.append({"variant": variant, "k": k, "slice": sl, "comparison": f"{new} vs {ref}",
                         "rows": int(m.sum()), "positives": int(y.sum()), "weeks": len(np.unique(wk[m])),
                         "base_rate": y.mean(), "pr_auc_ref": scores[ref], "pr_auc_new": scores[new],
                         "delta": scores[new] - scores[ref], "d_lo": lo, "d_hi": hi, "ci": ci, "P(d>0)": pgt,
                         **{f"pr_auc_{a}": s for a, s in scores.items()}})
    return rows


def cross_fit_s3(df: pd.DataFrame, k: int, rows: np.ndarray) -> np.ndarray:
    """Leave-one-season-out S3 scores for `rows`, so the offset isn't the forest's in-sample fit."""
    s = season(df.day)
    out = np.full(len(df), np.nan)
    known = df[f"y{k}"].notna().to_numpy()
    for g in s[rows].unique():
        tr = rows & known & (s != g).to_numpy()
        te = rows & (s == g).to_numpy()
        X = design(df, S3, tr)
        out[te] = fit_predict(X[tr], df.loc[tr, f"y{k}"], X[te])
    return out


def logit(p):
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def offset_logit(df: pd.DataFrame, k: int, rows: np.ndarray, off: np.ndarray, variant: str,
                 xs: list[str] = X_FEATURES) -> list[dict]:
    """b for each X alone and all X jointly, in SD units, SEs two-way clustered on state and week."""
    out = []
    for sl in SLICES:
        m = rows & (np.ones(len(df), bool) if sl == "all" else df.state_new_onset.fillna(False).to_numpy())
        d = df[m]
        z = (d[xs] - d[xs].mean()) / d[xs].std()
        groups = np.column_stack([pd.factorize(d.state)[0], pd.factorize(d.day)[0]])
        for name, cols in [(c, [c]) for c in xs] + [("joint", xs)]:
            fit = sm.GLM(d[f"y{k}"], sm.add_constant(z[cols]), family=sm.families.Binomial(),
                         offset=logit(off[m])).fit(cov_type="cluster", cov_kwds={"groups": groups})
            for c in cols:
                out.append({"variant": variant, "k": k, "slice": sl, "model": name, "term": c,
                            "beta_per_sd": fit.params[c], "se": fit.bse[c], "p": fit.pvalues[c],
                            "or_per_sd": np.exp(fit.params[c]), "rows": int(m.sum()),
                            "positives": int(d[f"y{k}"].sum())})
            if name == "joint":
                out.append({"variant": variant, "k": k, "slice": sl, "model": "joint", "term": "wald_all_x",
                            "p": float(fit.wald_test(np.eye(len(fit.params))[1:], scalar=True).pvalue),
                            "rows": int(m.sum()), "positives": int(d[f"y{k}"].sum())})
    return out


def permutation_test(df: pd.DataFrame, k: int, train: np.ndarray, val: np.ndarray, sl: str,
                     rng, n_perm: int = N_PERM, xs: list[str] = X_FEATURES) -> dict:
    """Fit S4 once, then shuffle the X block across weeks within state x season on the held-out rows.
    A free shuffle would break seasonality and overstate significance."""
    X = design(df, S3 + xs, train)
    rf = [RandomForestClassifier(random_state=s, **RF_PARAMS).fit(X[train], df.loc[train, f"y{k}"]) for s in SEEDS]
    predict = lambda T: np.mean([r.predict_proba(T)[:, 1] for r in rf], axis=0)  # noqa: E731
    Xv = X[val].reset_index(drop=True)
    d = df[val].reset_index(drop=True)
    keep = np.ones(len(d), bool) if sl == "all" else d.state_new_onset.to_numpy()
    y = d[f"y{k}"].to_numpy()[keep]
    obs = average_precision_score(y, predict(Xv)[keep])
    groups = [g.index.to_numpy() for _, g in d.groupby([d.state, season(d.day)])]
    xvals = Xv[xs].to_numpy()
    null = []
    for _ in range(n_perm):
        order = np.arange(len(d))
        for g in groups:
            order[g] = rng.permutation(g)
        T = Xv.copy()
        T[xs] = xvals[order]
        null.append(average_precision_score(y, predict(T)[keep]))
    null = np.array(null)
    return {"observed": obs, "null_mean": null.mean(), "gain": obs - null.mean(),
            "p": ((null >= obs).sum() + 1) / (n_perm + 1)}


def residualized(df: pd.DataFrame, k: int, splits, xs: list[str] = X_FEATURES) -> pd.DataFrame:
    """S3 plus only the part of each X that S3's columns can't explain, fit on the training rows."""
    parts = []
    for name, train, val in splits:
        X = design(df, S3, train)
        r = pd.DataFrame(index=df.index)
        for c in xs:
            lin = LinearRegression().fit(X[train], df.loc[train, c])
            r[c] = df[c] - lin.predict(X)
        XR = pd.concat([X, r], axis=1)
        part = df.loc[val, ["state", "day", "state_new_onset", f"y{k}"]].rename(columns={f"y{k}": "y"})
        part["S3"] = fit_predict(X[train], df.loc[train, f"y{k}"], X[val])
        part["S4"] = fit_predict(XR[train], df.loc[train, f"y{k}"], XR[val])
        parts.append(part)
    return pd.concat(parts)


def deviance_split(o: pd.DataFrame, k: int, variant: str) -> list[dict]:
    """Explained deviance from one-input recalibrations of held-out S3 and S1 scores, and of both."""
    rows = []
    for sl in SLICES:
        m = slice_mask(o, sl)
        y = o.y.to_numpy()[m]
        a, b = logit(o.S3.to_numpy()[m]), logit(o.S1.to_numpy()[m])

        def d2(*xs):
            return sm.GLM(y, sm.add_constant(np.column_stack(xs)), family=sm.families.Binomial()).fit()

        fit_ab = d2(a, b)
        null = fit_ab.null_deviance
        da, db, dab = (1 - f.deviance / null for f in (d2(a), d2(b), fit_ab))
        rows.append({"variant": variant, "k": k, "slice": sl, "d2_baseline": da, "d2_x": db, "d2_both": dab,
                     "unique_baseline": dab - db, "unique_x": dab - da, "shared": da + db - dab})
    return rows


def descriptives(df: pd.DataFrame, k: int = 14) -> None:
    """Correlations, VIF, lead/lag and event-time tables. Exploration window only."""
    d = df[df.x_covered].copy()
    tr = np.ones(len(d), bool)
    B = design(d, S3, tr)
    rows = []
    within_x = d[X_FEATURES] - d.groupby("state")[X_FEATURES].transform("mean")
    within_b = B - B.groupby(d.state).transform("mean")
    for x in X_FEATURES:
        for b in S3:
            rows.append({"x": x, "base": b, "spearman_pooled": spearmanr(d[x], B[b]).statistic,
                         "spearman_within_state": spearmanr(within_x[x], within_b[b]).statistic})
    corr = pd.DataFrame(rows)
    corr.to_csv(OUT / "corr_x_vs_base.csv", index=False)
    d[X_FEATURES].corr("spearman").to_csv(OUT / "corr_x_vs_x.csv")

    A = sm.add_constant(pd.concat([B.loc[:, B.std() > 0], d[X_FEATURES]], axis=1)).to_numpy()
    names = ["const"] + [c for c in B.columns if B[c].std() > 0] + X_FEATURES
    pd.DataFrame({"feature": names, "vif": [variance_inflation_factor(A, i) for i in range(A.shape[1])]}).iloc[1:].to_csv(
        OUT / "vif.csv", index=False)

    # lead/lag: x at week t against outbreak in week t + L, net of recent outbreaks, within state
    w = df.sort_values(["state", "day"]).copy()
    w["had"] = (np.expm1(w.log_outbreaks_7d) > 0).astype(float)
    rows = []
    for x in X_FEATURES:
        for lag in range(-4, 5):
            v = w.groupby("state").had.shift(-lag)
            s = pd.DataFrame({"x": w[x], "y": v, "c": w.groupby("state").log_outbreaks_60d.shift(max(lag, 0) + 1),
                              "state": w.state})[w.x_covered].dropna()
            s[["x", "y", "c"]] -= s.groupby("state")[["x", "y", "c"]].transform("mean")
            xr = s.x - np.polyval(np.polyfit(s.c, s.x, 1), s.c)
            yr = s.y - np.polyval(np.polyfit(s.c, s.y, 1), s.c)
            rows.append({"x": x, "week_of_outbreak_minus_x": lag, "partial_r": np.corrcoef(xr, yr)[0, 1], "rows": len(s)})
    pd.DataFrame(rows).to_csv(OUT / "lead_lag.csv", index=False)

    # event time: X around a state onset, the first outbreak week after 30 clean days
    w["onset"] = w.state_new_onset.shift(1).where(w.state == w.state.shift(1)).fillna(False).astype(bool) & (w.had == 1)
    zs = (w[X_FEATURES] - w.loc[w.x_covered, X_FEATURES].mean()) / w.loc[w.x_covered, X_FEATURES].std()
    rows = []
    for offset in range(-6, 5):
        # a row `offset` weeks from an onset is one whose onset flag sits `offset` rows earlier
        on = w.groupby("state").onset.shift(offset).fillna(False).astype(bool) & w.x_covered
        rows.append({"weeks_from_onset": offset, "rows": int(on.sum()), **zs[on].mean().to_dict()})
    pd.DataFrame(rows).to_csv(OUT / "event_time.csv", index=False)


def covered(df: pd.DataFrame, min_posts: int = 1) -> pd.DataFrame:
    return df[df.x_n_posts_7d >= min_posts].reset_index(drop=True)


def explore() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    posts = load_posts()
    posts = posts[posts.ts.dt.tz_convert(None) <= EXPLORE_END + pd.Timedelta(days=1)]
    m = estimate_pseudocount(posts)
    sw = load_state_week()
    assert sw.day.max() <= EXPLORE_END
    assert sw.loc[sw.day + pd.Timedelta(days=30) > EXPLORE_END, "y30"].isna().all()
    print(f"pseudocount {m:.2f}; state-weeks {len(sw):,}", flush=True)
    base = panel(posts, sw, m)
    rng = np.random.default_rng(SEED)
    lift, off, perm, dev, per_fold = [], [], [], [], []
    variants = {"primary": covered(base), "min5": covered(base, 5)}
    for lag in (3, 5):
        variants[f"embargo{lag}"] = covered(panel(posts, sw, m, lag))
    for k in KS:
        for name, df in variants.items():
            arms = ARMS if name == "primary" else {a: ARMS[a] for a in ("S3", "S4", "S4s", "S4v")}
            o = score_arms(df, k, explore_splits(df, k), arms)
            o.to_csv(OUT / f"oof_{name}_k{k}.csv", index=False)
            lift += lift_rows(o, k, N_BOOT_EXPLORE, 95.0, name)
            print(f"k={k} {name}: {len(o)} held-out rows, {int(o.y.sum())} positives", flush=True)
            if name != "primary":
                continue
            for fold, g in o.groupby("fold"):
                if 0 < g.y.sum() < len(g):
                    per_fold.append({"k": k, "fold": fold, "rows": len(g), "positives": int(g.y.sum()),
                                     **{a: average_precision_score(g.y, g[a]) for a in ARMS}})
            dev += deviance_split(o, k, name)
            rows = df[f"y{k}"].notna().to_numpy()
            off_s3 = cross_fit_s3(df, k, rows)
            off += offset_logit(df, k, rows, off_s3, name)
            r = residualized(df, k, explore_splits(df, k))
            lift += [dict(x, comparison="S4 residualized vs S3") for x in lift_rows(r, k, N_BOOT_EXPLORE, 95.0, "residualized")]
            f, tr, va = explore_splits(df, k)[-1]
            for sl in SLICES:
                perm.append({"k": k, "slice": sl, "fold": f, **permutation_test(df, k, tr, va, sl, rng)})
            # controls: a planted future-outcome column must win, shuffled X must not
            planted = df.assign(x_planted=df[f"y{k}"].fillna(0) + rng.normal(0, .3, len(df)))
            pl = score_arms(planted, k, explore_splits(planted, k), {"S3": S3, "S4": S3 + ["x_planted"]})
            shuf = df.copy()
            shuf[X_FEATURES] = df[X_FEATURES].to_numpy()[rng.permutation(len(df))]
            sh = score_arms(shuf, k, explore_splits(shuf, k), {"S3": S3, "S4": S3 + X_FEATURES})
            lift += lift_rows(pl, k, 100, 95.0, "control_planted") + lift_rows(sh, k, 100, 95.0, "control_shuffled")
    pd.DataFrame(lift).to_csv(OUT / "explore_lift.csv", index=False)
    pd.DataFrame(off).to_csv(OUT / "explore_offset.csv", index=False)
    pd.DataFrame(perm).to_csv(OUT / "explore_permutation.csv", index=False)
    pd.DataFrame(dev).to_csv(OUT / "explore_deviance.csv", index=False)
    pd.DataFrame(per_fold).to_csv(OUT / "explore_per_fold.csv", index=False)
    descriptives(base)
    show = ["variant", "k", "slice", "comparison", "rows", "positives", "pr_auc_ref", "pr_auc_new", "delta", "d_lo", "d_hi"]
    pd.set_option("display.width", 200)
    print(pd.DataFrame(lift)[show].round(4).to_string(index=False))


def require_frozen_spec() -> str:
    """The one-shot run is allowed only once the spec is committed. Returns its commit."""
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()  # noqa: E731
    commit = run("log", "-1", "--format=%H", "--", str(SPEC.relative_to(ROOT)))
    if not commit or run("status", "--porcelain", "--", str(SPEC.relative_to(ROOT))):
        raise SystemExit(f"{SPEC.name} must be committed and unmodified before the final run")
    return commit


def final(test_start: pd.Timestamp, end: pd.Timestamp, tag: str, commit: str, n_boot: int, n_perm: int) -> None:
    """The frozen Track A test. `tag` is "final", or "smoke" for a dry run on already-seen 2024."""
    posts = load_posts()
    posts = posts[posts.ts.dt.tz_convert(None) <= end + pd.Timedelta(days=1)]
    m = estimate_pseudocount(posts)
    assert abs(m - 10.58) < 0.01, m  # frozen: estimated through 2024
    sw = load_state_week(end)
    base = panel(posts, sw, m)
    variants = {"primary": covered(base), "min5": covered(base, 5)}
    for lag in (3, 5):
        variants[f"embargo{lag}"] = covered(panel(posts, sw, m, lag))
    rng = np.random.default_rng(SEED)
    q1_end = test_start + pd.Timedelta(days=85)
    lift, off, perm, dev, cov = [], [], [], [], []
    for k in KS:
        for name, df in variants.items():
            known = df[f"y{k}"].notna()
            train = ((df.day + pd.Timedelta(days=k) < test_start) & known).to_numpy()
            test = ((df.day >= test_start) & known).to_numpy()
            splits = [("test", train, test)]
            arms = FROZEN_ARMS if name == "primary" else {a: FROZEN_ARMS[a] for a in ("S3", "S4", "S4v", "S4s")}
            o = score_arms(df, k, splits, arms)
            o.to_csv(OUT / f"{tag}_oof_{name}_k{k}.csv", index=False)
            lift += lift_rows(o, k, n_boot, 95.0, name)
            print(f"k={k} {name}: train {train.sum()}, test {test.sum()}, positives {int(o.y.sum())}", flush=True)
            if name != "primary":
                continue
            lift += lift_rows(o[o.day <= q1_end], k, n_boot, 95.0, "first_quarter")
            lift += lift_rows(o[o.day > q1_end], k, n_boot, 95.0, "later_weeks")
            r = residualized(df, k, splits, FROZEN)
            lift += [dict(x, comparison="S4 residualized vs S3") for x in lift_rows(r, k, n_boot, 95.0, "residualized")]
            dev += deviance_split(o, k, name)
            off_s3 = np.full(len(df), np.nan)
            off_s3[test] = o.S3.to_numpy()
            off += offset_logit(df, k, test, off_s3, "test_rows", FROZEN)
            for sl in SLICES:
                perm.append({"k": k, "slice": sl, **permutation_test(df, k, train, test, sl, rng, n_perm, FROZEN)})
            all_test = base[(base.day >= test_start) & base[f"y{k}"].notna()]
            for sl, g in {"all": all_test, "state_new_onset": all_test[all_test.state_new_onset]}.items():
                pos = g[g[f"y{k}"] == 1]
                cov.append({"k": k, "slice": sl, "state_weeks": len(g), "covered_rows": int(g.x_covered.sum()),
                            "positive_state_weeks": len(pos), "positives_on_covered": int(pos.x_covered.sum()),
                            "share_positives_covered": pos.x_covered.mean()})
    for label, rows in {"lift": lift, "offset": off, "permutation": perm, "deviance": dev, "coverage": cov}.items():
        pd.DataFrame(rows).assign(spec_commit=commit).to_csv(OUT / f"{tag}_{label}.csv", index=False)
    show = ["variant", "k", "slice", "comparison", "rows", "positives", "pr_auc_ref", "pr_auc_new", "delta", "d_lo", "d_hi"]
    pd.set_option("display.width", 200)
    print(pd.DataFrame(lift)[show].round(4).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["explore", "smoke", "final"])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.what == "explore":
        explore()
    elif a.what == "smoke":
        final(pd.Timestamp("2024-01-07"), EXPLORE_END, "smoke", "smoke", 100, 20)
    else:
        commit = require_frozen_spec()
        if (OUT / "final_lift.csv").exists():
            raise SystemExit("the one-shot run has already been scored; refusing to run it again")
        last = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
        final(TEST_FIRST, last, "final", commit, N_BOOT_FINAL, N_PERM)
