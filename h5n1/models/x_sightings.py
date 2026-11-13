"""Track B of the X utility analysis: given a sighting, how likely is an outbreak in that state in
the next k days? Triggered: the sighting model runs only when a sighting is posted and never trains
on non-post rows. Design in docs/x_utility_spec.md.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_sightings explore
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_sightings smoke    # 2024 dry run
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_sightings final    # once, spec committed

Every row has a sighting, so the model cannot show what a sighting itself adds. That comes from
observed vs expected: S3 is trained separately on all state-weeks and used only as a reference for
the risk already present in those weeks.

  trigger rate    share of sightings followed by an outbreak, and share of outbreaks flagged
  O/E             outbreaks after sightings / summed calibrated S3 risk for those weeks
  O/E ratio       that O/E / the O/E of non-sighting weeks, in the same period. S3's overall
                  miscalibration appears in both, so the ratio cancels it. Central result.
  matched         the same state at the same time of year (+/- 15 days of year) in other years,
                  drawn at random: the rate a sighting would show by season and place alone
  logistic        logit(S3 risk), post volume, post sentiment, on sighting rows: which sightings
                  matter more. Small on purpose; about 181 rows cannot support a forest.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.linear_model import LogisticRegression

from h5n1.features.x_state import STREAMS, build_features, estimate_pseudocount, load_posts, sighting_rows
from h5n1.db import get_engine
from h5n1.models.x_baselines import EXPLORE_END, KS, OUT, S3, fit_predict, load_state_week
from h5n1.models.x_utility import TEST_FIRST, cross_fit_s3, design, logit, require_frozen_spec, season
from h5n1.sources.x_analysis_table import sightings

SEED = 20260728
N_BOOT, N_MATCH, MATCH_DOY = 2000, 2000, 15
SLICES = ("all", "state_new_onset")


def calibrate(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Platt scaling of cross-fitted S3 scores, on the rows that have an outcome."""
    known = ~np.isnan(y) & ~np.isnan(p)
    lr = LogisticRegression(C=1e6).fit(logit(p[known]).reshape(-1, 1), y[known])
    out = np.full(len(p), np.nan)
    has = ~np.isnan(p)
    out[has] = lr.predict_proba(logit(p[has]).reshape(-1, 1))[:, 1]
    return out


def oe(y: np.ndarray, e: np.ndarray) -> float:
    return y.sum() / e.sum() if e.sum() > 0 else np.nan


def oe_ratio(d: pd.DataFrame) -> dict:
    """d has y, e, sighted, week. Point estimates plus a week-block bootstrap of the ratio."""
    s, n = d[d.sighted], d[~d.sighted]
    ratio = oe(s.y.to_numpy(), s.e.to_numpy()) / oe(n.y.to_numpy(), n.e.to_numpy())
    weeks = d.week.unique()
    by_week = {w: g[["y", "e", "sighted"]].to_numpy(dtype=float) for w, g in d.groupby("week")}
    rng = np.random.default_rng(SEED)
    boot = []
    for _ in range(N_BOOT):
        a = np.concatenate([by_week[w] for w in rng.choice(weeks, len(weeks))])
        hit, miss = a[a[:, 2] == 1], a[a[:, 2] == 0]
        if len(hit) and hit[:, 1].sum() and miss[:, 1].sum() and miss[:, 0].sum():
            boot.append(oe(hit[:, 0], hit[:, 1]) / oe(miss[:, 0], miss[:, 1]))
    lo, hi = np.percentile(boot, [2.5, 97.5]) if boot else (np.nan, np.nan)
    return {"sightings": len(s), "followed": int(s.y.sum()), "trigger_rate": s.y.mean(), "expected": s.e.sum(),
            "oe_sighted": oe(s.y.to_numpy(), s.e.to_numpy()), "oe_other": oe(n.y.to_numpy(), n.e.to_numpy()),
            "oe_ratio": ratio, "ratio_lo": lo, "ratio_hi": hi, "boot_reps": len(boot),
            "P(ratio>1)": float(np.mean(np.array(boot) > 1)) if boot else np.nan}


def matched_rate(d: pd.DataFrame, rng, among=None) -> dict:
    """Mean outcome of random same-state, same-season-of-year weeks in other years, N_MATCH times.
    `among` limits which sightings are matched; the pool is always all of d."""
    s = d[d.sighted if among is None else d.sighted & among]
    doy = d.day.dt.dayofyear.to_numpy()
    pools = []
    for _, r in s.iterrows():
        gap = np.abs(doy - r.day.dayofyear)
        gap = np.minimum(gap, 365 - gap)
        pool = np.flatnonzero((d.state == r.state).to_numpy() & (gap <= MATCH_DOY)
                              & (d.day.dt.year != r.day.year).to_numpy() & ~d.sighted.to_numpy())
        if len(pool):
            pools.append(pool)
    y = d.y.to_numpy()
    sims = np.array([np.mean([y[rng.choice(p)] for p in pools]) for _ in range(N_MATCH)])
    obs = s.y.mean()
    return {"matched_pools": len(pools), "matched_rate": sims.mean(),
            "p_matched": ((sims >= obs).sum() + 1) / (N_MATCH + 1)}


def recall(d: pd.DataFrame) -> dict:
    """Of the state-weeks followed by an outbreak, the share that had a sighting."""
    pos = d[d.y == 1]
    return {"positive_state_weeks": len(pos), "share_flagged": pos.sighted.mean()}


def sighting_panel(end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """State-weeks to `end` with `sighted`, the post's sentiment and state volume, plus the sightings."""
    ev = sighting_rows(sightings(STREAMS))
    ev = ev[ev.day <= end]
    posts = load_posts()
    posts = posts[posts.ts.dt.tz_convert(None) <= end + pd.Timedelta(days=1)]
    sw = load_state_week(end)
    f = build_features(posts, sorted(sw.state.unique()), pd.DatetimeIndex(sorted(sw.day.unique())),
                       estimate_pseudocount(posts))
    sw = sw.merge(f[["state", "day", "log_x_posts_7d"]], on=["state", "day"], how="left")
    sw = sw.merge(ev.merge(posts[["_id", "sentiment"]].drop_duplicates("_id"), on="_id", how="left")[
        ["state", "day", "sentiment"]], on=["state", "day"], how="left")
    sw["sighted"] = sw.state.str.cat(sw.day.astype(str)).isin(ev.state.str.cat(ev.day.astype(str)))
    return sw, ev


def gap_target(sw: pd.DataFrame, k: int, gap: int) -> np.ndarray:
    """y over (d + gap, d + k]: the false-lead version, keeping NaN where the outcome is unknown."""
    o = pd.read_sql("SELECT c.state, o.day AS hit FROM fact_h5n1_outbreak o JOIN dim_county c USING (fips)",
                    get_engine(), parse_dates=["hit"])
    h = sw[["state", "day"]].merge(o, on="state")
    h = h[(h.hit > h.day + pd.Timedelta(days=gap)) & (h.hit <= h.day + pd.Timedelta(days=k))]
    h = h.drop_duplicates(["state", "day"])[["state", "day"]].assign(h=1.0)
    y = sw[["state", "day"]].merge(h, on=["state", "day"], how="left").h.fillna(0).to_numpy()
    return np.where(sw[f"y{k}"].notna(), y, np.nan)


def expected_risk(t: pd.DataFrame, k: int, test_start: pd.Timestamp) -> np.ndarray:
    """Calibrated S3 risk for test rows. S3 and its Platt map are fit on training rows only, the map
    on leave-one-season-out scores. Uses all state-weeks: non-post rows serve only as this reference."""
    known = t[f"y{k}"].notna().to_numpy()
    train = known & (t.day + pd.Timedelta(days=k) < test_start).to_numpy()
    test = known & (t.day >= test_start).to_numpy()
    cf = cross_fit_s3(t, k, train)
    lr = LogisticRegression(C=1e6).fit(logit(cf[train]).reshape(-1, 1), t.loc[train, f"y{k}"])
    X = design(t, S3, train)
    out = np.full(len(t), np.nan)
    p = fit_predict(X[train], t.loc[train, f"y{k}"], X[test])
    out[test] = lr.predict_proba(logit(p).reshape(-1, 1))[:, 1]
    return out


def final(test_start: pd.Timestamp, end: pd.Timestamp, tag: str, commit: str) -> None:
    """The frozen Track B test. `tag` is "final", or "smoke" for a dry run on already-seen 2024."""
    sw, _ = sighting_panel(end)
    rng = np.random.default_rng(SEED)
    q1_end = test_start + pd.Timedelta(days=85)
    rows, coefs = [], []
    for k, gap in [(7, 0), (14, 0), (14, 5), (30, 0)]:
        t = sw.copy()
        if gap:
            t[f"y{k}"] = gap_target(sw, k, gap)
        t["e"] = expected_risk(t, k, test_start)
        t["y"] = t[f"y{k}"]
        pool = t[t.y.notna()]
        d = t[t.e.notna()].assign(week=lambda x: x.day.dt.to_period("W").astype(str))
        in_test = pool.day >= test_start
        parts = {("all", "all"): (d, in_test),
                 ("all", "first_quarter"): (d[d.day <= q1_end], in_test & (pool.day <= q1_end)),
                 ("all", "later_weeks"): (d[d.day > q1_end], in_test & (pool.day > q1_end)),
                 ("state_new_onset", "all"): (d[d.state_new_onset], in_test & pool.state_new_onset)}
        for (sl, period), (g, among) in parts.items():
            if g.sighted.any():
                rows.append({"k": k, "gap_days": gap, "slice": sl, "period": period, "state_weeks": len(g),
                             "base_rate": g.y.mean(), **oe_ratio(g), **matched_rate(pool, rng, among), **recall(g)})
        print(f"k={k} gap={gap} done", flush=True)
        if gap == 0:
            s = d[d.sighted & d.sentiment.notna()].reset_index(drop=True)
            z = lambda v: (v - v.mean()) / v.std()  # noqa: E731
            Xs = sm.add_constant(pd.DataFrame({"logit_s3": logit(s.e.to_numpy()), "post_volume": z(s.log_x_posts_7d),
                                               "post_sentiment": z(s.sentiment)}))
            fit = sm.GLM(s.y, Xs, family=sm.families.Binomial()).fit(
                cov_type="cluster", cov_kwds={"groups": pd.factorize(season(s.day).astype(str) + s.state)[0]})
            coefs += [{"k": k, "term": c, "beta": fit.params[c], "se": fit.bse[c], "p": fit.pvalues[c], "rows": len(s)}
                      for c in Xs.columns]
    res = pd.DataFrame(rows).assign(spec_commit=commit)
    res.to_csv(OUT / f"{tag}_sightings.csv", index=False)
    pd.DataFrame(coefs).assign(spec_commit=commit).to_csv(OUT / f"{tag}_sightings_logistic.csv", index=False)
    pd.set_option("display.width", 240, "display.max_columns", 40)
    print(res.drop(columns="spec_commit").round(3).to_string(index=False))
    print(pd.DataFrame(coefs).round(3).to_string(index=False))


def explore() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sw, ev = sighting_panel(EXPLORE_END)
    print(f"sightings {len(ev)}, on state-week rows {int(sw.sighted.sum())}", flush=True)
    rng = np.random.default_rng(SEED)
    rows, coefs = [], []
    for k in KS:
        known = sw[f"y{k}"].notna().to_numpy()
        p = calibrate(cross_fit_s3(sw, k, known), sw[f"y{k}"].to_numpy())
        d = sw[known].assign(y=sw.loc[known, f"y{k}"].to_numpy(), e=p[known],
                             week=lambda x: x.day.dt.to_period("W").astype(str))
        for sl in SLICES:
            g = d if sl == "all" else d[d.state_new_onset]
            rows.append({"k": k, "slice": sl, "state_weeks": len(g), "base_rate": g.y.mean(),
                         **oe_ratio(g), **matched_rate(g, rng), **recall(g)})
        s = d[d.sighted & d.sentiment.notna()].reset_index(drop=True)
        z = lambda v: (v - v.mean()) / v.std()  # noqa: E731
        Xs = sm.add_constant(pd.DataFrame({"logit_s3": logit(s.e.to_numpy()), "post_volume": z(s.log_x_posts_7d),
                                           "post_sentiment": z(s.sentiment)}))
        fit = sm.GLM(s.y, Xs, family=sm.families.Binomial()).fit(
            cov_type="cluster", cov_kwds={"groups": pd.factorize(season(s.day).astype(str) + s.state)[0]})
        coefs += [{"k": k, "term": t, "beta": fit.params[t], "se": fit.bse[t], "p": fit.pvalues[t], "rows": len(s)}
                  for t in Xs.columns]
        print(f"k={k} done", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "sightings_explore.csv", index=False)
    pd.DataFrame(coefs).to_csv(OUT / "sightings_logistic_explore.csv", index=False)
    pd.set_option("display.width", 220)
    print(res.round(3).to_string(index=False))
    print(pd.DataFrame(coefs).round(3).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["explore", "smoke", "final"])
    a = ap.parse_args()
    if a.what == "explore":
        explore()
    elif a.what == "smoke":
        final(pd.Timestamp("2024-01-07"), EXPLORE_END, "smoke", "smoke")
    else:
        commit = require_frozen_spec()
        if (OUT / "final_sightings.csv").exists():
            raise SystemExit("the one-shot run has already been scored; refusing to run it again")
        last = pd.read_sql("SELECT max(day) AS d FROM fact_h5n1_outbreak", get_engine(), parse_dates=["d"]).d.iloc[0]
        final(TEST_FIRST, last, "final", commit)
