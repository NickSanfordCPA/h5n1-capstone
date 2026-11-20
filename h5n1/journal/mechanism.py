"""News-echo mechanism checks for Track B (protocol section 9), both platforms, k = 14.

    uv run --extra modeling python -m h5n1.journal.mechanism [--smoke]

Is a Track B signal early warning, or news reporting the interval between a public
announcement and the APHIS confirmation date?

Groups. A state-week is sighted for a group if it has at least one sighting post (funnel
steps 0 and 16) of that group on the platform:
    all                every sighting post (the Track B primary)
    wildlife_report    content class
    first_hand         content class
    news_like          organisation author, or class wildlife_report
    personal           individual author and class first_hand
The reference is always the state-weeks with NO sighting post on that platform, so a
state-week sighted only by another group is left out of that group's comparison.

Outputs in data/journal/models/ (smoke/ with --smoke):
    mechanism_cells.csv         O/E ratio per platform x group x gap (0, 3, 5, 7, 10 days)
    mechanism_interaction.csv   pooled offset-logistic model, group x platform interaction,
                                and the ratio of O/E ratios, with a shared week-block bootstrap
    mechanism_timing.csv        days from a sighting to the next confirmation in its state
    mechanism_timing_rows.parquet
    mechanism_dropped.csv       DESCRIPTIVE: sightings followed within N days removed
    mechanism_proxy.csv         sightings before / after the first public report (GDELT; WHISPers)
    mechanism_news.csv          O/E against S3 + GDELT news volume
    mechanism_panel.parquet     the state-week panel behind all of it

The estimand of the timing test is the shifted outcome window (gap). The gap is counted from
the forecast Sunday, which is on or after the post, so it is at least as strict as counting
from the post. Removing sightings BECAUSE an outbreak followed selects on the outcome; those
tables (dropped, proxy) can only lower the ratio and are bounds, not estimates.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from sqlalchemy import text

from h5n1.journal import models as M
from h5n1.journal import tracks as T
from h5n1.journal.extras import OUTAGE
from h5n1.journal.localdb import local_engine

K = 14
GAPS = (0, 3, 5, 7, 10)
GROUPS = ("all", "wildlife_report", "first_hand", "news_like", "personal")
PAIRS = {"tag": ("news_like", "personal"), "content_class": ("wildlife_report", "first_hand")}
NEWS = ["log_gdelt_n_7d", "gdelt_share_7d"]
PROXY_DAYS = 14


# ---------------------------------------------------------------- data

def sighting_posts() -> pd.DataFrame:
    q = text("SELECT f.platform, f.state, f.week AS day, f.post_id, f.day AS posted, f.content_class, "
             "t.author_type, t.news_like FROM journal.post_funnel f "
             "JOIN journal.post_tag t USING (platform, post_id) WHERE f.sighting_post AND f.step IN (0, 16)")
    with local_engine().connect() as cn:
        d = pd.read_sql(q, cn, parse_dates=["day", "posted"])
    assert d.content_class.isin(["wildlife_report", "first_hand"]).all()
    assert (d.news_like == ((d.author_type == "organisation") | (d.content_class == "wildlife_report"))).all()
    return d


def group_mask(p: pd.DataFrame, g: str) -> pd.Series:
    if g == "all":
        return pd.Series(True, index=p.index)
    if g in ("wildlife_report", "first_hand"):
        return p.content_class == g
    return p.news_like if g == "news_like" else ~p.news_like


def build_panel(sw: pd.DataFrame) -> pd.DataFrame:
    """sw plus, per platform and group, s_<group>_<platform> (sighted) and
    p_<group>_<platform> (date of the group's first sighting post in the state-week)."""
    posts = sighting_posts()
    out = sw.copy()
    for plat in T.PLATFORMS:
        p = posts[posts.platform == plat]
        for g in GROUPS:
            first = (p[group_mask(p, g)].groupby(["state", "day"]).posted.min()
                     .rename(f"p_{g}_{plat}").reset_index())
            out = out.merge(first, on=["state", "day"], how="left", validate="1:1")
            out[f"s_{g}_{plat}"] = out[f"p_{g}_{plat}"].notna()
    return out


def outbreaks() -> pd.DataFrame:
    with local_engine().connect() as cn:
        return pd.read_sql(text("SELECT DISTINCT c.state, o.day AS hit FROM fact_h5n1_outbreak o "
                                "JOIN dim_county c USING (fips)"), cn, parse_dates=["hit"])


def references() -> dict[str, pd.DataFrame]:
    """state, day of each public reference: a GDELT story naming the state; the lower-bound
    record date of a WHISPers event in the state that carries an HPAI diagnosis."""
    with local_engine().connect() as cn:
        g = pd.read_sql(text("SELECT state, day FROM journal.gdelt_state_day WHERE n_stories > 0"), cn,
                        parse_dates=["day"])
        w = pd.read_sql(text(
            "SELECT DISTINCT c.state, e.known_date_lb AS day FROM whispers_event e "
            "JOIN whispers_event_county ec USING (event_id) JOIN dim_county c ON c.fips = ec.fips "
            "WHERE e.us AND e.hpai"), cn, parse_dates=["day"])
    return {"gdelt": g, "whispers": w}


# ---------------------------------------------------------------- O/E cells

def cell(d: pd.DataFrame, plat: str, g: str) -> dict | None:
    """d has y, e, day and the s_ flags. Group-sighted against never-sighted on the platform."""
    keep = d[f"s_{g}_{plat}"] | ~d[f"s_all_{plat}"]
    x = d[keep].assign(sighted=lambda v: v[f"s_{g}_{plat}"].to_numpy())
    if not x.sighted.any() or not (~x.sighted).any():
        return None
    r = T.oe_ratio(x[["y", "e", "sighted", "day"]].reset_index(drop=True))
    return {k: r[k] for k in ("state_weeks", "sightings", "followed", "expected", "oe_sighted",
                              "oe_other", "oe_ratio", "lo", "hi", "boot_reps", "P(ratio>1)")}


def cells(t: pd.DataFrame, base: dict) -> list[dict]:
    rows = []
    for period, ecol in (("test", "e_test"), ("development", "e_dev")):
        d = t[t[ecol].notna()].assign(e=lambda v: v[ecol])
        for plat in T.PLATFORMS:
            for g in GROUPS:
                r = cell(d, plat, g)
                if r:
                    rows.append({**base, "period": period, "platform": plat, "group": g, **r})
    return rows


# ---------------------------------------------------------------- interaction

def fit_logit(X: np.ndarray, y: np.ndarray, off: np.ndarray) -> np.ndarray | None:
    """Newton fit of a logistic model with an offset. None if it does not converge to a
    finite answer (an empty cell in a bootstrap replicate)."""
    b = np.zeros(X.shape[1])
    for _ in range(60):
        p = 1 / (1 + np.exp(-(X @ b + off)))
        try:
            step = np.linalg.solve((X.T * (p * (1 - p))) @ X, X.T @ (y - p))
        except np.linalg.LinAlgError:
            return None
        b += step
        if not np.isfinite(b).all() or np.abs(b).max() > 15:
            return None
        if np.abs(step).max() < 1e-8:
            return b
    return None


def interaction(t: pd.DataFrame, pair: tuple[str, str], base: dict, n_boot: int) -> list[dict]:
    """One pooled model over both platforms' test state-weeks (each state-week appears once
    per platform): y ~ meta + a + b + meta:a + meta:b, offset logit(calibrated S3 risk).
    exp(meta:a) is the ratio of group a's odds ratio on Meta to that on X. The same weeks are
    resampled for both platforms. The ratio of the two O/E ratios is bootstrapped alongside."""
    d = t[t.e_test.notna()].reset_index(drop=True)
    a, b = pair
    blocks = []
    for plat in T.PLATFORMS:
        meta = float(plat == "meta")
        fa, fb = d[f"s_{a}_{plat}"].to_numpy(float), d[f"s_{b}_{plat}"].to_numpy(float)
        blocks.append(np.column_stack([np.ones(len(d)), np.full(len(d), meta), fa, fb, meta * fa, meta * fb,
                                       d.y.to_numpy(float), T.logit(d.e_test.to_numpy()),
                                       d.e_test.to_numpy(), d[f"s_all_{plat}"].to_numpy(float)]))
    A = np.vstack(blocks)
    wk = np.tile(M.week_of(d.day), 2)
    names = ["const", "meta", a, b, f"meta:{a}", f"meta:{b}"]

    def stats(arr: np.ndarray) -> np.ndarray | None:
        coef = fit_logit(arr[:, :6], arr[:, 6], arr[:, 7])
        if coef is None:
            return None
        out = list(coef)
        for col in (2, 3):                      # ratio of O/E ratios, Meta over X, per group
            r = []
            for meta in (1.0, 0.0):
                p = arr[arr[:, 1] == meta]
                s, n = p[p[:, col] == 1], p[p[:, 9] == 0]
                if not len(s) or not s[:, 8].sum() or not n[:, 6].sum():
                    return None
                r.append((s[:, 6].sum() / s[:, 8].sum()) / (n[:, 6].sum() / n[:, 8].sum()))
            if r[1] == 0:
                return None
            out.append(r[0] / r[1])
        return np.array(out)

    point = stats(A)
    assert point is not None, "the pooled model did not fit"
    weeks = np.unique(wk)
    by_week = {w: A[wk == w] for w in weeks}
    rng = np.random.default_rng(M.BOOT_SEED)
    boot = [s for s in (stats(np.concatenate([by_week[w] for w in rng.choice(weeks, len(weeks))]))
                        for _ in range(n_boot)) if s is not None]
    boot = np.array(boot)
    rows = []
    labels = names + [f"oe_ratio_meta_over_x:{a}", f"oe_ratio_meta_over_x:{b}"]
    for i, lab in enumerate(labels):
        logscale = i < 6
        est = float(np.exp(point[i])) if logscale else float(point[i])
        col = np.exp(boot[:, i]) if logscale else boot[:, i]
        lo, hi = np.percentile(col, [2.5, 97.5])
        rows.append({**base, "term": lab, "scale": "odds ratio" if logscale else "ratio of O/E ratios",
                     "estimate": est, "lo": float(lo), "hi": float(hi), "P(>1)": float((col > 1).mean()),
                     "boot_reps": len(boot), "boot_failed": n_boot - len(boot),
                     "sighted_meta": int(d[f"s_{lab.split(':')[-1]}_meta"].sum()) if ":" in lab else np.nan,
                     "sighted_x": int(d[f"s_{lab.split(':')[-1]}_x"].sum()) if ":" in lab else np.nan})
    return rows


# ---------------------------------------------------------------- timing

def next_hit(state: pd.Series, day: pd.Series, hits: pd.DataFrame, strict: bool) -> pd.Series:
    """The next confirmation in the state on or after `day` (after, if strict)."""
    by = {s: np.sort(g.hit.to_numpy()) for s, g in hits.groupby("state")}
    out = []
    for s, d in zip(state, day.to_numpy()):
        h = by.get(s, np.array([], dtype="datetime64[ns]"))
        i = np.searchsorted(h, d, side="right" if strict else "left")
        out.append(h[i] if i < len(h) else np.datetime64("NaT", "ns"))
    return pd.Series(pd.to_datetime(out), index=state.index)


def timing(panel: pd.DataFrame, hits: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Days from the group's first sighting post in a state-week to the next confirmation in
    that state. Description only: it uses the outcome."""
    rows = []
    for plat in T.PLATFORMS:
        for g in GROUPS:
            s = panel[panel[f"s_{g}_{plat}"]]
            posted = s[f"p_{g}_{plat}"]
            nh = next_hit(s.state, posted, hits, strict=False)
            rows.append(pd.DataFrame({
                "platform": plat, "group": g, "state": s.state.to_numpy(), "day": s.day.to_numpy(),
                "period": np.where(s.day >= M.TEST_FIRST, "test", "development"),
                "posted": posted.to_numpy(), "days_to_confirmation": (nh - posted).dt.days.to_numpy()}))
    r = pd.concat(rows, ignore_index=True)
    summ = []
    for (plat, g, per), x in r.groupby(["platform", "group", "period"], sort=False):
        d = x.days_to_confirmation
        w = d[d <= 28]
        summ.append({"platform": plat, "group": g, "period": per, "sightings": len(x),
                     **{f"within_{n}d": int((d <= n).sum()) for n in (3, 5, 7, 10, 14, 28)},
                     "median_days_if_within_28": float(w.median()) if len(w) else np.nan,
                     "q25": float(w.quantile(.25)) if len(w) else np.nan,
                     "q75": float(w.quantile(.75)) if len(w) else np.nan})
    return pd.DataFrame(summ), r


def dropped(t: pd.DataFrame, hits: pd.DataFrame, base: dict) -> list[dict]:
    """DESCRIPTIVE. Sighted state-weeks whose next confirmation falls within N days of the
    Sunday are removed. This selects on the outcome and can only lower the ratio."""
    d = t[t.e_test.notna()].assign(e=lambda v: v.e_test)
    lead = (next_hit(d.state, d.day, hits, strict=True) - d.day).dt.days
    rows = []
    for n in (3, 5, 7, 10):
        for plat in T.PLATFORMS:
            for g in GROUPS:
                x = d[~(d[f"s_{g}_{plat}"] & (lead <= n))]
                r = cell(x, plat, g)
                if r:
                    rows.append({**base, "dropped_within_days": n, "platform": plat, "group": g,
                                 "removed": int((d[f"s_{g}_{plat}"] & (lead <= n)).sum()), **r})
    return rows


def proxy(t: pd.DataFrame, hits: pd.DataFrame, refs: dict[str, pd.DataFrame], base: dict) -> list[dict]:
    """Each sighting followed by an outbreak is classed against the first public reference
    in the PROXY_DAYS days before that confirmation: posted before it, on or after it, or no
    reference in the window. The O/E ratio is recomputed without the 'after' sightings. That
    removes only sightings that were followed, so it is a lower bound."""
    d = t[t.e_test.notna()].assign(e=lambda v: v.e_test).reset_index(drop=True)
    conf = next_hit(d.state, d.day, hits, strict=True)
    rows = []
    for src, ref in refs.items():
        by = {s: np.sort(g.day.to_numpy()) for s, g in ref.groupby("state")}

        def first_ref(state, c):
            h = by.get(state, np.array([], dtype="datetime64[ns]"))
            lo = np.searchsorted(h, np.datetime64(c - pd.Timedelta(days=PROXY_DAYS)), side="left")
            hi = np.searchsorted(h, np.datetime64(c), side="left")
            return pd.Timestamp(h[lo]) if lo < hi else pd.NaT

        for plat in T.PLATFORMS:
            for g in GROUPS:
                cls = pd.Series("", index=d.index)
                for i in d.index[d[f"s_{g}_{plat}"] & (d.y == 1)]:
                    c, posted = conf[i], d.at[i, f"p_{g}_{plat}"]
                    fr = first_ref(d.at[i, "state"], c)
                    in_outage = (src == "gdelt" and c - pd.Timedelta(days=PROXY_DAYS) <= OUTAGE[1]
                                 and c - pd.Timedelta(days=1) >= OUTAGE[0])
                    if in_outage and (pd.isna(fr) or fr > OUTAGE[0]):
                        cls[i] = "unknown"
                    elif pd.isna(fr):
                        cls[i] = "no_reference"
                    else:
                        cls[i] = "before" if posted < fr else "after"
                r = cell(d[cls != "after"], plat, g)
                if r:
                    n = cls.value_counts()
                    rows.append({**base, "reference": src, "platform": plat, "group": g,
                                 "followed_before": int(n.get("before", 0)),
                                 "followed_after": int(n.get("after", 0)),
                                 "followed_no_reference": int(n.get("no_reference", 0)),
                                 "followed_unknown": int(n.get("unknown", 0)), **r})
    return rows


# ---------------------------------------------------------------- news control

def news_control(panel: pd.DataFrame, params: dict) -> list[dict]:
    """S3 + GDELT news volume. Rows whose 7-day GDELT window is unknown are dropped from
    both models, so the S3 row here is S3 on the same rows."""
    with local_engine().connect() as cn:
        ex = pd.read_sql(text("SELECT state, day, log_gdelt_n_7d, gdelt_share_7d FROM journal.state_week_extra"),
                         cn, parse_dates=["day"])
    t = panel.merge(ex, on=["state", "day"], how="left", validate="1:1")
    n_null = int(t[NEWS].isna().any(axis=1).sum())
    t = t[t[NEWS].notna().all(axis=1)].reset_index(drop=True)
    t["y"] = t[f"y{K}"]
    rows = []
    for name, cols in (("S3 (same rows)", M.S3), ("S3 + news", M.S3 + NEWS)):
        t["e_test"], t["e_dev"] = T.expected_risk(t, "y", K, params, cols)
        te = t[t.e_test.notna()]
        ap = {sl: average_precision_score(te.y[m], te.e_test[m])
              for sl, m in (("all", np.ones(len(te), bool)), ("new_onset", te.new_onset.to_numpy(bool)))}
        base = {"k": K, "reference_model": name, "rows_dropped_null_gdelt": n_null,
                "pr_auc_all": ap["all"], "pr_auc_new_onset": ap["new_onset"]}
        rows += cells(t, base)
        print(f"news control: {name} done", flush=True)
    return rows


# ---------------------------------------------------------------- run

def run(smoke: bool) -> None:
    out = M.OUT / "smoke" if smoke else M.OUT
    n_boot = 50 if smoke else M.N_BOOT
    T.CFG.update(n_boot=n_boot)
    out.mkdir(parents=True, exist_ok=True)
    params = M.chosen_params("state")
    sw = M.load_state_week("detected")
    panel = build_panel(sw)
    hits, refs = outbreaks(), references()
    for plat in T.PLATFORMS:
        print(plat, {g: int(panel[f"s_{g}_{plat}"].sum()) for g in GROUPS}, flush=True)

    tsum, trows = timing(panel, hits)
    tsum.to_csv(out / "mechanism_timing.csv", index=False)
    trows.to_parquet(out / "mechanism_timing_rows.parquet", index=False)

    cell_rows, inter, drop_rows, proxy_rows, panels = [], [], [], [], []
    for gap in GAPS:
        t = panel.copy()
        t["y"] = T.gap_target(sw, K, gap)
        t["e_test"], t["e_dev"] = T.expected_risk(t, "y", K, params)
        base = {"k": K, "gap_days": gap}
        cell_rows += cells(t, base)
        if gap in (0, 5):
            for label, pair in PAIRS.items():
                inter += interaction(t, pair, {**base, "pair": label}, n_boot)
        if gap == 0:
            drop_rows = dropped(t, hits, base)
            proxy_rows = proxy(t, hits, refs, base)
        panels.append(t[["state", "day", "new_onset", "y", "e_test", "e_dev"]
                        + [c for c in t.columns if c[:2] in ("s_", "p_")]].assign(k=K, gap=gap))
        pd.DataFrame(cell_rows).to_csv(out / "mechanism_cells.csv", index=False)
        print(f"gap {gap} done", flush=True)
    pd.DataFrame(inter).to_csv(out / "mechanism_interaction.csv", index=False)
    pd.DataFrame(drop_rows).to_csv(out / "mechanism_dropped.csv", index=False)
    pd.DataFrame(proxy_rows).to_csv(out / "mechanism_proxy.csv", index=False)
    pd.concat(panels).to_parquet(out / "mechanism_panel.parquet", index=False)
    news = pd.DataFrame(news_control(panel, params))
    news.to_csv(out / "mechanism_news.csv", index=False)

    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 400)
    c = pd.DataFrame(cell_rows)
    show = ["gap_days", "period", "platform", "group", "sightings", "followed", "expected", "oe_ratio", "lo", "hi"]
    print(c[c.period == "test"][show].round(3).to_string(index=False))
    print(pd.DataFrame(inter).round(3).to_string(index=False))
    print(tsum.round(1).to_string(index=False))
    print(pd.DataFrame(drop_rows)[["dropped_within_days", "platform", "group", "removed", "sightings",
                                   "followed", "oe_ratio", "lo", "hi"]].round(3).to_string(index=False))
    print(pd.DataFrame(proxy_rows)[["reference", "platform", "group", "followed_before", "followed_after",
                                    "followed_no_reference", "followed_unknown", "sightings", "followed",
                                    "oe_ratio", "lo", "hi"]].round(3).to_string(index=False))
    print(news[news.period == "test"][["reference_model", "pr_auc_all", "pr_auc_new_onset", "platform", "group",
                                       "sightings", "followed", "expected", "oe_ratio", "lo", "hi"]]
          .round(3).to_string(index=False))


# ---------------------------------------------------------------- risk-level check

def risk_check() -> None:
    """Post hoc (added in the self-review of the first draft, after the results were seen).
    Sighted state-weeks have about twice the expected risk of the others, so a baseline that
    under-predicts at high risk would give an O/E ratio above 1 with no information in the
    posts. Two adjustments, from the saved panel: (1) the sighted O/E against the non-sighted
    O/E of the same expected-risk quintile; (2) an offset logistic model of the outcome on the
    sighting indicator that also recalibrates on logit risk and its square, standard errors
    clustered on week. Output: mechanism_risk_adjusted.csv."""
    import statsmodels.api as sm

    panel = pd.read_parquet(M.OUT / "mechanism_panel.parquet")
    rows = []
    for gap in GAPS:
        t = panel[(panel.gap == gap) & panel.e_test.notna()].copy()
        t["bin"] = pd.qcut(t.e_test, 5, labels=False, duplicates="drop")
        top = t[t.bin == t.bin.max()]
        for plat in T.PLATFORMS:
            s = t[f"s_all_{plat}"].to_numpy(bool)
            non = t[~s].groupby("bin").apply(lambda g: g.y.sum() / g.e_test.sum(), include_groups=False).rename("oe_n")
            m = t[s].merge(non, left_on="bin", right_index=True)
            crude = (t[s].y.sum() / t[s].e_test.sum()) / (t[~s].y.sum() / t[~s].e_test.sum())
            lo_ = T.logit(t.e_test.to_numpy())
            X = pd.DataFrame({"const": 1.0, "sighted": s.astype(float), "lo": lo_, "lo2": lo_ ** 2}, index=t.index)
            rec = {"gap_days": gap, "platform": plat, "sighted": int(s.sum()),
                   "mean_risk_sighted": float(t[s].e_test.mean()), "mean_risk_other": float(t[~s].e_test.mean()),
                   "top_quintile_oe_all_rows": float(top.y.sum() / top.e_test.sum()),
                   "oe_ratio_crude": float(crude),
                   "oe_ratio_risk_standardised": float(t[s].y.sum() / (m.e_test * m.oe_n).sum())}
            for name, cols in (("offset_only", ["const", "sighted"]), ("recalibrated", ["const", "sighted", "lo", "lo2"])):
                f = sm.GLM(t.y, X[cols], family=sm.families.Binomial(), offset=lo_).fit(
                    cov_type="cluster", cov_kwds={"groups": pd.factorize(t.day)[0]})
                b, se = f.params["sighted"], f.bse["sighted"]
                rec.update({f"or_{name}": float(np.exp(b)), f"or_{name}_lo": float(np.exp(b - 1.96 * se)),
                            f"or_{name}_hi": float(np.exp(b + 1.96 * se))})
            rows.append(rec)
    res = pd.DataFrame(rows)
    res.to_csv(M.OUT / "mechanism_risk_adjusted.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 40)
    print(res.round(3).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--risk-check", action="store_true", help="post hoc risk-level adjustment from the saved panel")
    a = ap.parse_args()
    risk_check() if a.risk_check else run(a.smoke)
