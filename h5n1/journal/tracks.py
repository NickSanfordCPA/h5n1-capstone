"""Track A (sentiment and volume) and Track B (sightings) on both platforms with one code
path (protocol section 8). State-week grain, week ending Sunday.

    uv run --extra modeling python -m h5n1.journal.tracks a [--smoke]
    uv run --extra modeling python -m h5n1.journal.tracks b [--smoke]

Track A. Per platform, three post features from the sentiment set (share_7d, sent_7d,
sent_chg; h5n1/features/x_state.build_features, pseudocount estimated through 2024 without
outcomes). Covered state-weeks only, the same rows in every arm. Primary: S4 - S3 PR-AUC,
k = 14, new onset, paired week-block bootstrap.

Track B. One sighting per platform state-week (journal.post_funnel, step 0). S3 is fitted on
all development state-weeks and Platt-calibrated on leave-one-season-out scores; it supplies
the expected risk only. Primary: the O/E ratio at k = 14, sighted against non-sighted
state-weeks of the test period, week-block bootstrap.

Every model uses the searched baseline setting (data/journal/models/chosen_state.json) and
five seeds. `--smoke` runs few bootstrap replicates into models/smoke/ to test the code.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import average_precision_score
from sqlalchemy import text

from h5n1.features.x_state import build_features, estimate_pseudocount
from h5n1.journal import models as M
from h5n1.journal.localdb import local_engine

PLATFORMS = ("meta", "x")
POSTS = ["share_7d", "sent_7d", "sent_chg"]
VOLUME, SENT = ["share_7d"], ["sent_7d", "sent_chg"]
ARMS = {"S0": M.S0, "S1": POSTS, "S2": M.S0 + POSTS, "S3": M.S3, "S4": M.S3 + POSTS,
        "S4v": M.S3 + VOLUME, "S4s": M.S3 + SENT}
COMPARISONS = [("S4", "S3"), ("S2", "S0"), ("S4v", "S3"), ("S4s", "S3")]
FIT_END = "2024-12-31"
Q1_END = M.TEST_FIRST + pd.Timedelta(days=85)
N_PERM = 300
MATCH_DOY, N_MATCH = 15, 2000
SEED = 20261118
CFG = {"n_boot": M.N_BOOT, "n_perm": N_PERM, "out": M.OUT}


# ---------------------------------------------------------------- shared

def season(day: pd.Series) -> pd.Series:
    """July-June outbreak season, labelled by its start year."""
    return day.dt.year - (day.dt.month < 7).astype(int)


def logit(p):
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def design(df: pd.DataFrame, cols: list[str], train: np.ndarray) -> pd.DataFrame:
    base = [c for c in cols if c not in POSTS]
    X = M.prepare(df, base, train) if base else pd.DataFrame(index=df.index)
    out = pd.concat([X, df[[c for c in cols if c in POSTS]]], axis=1)[cols]
    assert not out.isna().any().any()
    return out


def sentiment_posts(platform: str) -> pd.DataFrame:
    """The platform's sentiment-set posts, at the reader's state, with the XLM-R score."""
    q = text("SELECT f.post_id, f.state, f.created_at AS ts, s.sentiment "
             "FROM journal.post_funnel f JOIN journal.post_sentiment s USING (platform, post_id) "
             "WHERE f.sentiment_set AND f.platform = :p")
    with local_engine().connect() as cn:
        d = pd.read_sql(q, cn, params={"p": platform})
    d["ts"] = pd.to_datetime(d["ts"], utc=True)
    return d.assign(account_type="", is_negative=False)


def post_features(posts: pd.DataFrame, sw: pd.DataFrame, m: float, end_lag: int = 0) -> pd.DataFrame:
    states = sorted(sw.state.unique())
    sundays = pd.DatetimeIndex(sorted(sw.day.unique()))
    f = build_features(posts, states, sundays, m, end_lag)
    f = f.rename(columns={"x_n_posts_7d": "n_posts_7d", "x_share_7d": "share_7d",
                          "x_sent_7d": "sent_7d", "x_sent_chg": "sent_chg"})
    return f[["state", "day", "n_posts_7d"] + POSTS]


def two_way(d: pd.DataFrame) -> np.ndarray:
    return np.column_stack([pd.factorize(d.state)[0], pd.factorize(d.day)[0]])


# ---------------------------------------------------------------- Track A

def slice_masks(d: pd.DataFrame) -> dict[str, np.ndarray]:
    return {"all": np.ones(len(d), bool), "new_onset": d.new_onset.to_numpy(bool)}


def lift_rows(o: pd.DataFrame, preds: dict[str, np.ndarray], base: dict) -> list[dict]:
    """o: the test rows (state, day, new_onset, y). preds[arm]: mean-of-seeds prediction."""
    wk = M.week_of(o.day)
    y_all = o.y.to_numpy()
    rows = []
    for sl, m in slice_masks(o).items():
        y = y_all[m]
        if y.sum() == 0 or y.sum() == len(y):
            continue
        ap = {a: average_precision_score(y, p[m]) for a, p in preds.items()}
        for new, ref in COMPARISONS:
            if new not in preds or ref not in preds:
                continue
            lo, hi, pgt, reps = M.paired_boot(y, preds[new][m], preds[ref][m], wk[m], CFG["n_boot"])
            rows.append({**base, "slice": sl, "comparison": f"{new} - {ref}", "rows": int(m.sum()),
                         "events": int(y.sum()), "weeks": len(np.unique(wk[m])),
                         "base_rate": float(y.mean()), "pr_auc_ref": ap[ref], "pr_auc_new": ap[new],
                         "delta": ap[new] - ap[ref], "lo": lo, "hi": hi, "P(d>0)": pgt,
                         "boot_reps": reps})
    return rows


def offset_logit(d: pd.DataFrame, off: np.ndarray, base: dict) -> list[dict]:
    """Each post feature alone and all jointly, per SD, on the test rows, with the S3 test
    prediction as offset; standard errors clustered on state and week."""
    out = []
    for sl, m in slice_masks(d).items():
        g = d[m]
        if g.y.sum() < 5:
            continue
        z = (g[POSTS] - g[POSTS].mean()) / g[POSTS].std()
        for name, cols in [(c, [c]) for c in POSTS] + [("joint", POSTS)]:
            fit = sm.GLM(g.y, sm.add_constant(z[cols]), family=sm.families.Binomial(),
                         offset=logit(off[m])).fit(cov_type="cluster", cov_kwds={"groups": two_way(g)})
            for c in cols:
                out.append({**base, "slice": sl, "model": name, "term": c, "beta_per_sd": fit.params[c],
                            "se": fit.bse[c], "p": fit.pvalues[c], "rows": len(g), "events": int(g.y.sum())})
            if name == "joint":
                w = fit.wald_test(np.eye(len(fit.params))[1:], scalar=True)
                out.append({**base, "slice": sl, "model": "joint", "term": "wald_all", "p": float(w.pvalue),
                            "rows": len(g), "events": int(g.y.sum())})
    return out


def permutation(df: pd.DataFrame, k: int, train: np.ndarray, test: np.ndarray, params: dict,
                base: dict) -> list[dict]:
    """Fit S4 once, then shuffle the post block across weeks within state x season on the
    test rows. A free shuffle would break seasonality and overstate significance."""
    rng = np.random.default_rng(SEED)
    X = design(df, ARMS["S4"], train)
    rfs = [RandomForestClassifier(random_state=s, n_jobs=12, **params).fit(X[train], df.loc[train, f"y{k}"])
           for s in M.SEEDS]
    predict = lambda T: np.mean([r.predict_proba(T)[:, 1] for r in rfs], axis=0)  # noqa: E731
    Xv, d = X[test].reset_index(drop=True), df[test].reset_index(drop=True)
    groups = [g.index.to_numpy() for _, g in d.groupby([d.state, season(d.day)])]
    xv = Xv[POSTS].to_numpy()
    obs_p = predict(Xv)
    null_p = []
    for _ in range(CFG["n_perm"]):
        order = np.arange(len(d))
        for g in groups:
            order[g] = rng.permutation(g)
        T = Xv.copy()
        T[POSTS] = xv[order]
        null_p.append(predict(T))
    y = d[f"y{k}"].to_numpy()
    out = []
    for sl, m in slice_masks(d).items():
        if y[m].sum() == 0:
            continue
        obs = average_precision_score(y[m], obs_p[m])
        null = np.array([average_precision_score(y[m], p[m]) for p in null_p])
        out.append({**base, "slice": sl, "observed": obs, "null_mean": null.mean(),
                    "gain": obs - null.mean(), "p": ((null >= obs).sum() + 1) / (len(null) + 1),
                    "perms": len(null)})
    return out


def track_a() -> None:
    out = CFG["out"]
    out.mkdir(parents=True, exist_ok=True)
    params = M.chosen_params("state")
    sw = M.load_state_week("detected")
    lift, arms_tab, off, perm, cov, rel, meta = [], [], [], [], [], [], []
    for plat in PLATFORMS:
        posts = sentiment_posts(plat)
        m = estimate_pseudocount(posts, FIT_END)
        meta.append({"platform": plat, "sentiment_posts": len(posts), "pseudocount": m})
        print(f"{plat}: {len(posts):,} sentiment-set posts, pseudocount {m:.2f}", flush=True)
        panels = {lag: sw.merge(post_features(posts, sw, m, lag), on=["state", "day"], how="left")
                  for lag in (0, 3, 5)}
        variants = {"primary": (0, 1), "min5": (0, 5), "embargo3": (3, 1), "embargo5": (5, 1)}
        for k in M.KS:
            for name, (lag, min_posts) in variants.items():
                df = panels[lag]
                df = df[df.n_posts_7d >= min_posts].reset_index(drop=True)
                train, test = M.dev_masks(df, k)
                use = ARMS if name == "primary" else {a: ARMS[a] for a in ("S3", "S4", "S4v", "S4s")}
                o = df.loc[test, ["state", "day", "new_onset"]].assign(y=df.loc[test, f"y{k}"].to_numpy())
                o = o.reset_index(drop=True)
                seeds, preds = {}, {}
                for arm, cols in use.items():
                    X = design(df, cols, train)
                    seeds[arm] = M.fit_predict(X[train], df.loc[train, f"y{k}"], X[test], params)
                    preds[arm] = seeds[arm].mean(axis=0)
                    o[arm] = preds[arm]
                o.to_csv(out / f"track_a_oof_{plat}_{name}_k{k}.csv", index=False)
                base = {"platform": plat, "variant": name, "k": k, "train_rows": int(train.sum()),
                        "train_events": int(df.loc[train, f"y{k}"].sum())}
                lift += [{**r, "period": "test"} for r in lift_rows(o, preds, base)]
                print(f"{plat} k={k} {name}: train {train.sum()}, test {test.sum()}, "
                      f"events {int(o.y.sum())}", flush=True)
                if name != "primary":
                    continue
                for per, pm in (("first_quarter", (o.day <= Q1_END).to_numpy()),
                                ("later_weeks", (o.day > Q1_END).to_numpy())):
                    lift += [{**r, "period": per} for r in
                             lift_rows(o[pm].reset_index(drop=True), {a: p[pm] for a, p in preds.items()}, base)]
                y = o.y.to_numpy()
                for sl, sm_ in slice_masks(o).items():
                    for arm in use:
                        arms_tab.append({**base, "slice": sl, "arm": arm,
                                         **M.score_row(y[sm_], seeds[arm][:, sm_])})
                for arm in ("S3", "S4"):
                    rel.append(M.reliability(y, preds[arm]).assign(platform=plat, k=k, arm=arm))
                off += offset_logit(df[test].reset_index(drop=True).assign(y=y), preds["S3"], base)
                # residualised: S3 plus the part of each post feature S3's columns cannot explain
                X3 = design(df, M.S3, train)
                r = pd.DataFrame({c: df[c] - LinearRegression().fit(X3[train], df.loc[train, c]).predict(X3)
                                  for c in POSTS}, index=df.index)
                XR = pd.concat([X3, r], axis=1)
                pr = M.fit_predict(XR[train], df.loc[train, f"y{k}"], XR[test], params).mean(axis=0)
                lift += [{**x, "variant": "residualised", "period": "test"} for x in
                         lift_rows(o, {"S3": preds["S3"], "S4": pr}, base)]
                if k == 14:
                    perm += permutation(df, k, train, test, params, base)
                full = panels[0]
                _, t_all = M.dev_masks(full, k)
                g = full[t_all]
                for sl, gg in (("all", g), ("new_onset", g[g.new_onset])):
                    pos = gg[gg[f"y{k}"] == 1]
                    cov.append({"platform": plat, "k": k, "slice": sl, "state_weeks": len(gg),
                                "covered": int((gg.n_posts_7d >= 1).sum()),
                                "covered_5": int((gg.n_posts_7d >= 5).sum()),
                                "event_state_weeks": len(pos),
                                "events_on_covered": int((pos.n_posts_7d >= 1).sum())})
    for label, rows in {"lift": lift, "arms": arms_tab, "offset": off, "permutation": perm,
                        "coverage": cov, "meta": meta}.items():
        pd.DataFrame(rows).to_csv(out / f"track_a_{label}.csv", index=False)
    pd.concat(rel).to_csv(out / "track_a_reliability.csv", index=False)
    L = pd.DataFrame(lift)
    pd.set_option("display.width", 250)
    show = ["platform", "variant", "period", "k", "slice", "comparison", "rows", "events",
            "pr_auc_ref", "pr_auc_new", "delta", "lo", "hi"]
    print(L[(L.comparison == "S4 - S3")][show].round(4).to_string(index=False))


# ---------------------------------------------------------------- Track B

def sightings(platform: str) -> pd.DataFrame:
    q = text("SELECT state, week AS day, post_id, created_at, content_class, n_sighting_posts, n_places "
             "FROM journal.post_funnel WHERE step = 0 AND platform = :p")
    with local_engine().connect() as cn:
        return pd.read_sql(q, cn, params={"p": platform}, parse_dates=["day"])


def gap_target(sw: pd.DataFrame, k: int, gap: int) -> np.ndarray:
    """Outbreak in the state in (d + gap, d + k]; NaN where the k-day window is not complete."""
    with local_engine().connect() as cn:
        o = pd.read_sql(text("SELECT c.state, o.day AS hit FROM fact_h5n1_outbreak o "
                             "JOIN dim_county c USING (fips)"), cn, parse_dates=["hit"])
    h = sw[["state", "day"]].merge(o, on="state")
    h = h[(h.hit > h.day + pd.Timedelta(days=gap)) & (h.hit <= h.day + pd.Timedelta(days=k))]
    h = h.drop_duplicates(["state", "day"])[["state", "day"]].assign(h=1.0)
    y = sw[["state", "day"]].merge(h, on=["state", "day"], how="left").h.fillna(0).to_numpy()
    return np.where(sw[f"y{k}"].notna(), y, np.nan)


def expected_risk(t: pd.DataFrame, ycol: str, k: int, params: dict) -> tuple[np.ndarray, np.ndarray]:
    """Calibrated S3 risk. Returns (test, development): the test risk comes from S3 fitted on
    every development row and a Platt map fitted on leave-one-season-out development scores;
    the development risk is those cross-fitted scores through the same map."""
    known = t[ycol].notna().to_numpy()
    train = known & (t.day + pd.Timedelta(days=k) <= M.DEV_END).to_numpy()
    test = known & (t.day >= M.TEST_FIRST).to_numpy()
    s = season(t.day).to_numpy()
    cf = np.full(len(t), np.nan)
    for g in np.unique(s[train]):
        tr, te = train & (s != g), train & (s == g)
        X = M.prepare(t, M.S3, tr)
        cf[te] = M.fit_predict(X[tr], t.loc[tr, ycol], X[te], params).mean(axis=0)
    lr = LogisticRegression(C=1e6).fit(logit(cf[train]).reshape(-1, 1), t.loc[train, ycol])
    X = M.prepare(t, M.S3, train)
    e_test, e_dev = np.full(len(t), np.nan), np.full(len(t), np.nan)
    p = M.fit_predict(X[train], t.loc[train, ycol], X[test], params).mean(axis=0)
    e_test[test] = lr.predict_proba(logit(p).reshape(-1, 1))[:, 1]
    e_dev[train] = lr.predict_proba(logit(cf[train]).reshape(-1, 1))[:, 1]
    return e_test, e_dev


def oe(y: np.ndarray, e: np.ndarray) -> float:
    return y.sum() / e.sum() if e.sum() > 0 else np.nan


def oe_ratio(d: pd.DataFrame) -> dict:
    """d has y, e, sighted, day. Point estimates and a week-block bootstrap of the ratio."""
    s, n = d[d.sighted], d[~d.sighted]
    ratio = oe(s.y.to_numpy(), s.e.to_numpy()) / oe(n.y.to_numpy(), n.e.to_numpy())
    wk = M.week_of(d.day)
    arr = d[["y", "e", "sighted"]].to_numpy(dtype=float)
    weeks = np.unique(wk)
    by_week = {w: arr[wk == w] for w in weeks}
    rng = np.random.default_rng(M.BOOT_SEED)
    boot = []
    for _ in range(CFG["n_boot"]):
        a = np.concatenate([by_week[w] for w in rng.choice(weeks, len(weeks))])
        hit, miss = a[a[:, 2] == 1], a[a[:, 2] == 0]
        if len(hit) and hit[:, 1].sum() and miss[:, 1].sum() and miss[:, 0].sum():
            boot.append(oe(hit[:, 0], hit[:, 1]) / oe(miss[:, 0], miss[:, 1]))
    lo, hi = np.percentile(boot, [2.5, 97.5]) if boot else (np.nan, np.nan)
    pos = d[d.y == 1]
    return {"state_weeks": len(d), "base_rate": float(d.y.mean()), "sightings": len(s),
            "followed": int(s.y.sum()), "trigger_rate": float(s.y.mean()) if len(s) else np.nan,
            "expected": float(s.e.sum()), "oe_sighted": oe(s.y.to_numpy(), s.e.to_numpy()),
            "oe_other": oe(n.y.to_numpy(), n.e.to_numpy()), "oe_ratio": ratio, "lo": lo, "hi": hi,
            "boot_reps": len(boot), "P(ratio>1)": float(np.mean(np.array(boot) > 1)) if boot else np.nan,
            "event_state_weeks": len(pos), "share_flagged": float(pos.sighted.mean()) if len(pos) else np.nan}


def matched_rate(pool: pd.DataFrame, among: np.ndarray) -> dict:
    """Mean outcome of random weeks of the same state and time of year (+/- 15 days) in other
    years with no sighting: the rate a sighting would show by season and place alone."""
    rng = np.random.default_rng(SEED)
    s = pool[pool.sighted.to_numpy() & among]
    doy, y = pool.day.dt.dayofyear.to_numpy(), pool.y.to_numpy()
    state, year, sighted = pool.state.to_numpy(), pool.day.dt.year.to_numpy(), pool.sighted.to_numpy()
    pools = []
    for r in s.itertuples():
        gap = np.abs(doy - r.day.dayofyear)
        gap = np.minimum(gap, 365 - gap)
        c = np.flatnonzero((state == r.state) & (gap <= MATCH_DOY) & (year != r.day.year) & ~sighted)
        if len(c):
            pools.append(c)
    if not pools:
        return {"matched_pools": 0, "matched_rate": np.nan, "p_matched": np.nan}
    sims = np.array([np.mean([y[rng.choice(c)] for c in pools]) for _ in range(N_MATCH)])
    return {"matched_pools": len(pools), "matched_rate": float(sims.mean()),
            "p_matched": float(((sims >= s.y.mean()).sum() + 1) / (N_MATCH + 1))}


def track_b() -> None:
    out = CFG["out"]
    out.mkdir(parents=True, exist_ok=True)
    params = M.chosen_params("state")
    sw = M.load_state_week("detected")
    key = sw.state.str.cat(sw.day.dt.strftime("%Y-%m-%d"))
    for plat in PLATFORMS:
        s = sightings(plat)
        on_grid = s.state.str.cat(s.day.dt.strftime("%Y-%m-%d")).isin(key)
        print(f"{plat}: {len(s)} sightings, {int(on_grid.sum())} on the state-week grid", flush=True)
        s = s.rename(columns={"n_sighting_posts": f"n_posts_{plat}", "n_places": f"n_places_{plat}",
                              "content_class": f"class_{plat}", "post_id": f"post_id_{plat}",
                              "created_at": f"posted_{plat}"})
        sw = sw.merge(s, on=["state", "day"], how="left", validate="1:1")
        sw[f"sighted_{plat}"] = sw[f"n_posts_{plat}"].notna()
    rows, dose, trend, panel = [], [], [], []
    for k, gap in [(14, 0), (14, 5), (14, 3), (7, 0), (28, 0)]:
        t = sw.copy()
        y_gap = gap_target(sw, k, gap)
        if gap == 0:
            assert np.array_equal(np.nan_to_num(y_gap, nan=-1), np.nan_to_num(sw[f"y{k}"].to_numpy(), nan=-1)), \
                "the outbreak table does not reproduce the state-week target"
        t["y"] = y_gap
        t["e_test"], t["e_dev"] = expected_risk(t, "y", k, params)
        keep = ["state", "day", "new_onset", "y", "e_test", "e_dev"] + [
            c for c in t.columns if c.endswith(("_meta", "_x"))]
        panel.append(t[keep].assign(k=k, gap=gap))
        for plat in PLATFORMS:
            t["sighted"] = t[f"sighted_{plat}"]
            pool = t[t.y.notna()].reset_index(drop=True)
            for period, ecol in (("test", "e_test"), ("development", "e_dev")):
                d = t[t[ecol].notna()].assign(e=lambda x: x[ecol]).reset_index(drop=True)
                in_period = pool[ecol].notna().to_numpy()
                parts = {("all", period): (d, in_period)}
                if period == "test":
                    parts[("all", "first_quarter")] = (d[d.day <= Q1_END], in_period & (pool.day <= Q1_END).to_numpy())
                    parts[("all", "later_weeks")] = (d[d.day > Q1_END], in_period & (pool.day > Q1_END).to_numpy())
                parts[("new_onset", period)] = (d[d.new_onset], in_period & pool.new_onset.to_numpy(bool))
                for (sl, per), (g, among) in parts.items():
                    if g.sighted.any():
                        rows.append({"platform": plat, "k": k, "gap_days": gap, "slice": sl, "period": per,
                                     **oe_ratio(g), **matched_rate(pool, among)})
                if period != "test" or gap != 0:
                    continue
                non = d[~d.sighted]
                bins = {f"n_posts_{plat}": [("1", 1, 1), ("2-3", 2, 3), ("4+", 4, 10**9)],
                        f"n_places_{plat}": [("1", 1, 1), ("2", 2, 2), ("3+", 3, 10**9)]}
                for col, groups in bins.items():
                    for lab, lo_, hi_ in groups:
                        g = pd.concat([d[d.sighted & d[col].between(lo_, hi_)], non])
                        if g.sighted.any():
                            dose.append({"platform": plat, "k": k, "count_of": col.rsplit("_", 1)[0],
                                         "bin": lab, **oe_ratio(g)})
                    sd = d[d.sighted]
                    if sd.y.sum() >= 5 and sd[col].nunique() > 1:
                        fit = sm.GLM(sd.y, sm.add_constant(np.log1p(sd[col].astype(float)).rename("log1p_count")),
                                     family=sm.families.Binomial(), offset=logit(sd.e.to_numpy())).fit(
                            cov_type="cluster", cov_kwds={"groups": two_way(sd)})
                        trend.append({"platform": plat, "k": k, "count_of": col.rsplit("_", 1)[0],
                                      "beta": fit.params["log1p_count"], "se": fit.bse["log1p_count"],
                                      "p": fit.pvalues["log1p_count"], "rows": len(sd), "events": int(sd.y.sum())})
        print(f"k={k} gap={gap} done", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(out / "track_b.csv", index=False)
    pd.DataFrame(dose).to_csv(out / "track_b_dose.csv", index=False)
    pd.DataFrame(trend).to_csv(out / "track_b_trend.csv", index=False)
    pd.concat(panel).to_parquet(out / "track_b_panel.parquet", index=False)
    pd.set_option("display.width", 260, "display.max_columns", 40)
    show = ["platform", "k", "gap_days", "slice", "period", "state_weeks", "sightings", "followed",
            "expected", "oe_sighted", "oe_other", "oe_ratio", "lo", "hi", "share_flagged",
            "matched_rate", "p_matched"]
    print(res[show].round(3).to_string(index=False))
    print(pd.DataFrame(dose)[["platform", "k", "count_of", "bin", "sightings", "followed", "oe_ratio", "lo", "hi"]]
          .round(3).to_string(index=False))
    print(pd.DataFrame(trend).round(3).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("track", choices=["a", "b"])
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    if a.smoke:
        CFG.update(n_boot=50, n_perm=3, out=M.OUT / "smoke")
    {"a": track_a, "b": track_b}[a.track]()
