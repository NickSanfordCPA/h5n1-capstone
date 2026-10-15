"""Event study: do firsthand X reports of sick/dead animals precede APHIS detections?

EVENTS. The illness_seen posts from x_topics (firsthand report of sick/dead animals or a
suspected infection), after a manual screen (labels/illness_posts_screened.parquet) that drops
events outside the US, retrospective anecdotes, tested-negative / not-HPAI, secondhand reports
from another state, and two non-credible posts. Geography is the event state (the author's
profile state unless the text names another). One event per state per ISO week (the
earliest post), so one die-off posted about three times counts once.

OUTCOMES, per target, at state x day:
  poultry  any row in fact_h5n1_outbreak (the model's DV table), dated by Confirmed Diagnosis
  wild     any fact_wild_bird_detection row, dated by COLLECTION date -- the day the bird was
           sampled, not the lab confirmation, so a post about a bird that is then collected is
           contemporaneous, not a lead. The public-confirmation lag is longer than this measures.
Y(s, t, k) = 1 if the state has a detection in (t, t+k] -- strictly after the post day.

COMPARISONS (Nick's design plus a seasonality control):
  B1 national     mean of Y over every state-day in the study period
  B2 own-area     mean of Y over every day in the study period, for the event's own state,
                  averaged over events
  B3 season-matched permutation: each event is moved to a random day in the SAME state within
                  +/-15 days of the same day-of-year (any study year), 2,000 draws. Controls for
                  "posts and outbreaks both happen in migration season".
NEW-ONSET variant: events (and every baseline pool) restricted to state-days with no detection
of that target in the prior 30 days -- the early-warning question proper. All-events results
mix in posts reacting to detections that already happened.

Study period defaults to the exploratory span, 2022-06-01..2024-12-31 (train window). The
confirmatory run in docs/x_event_preregistration.md uses --start 2025-01-01 --end <last
poultry confirmation - 30 days>.

    DB_PORT=5433 uv run python -m h5n1.models.x_event_study            # event study
    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_event_study regress  # sentiment models
    ... regress --start 2025-01-01 --end 2026-06-13                                 # confirmatory P1-P3
    ... forest --start 2022-02-08 --end 2026-06-13 --train-end 2024-12-01 --test-start 2025-01-01 --ci 98.75  # P4
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from h5n1.db import get_engine
from h5n1.sources.x_location import FIPS_STATE

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAB = ROOT / "data" / "X_derived" / "labels"
OUT = ROOT / "data" / "X_derived" / "event_study"
START, END = pd.Timestamp("2022-06-01"), pd.Timestamp("2024-12-31")
KS = (7, 14, 30)
ONSET_LOOKBACK = 30
N_PERM, N_BOOT, SEASON_HALFWIDTH = 2000, 2000, 15
STATES = sorted(FIPS_STATE.values())


def load_events() -> pd.DataFrame:
    ev = pd.read_parquet(LAB / "illness_posts_screened.parquet")
    ev = ev[ev.exclude == ""].copy()
    ev["day"] = ev.ts.dt.tz_convert(None).dt.normalize()
    ev["week"] = ev.day.dt.to_period("W")
    ev = ev.sort_values("day").drop_duplicates(["event_state", "week"])
    ev = ev[ev.day.between(START, END)]  # events outside the window would add rows via .at
    return ev[ev.event_state.isin(STATES)][["_id", "event_state", "day"]].rename(columns={"event_state": "state"})


def load_detections() -> dict[str, pd.DataFrame]:
    e = get_engine()
    q = {"poultry": "SELECT DISTINCT fips, day FROM fact_h5n1_outbreak",
         "wild": "SELECT DISTINCT fips, day FROM fact_wild_bird_detection WHERE detection_count > 0"}
    out = {}
    for name, sql in q.items():
        d = pd.read_sql(sql, e, parse_dates=["day"])
        d["state"] = d.fips.str[:2].map(FIPS_STATE)
        out[name] = d.dropna(subset=["state"])[["state", "day"]].drop_duplicates()
    return out


def state_day_grid(det: pd.DataFrame) -> pd.DataFrame:
    """Wide 0/1 matrix, days x states, covering the lookback before START to END + max k."""
    days = pd.date_range(START - pd.Timedelta(days=ONSET_LOOKBACK), END + pd.Timedelta(days=max(KS)))
    g = pd.DataFrame(0, index=days, columns=STATES, dtype=np.int8)
    d = det[det.day.between(days[0], days[-1])]
    for s, day in zip(d.state, d.day):
        g.at[day, s] = 1
    return g


def forward_any(g: pd.DataFrame, k: int) -> pd.DataFrame:
    """Y(s, t, k): any detection in (t, t+k]."""
    rev = g.iloc[::-1].rolling(k, min_periods=1).max().iloc[::-1]
    return rev.shift(-1).fillna(0).astype(np.int8)


def backward_none(g: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """True where the state had no detection in [t-lookback, t] -- a new-onset day."""
    return g.rolling(lookback + 1, min_periods=1).max() == 0


def evaluate(ev: pd.DataFrame, g: pd.DataFrame, k: int, onset: bool, rng) -> dict:
    Y = forward_any(g, k).loc[START:END]
    ok = backward_none(g, ONSET_LOOKBACK).loc[START:END] if onset else pd.DataFrame(True, Y.index, Y.columns)
    e = ev[[ok.at[d, s] for s, d in zip(ev.state, ev.day)]]
    if len(e) < 10:
        return {"n_events": len(e)}
    y = np.array([Y.at[d, s] for s, d in zip(e.state, e.day)], dtype=float)
    obs = y.mean()

    b1 = Y.where(ok).stack().mean()
    own = Y.where(ok).mean()  # per-state rate over the period
    b2 = own.reindex(e.state).mean()

    # season-matched permutation, same state, same day-of-year +/- halfwidth, onset-eligible days
    doy = Y.index.dayofyear.to_numpy()
    pools = {}
    for s, d in zip(e.state, e.day):
        key = (s, d.dayofyear)
        if key not in pools:
            dist = np.abs(doy - d.dayofyear)
            dist = np.minimum(dist, 366 - dist)
            cand = np.where((dist <= SEASON_HALFWIDTH) & ok[s].to_numpy())[0]
            pools[key] = Y[s].to_numpy()[cand]
    draws = np.array([[rng.choice(pools[(s, d.dayofyear)]) for s, d in zip(e.state, e.day)]
                      for _ in range(N_PERM)], dtype=float).mean(axis=1)
    b3 = draws.mean()
    p_perm = (np.sum(draws >= obs) + 1) / (N_PERM + 1)

    boot = np.array([rng.choice(y, len(y)).mean() for _ in range(N_BOOT)])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n_events": len(e), "event_rate": obs, "ci_lo": lo, "ci_hi": hi,
            "B1_national": b1, "B2_own_area": b2, "B3_season_matched": b3,
            "lift_B1": obs / b1, "lift_B2": obs / b2, "lift_B3": obs / b3, "p_perm_B3": p_perm}


SENT_WINDOW = 7


def state_day_sentiment() -> pd.DataFrame:
    """Trailing SENT_WINDOW-day mean sentiment and post count per state-day, from the topic
    subset (state-geocoded, hubs and non-first copypasta removed), days x states."""
    X = ROOT / "data" / "X_derived"
    p = pd.read_parquet(X / "topic_subset.parquet", columns=["_id", "state", "ts", "account_type"]).merge(
        pd.read_parquet(X / "sentiment_subset.parquet", columns=["_id", "sentiment"]), on="_id")
    p["day"] = p.ts.dt.tz_convert(None).dt.normalize()
    days = pd.date_range(START - pd.Timedelta(days=SENT_WINDOW), END)
    s = p.pivot_table(index="day", columns="state", values="sentiment", aggfunc="sum").reindex(
        index=days, columns=STATES).fillna(0)
    n = p.pivot_table(index="day", columns="state", values="_id", aggfunc="count").reindex(
        index=days, columns=STATES).fillna(0)
    m = p.assign(media=p.account_type.eq("media_aggregator").astype(int)).pivot_table(
        index="day", columns="state", values="media", aggfunc="sum").reindex(index=days, columns=STATES).fillna(0)
    s7, n7, m7 = s.rolling(SENT_WINDOW).sum(), n.rolling(SENT_WINDOW).sum(), m.rolling(SENT_WINDOW).sum()
    return ((s7 / n7.where(n7 > 0)).loc[START:END], n7.loc[START:END],
            (m7 / n7.where(n7 > 0)).loc[START:END])


def regressions(ev: pd.DataFrame, g: pd.DataFrame, target: str) -> list[dict]:
    """New-onset state-days only. Logit of Y(s,t,k) with state x month-of-year fixed effects
    (the regression analogue of the season-matched comparison), SEs clustered by state x
    calendar month (overlapping outcome windows correlate neighbouring days).

      M0  post              all new-onset state-days        -- replicates the event study
      M1  post              state-days with >= 1 post in the trailing window
      M2  sentiment (z)     same rows
      M3  post + sentiment  same rows
      M4  M3 + log(1 + trailing post count) + media share of trailing posts -- the check that
          sentiment is not just proxying news volume / topic mix
    """
    import statsmodels.api as sm

    sent, n7, media = state_day_sentiment()
    onset = backward_none(g, ONSET_LOOKBACK).loc[START:END]
    post = pd.DataFrame(0, index=onset.index, columns=STATES, dtype=np.int8)
    for s, d in zip(ev.state, ev.day):
        post.at[d, s] = 1
    out = []
    for k in KS:
        Y = forward_any(g, k).loc[START:END]
        df = pd.DataFrame({"y": Y.stack(), "post": post.stack(), "onset": onset.stack(),
                           "sent": sent.stack(), "n7": n7.stack(),
                           "media": media.stack()}).reset_index()
        df.columns = ["day", "state", "y", "post", "onset", "sent", "n7", "media"]
        df = df[df.onset].copy()
        df["stratum"] = df.state + "_" + df.day.dt.month.astype(str)
        df["cluster"] = df.state + "_" + df.day.dt.strftime("%Y-%m")
        specs = [("M0", ["post"], df),
                 *[(m, cols, df[df.n7 > 0]) for m, cols in
                   [("M1", ["post"]), ("M2", ["sent_z"]), ("M3", ["post", "sent_z"]),
                    ("M4", ["post", "sent_z", "log_n7", "media_share"])]]]
        for name, cols, d in specs:
            d = d.copy()
            d["sent_z"] = (d.sent - d.sent.mean()) / d.sent.std()
            d["log_n7"] = np.log1p(d.n7)
            d["media_share"] = d.media.fillna(0)
            # strata with no outcome variation carry no information under stratum FE
            var = d.groupby("stratum").y.transform(lambda v: 0 < v.mean() < 1)
            d = d[var]
            fe = pd.get_dummies(d.stratum, drop_first=True, dtype=float)
            Xm = pd.concat([d[cols].astype(float), fe], axis=1)
            Xm.insert(0, "const", 1.0)
            fit = sm.GLM(d.y.astype(float), Xm, family=sm.families.Binomial()).fit(
                cov_type="cluster", cov_kwds={"groups": pd.factorize(d.cluster)[0]})
            for c in cols:
                lo, hi = fit.conf_int().loc[c]
                out.append({"target": target, "k": k, "model": name, "term": c,
                            "odds_ratio": np.exp(fit.params[c]), "or_lo": np.exp(lo), "or_hi": np.exp(hi),
                            "p": fit.pvalues[c], "n_rows": len(d), "n_post_days": int(d.post.sum()),
                            "n_strata": d.stratum.nunique()})
    return out


def forest_vs_logit(ev: pd.DataFrame, g: pd.DataFrame, k: int = 30,
                    train_end: str = "2023-11-30", test_start: str = "2024-01-01",
                    ci_level: float = 95.0, n_boot: int = 2000) -> None:
    """Out-of-sample comparison of a random forest and a logistic regression on the M4 rows
    (poultry new-onset state-days with >= 1 trailing post), k = 30.

    Fixed effects are not available to a forest, so BOTH models get season/state control as
    features computed from the training rows only: the state's new-onset outcome rate, the
    state x month-of-year rate (smoothed toward the state rate, 20 pseudo-rows), and month
    sin/cos. Arms per model: BASE (those features) and BASE+X (+ post, sent_z, media_share,
    log_n7). Train rows end 30 days before the test year so no training outcome window reaches
    into it. RF = Waree's production forest (300 trees, depth 6, leaf 2), mean of 3 seeds.
    Inference: paired bootstrap over test ISO weeks (all states in a week together)."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    sent, n7, media = state_day_sentiment()
    onset = backward_none(g, ONSET_LOOKBACK).loc[START:END]
    post = pd.DataFrame(0, index=onset.index, columns=STATES, dtype=np.int8)
    for s, d in zip(ev.state, ev.day):
        post.at[d, s] = 1
    Y = forward_any(g, k).loc[START:END]
    df = pd.DataFrame({"y": Y.stack(), "post": post.stack(), "onset": onset.stack(), "sent": sent.stack(),
                       "n7": n7.stack(), "media": media.stack()}).reset_index()
    df.columns = ["day", "state", "y", "post", "onset", "sent", "n7", "media"]
    df = df[df.onset & (df.n7 > 0)].copy()
    df["month"] = df.day.dt.month
    tr = df[df.day <= train_end].copy()
    te = df[df.day >= test_start].copy()
    mu, sd = tr.sent.mean(), tr.sent.std()  # train-only scaling, no peeking at the test year
    for d in (tr, te):
        d["sent_z"] = (d.sent - mu) / sd
        d["log_n7"] = np.log1p(d.n7)
        d["media_share"] = d.media.fillna(0)
        d["m_sin"], d["m_cos"] = np.sin(2 * np.pi * d.month / 12), np.cos(2 * np.pi * d.month / 12)
    glob = tr.y.mean()
    st = tr.groupby("state").y.agg(["sum", "size"])
    st_rate = (st["sum"] + 20 * glob) / (st["size"] + 20)
    sm_ = tr.groupby(["state", "month"]).y.agg(["sum", "size"])
    for d in (tr, te):
        d["state_rate"] = d.state.map(st_rate).fillna(glob)
        prior = d.state_rate
        key = pd.MultiIndex.from_arrays([d.state, d.month])
        s_sum = sm_["sum"].reindex(key).fillna(0).to_numpy()
        s_n = sm_["size"].reindex(key).fillna(0).to_numpy()
        d["season_rate"] = (s_sum + 20 * prior.to_numpy()) / (s_n + 20)

    base = ["state_rate", "season_rate", "m_sin", "m_cos"]
    xcols = ["post", "sent_z", "media_share", "log_n7"]
    arms = {"BASE": base, "BASE+X": base + xcols}
    preds, fitted = {}, {}
    for arm, cols in arms.items():
        lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000))
        lr.fit(tr[cols], tr.y)
        preds[("LR", arm)] = lr.predict_proba(te[cols])[:, 1]
        fitted[("LR", arm)] = lr
        ps, rfs = [], []
        for seed in (0, 1, 2):
            rf = RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=2,
                                        n_jobs=12, random_state=seed)
            rf.fit(tr[cols], tr.y)
            ps.append(rf.predict_proba(te[cols])[:, 1])
            rfs.append(rf)
        preds[("RF", arm)] = np.mean(ps, axis=0)
        fitted[("RF", arm)] = rfs[0]

    y = te.y.to_numpy()
    wk = pd.factorize(te.day.dt.to_period("W"))[0]
    rng = np.random.default_rng(20260928)
    idx_by_wk = [np.where(wk == w)[0] for w in range(wk.max() + 1)]
    boots = [np.concatenate([idx_by_wk[w] for w in rng.integers(0, len(idx_by_wk), len(idx_by_wk))])
             for _ in range(n_boot)]
    boots = [b for b in boots if 0 < y[b].sum() < len(b)]

    def ci(f):
        v = np.array([f(b) for b in boots])
        tail = (100 - ci_level) / 2
        return np.percentile(v, [tail, 100 - tail])

    print(f"train rows {len(tr):,} (positives {int(tr.y.sum())}, post days {int(tr.post.sum())}); "
          f"test rows {len(te):,} (positives {int(te.y.sum())}, post days {int(te.post.sum())}); "
          f"test base rate {y.mean():.3f}")
    rows = []
    for (m, arm), p in preds.items():
        lo, hi = ci(lambda b: average_precision_score(y[b], p[b]))
        rows.append({"model": m, "arm": arm, "pr_auc": average_precision_score(y, p), "pr_lo": lo, "pr_hi": hi,
                     "roc_auc": roc_auc_score(y, p)})
    print(pd.DataFrame(rows).round(4).to_string(index=False))
    print("\npaired deltas in PR-AUC (95% week-block bootstrap CI):")
    for name, a, b in [("LR: X lift", ("LR", "BASE+X"), ("LR", "BASE")),
                       ("RF: X lift", ("RF", "BASE+X"), ("RF", "BASE")),
                       ("RF - LR, BASE+X", ("RF", "BASE+X"), ("LR", "BASE+X")),
                       ("RF - LR, BASE", ("RF", "BASE"), ("LR", "BASE"))]:
        pa, pb = preds[a], preds[b]
        dlt = average_precision_score(y, pa) - average_precision_score(y, pb)
        lo, hi = ci(lambda i: average_precision_score(y[i], pa[i]) - average_precision_score(y[i], pb[i]))
        print(f"  {name:18} {dlt:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
    print("\npermutation importance on test (drop in PR-AUC, 10 repeats), BASE+X:")
    for m in ("LR", "RF"):
        cols = arms["BASE+X"]
        pi = permutation_importance(fitted[(m, "BASE+X")], te[cols], y, scoring="average_precision",
                                    n_repeats=10, random_state=0)
        print(f"  {m}: " + ", ".join(f"{c} {v:+.4f}" for c, v in zip(cols, pi.importances_mean)))
    lr = fitted[("LR", "BASE+X")][-1]
    print("\nLR (standardized) coefficients, BASE+X: " +
          ", ".join(f"{c} {v:+.3f}" for c, v in zip(arms["BASE+X"], lr.coef_[0])))


def main_forest(train_end: str, test_start: str, ci_level: float) -> None:
    ev = load_events()
    forest_vs_logit(ev, state_day_grid(load_detections()["poultry"]), train_end=train_end,
                    test_start=test_start, ci_level=ci_level)


def main_regressions() -> None:
    ev = load_events()
    rows = []
    for target, d in load_detections().items():
        rows += regressions(ev, state_day_grid(d), target)
    res = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / f"regressions_new_onset_{START:%Y%m%d}_{END:%Y%m%d}.csv", index=False)
    pd.set_option("display.width", 200)
    print(res.round(3).to_string(index=False))


def main() -> None:
    rng = np.random.default_rng(20260928)
    ev = load_events()
    det = load_detections()
    rows = []
    for target, d in det.items():
        g = state_day_grid(d)
        for onset in (False, True):
            for k in KS:
                r = evaluate(ev, g, k, onset, rng)
                rows.append({"target": target, "subset": "new_onset" if onset else "all", "k": k, **r})
    res = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / f"results_{START:%Y%m%d}_{END:%Y%m%d}.csv", index=False)
    ev.to_csv(OUT / f"events_{START:%Y%m%d}_{END:%Y%m%d}.csv", index=False)
    pd.set_option("display.width", 200)
    print(f"events after screen + state-week dedup: {len(ev)} in {ev.state.nunique()} states")
    print(res.round(3).to_string(index=False))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", choices=["events", "regress", "forest"], default="events")
    ap.add_argument("--start", default=str(START.date()))
    ap.add_argument("--end", default=str(END.date()))
    ap.add_argument("--train-end", default="2023-11-30")    # forest step only
    ap.add_argument("--test-start", default="2024-01-01")   # forest step only
    ap.add_argument("--ci", type=float, default=95.0)       # forest step only
    a = ap.parse_args()
    START, END = pd.Timestamp(a.start), pd.Timestamp(a.end)  # module globals read by every step
    if a.step == "forest":
        main_forest(a.train_end, a.test_start, a.ci)
    else:
        {"events": main, "regress": main_regressions}[a.step]()
