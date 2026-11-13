"""State-week X features for the X utility analysis (docs/x_utility_spec.md, Track A), and the
sighting rows for Track B. One row per (state, week ending Sunday), the grain of model_state_week.

    DB_PORT=5433 uv run --extra modeling python -m h5n1.features.x_state coverage

Every window is trailing and ends at the week's Sunday, or `end_lag` days earlier for the
false-lead check, so a post dated after the window cannot change a feature.

COVERED ROWS. x_covered = at least one filtered-subset post in the 7d window. Rows that are not
covered are left out of the analysis, not imputed. Coverage uses past posts only, so the row
filter cannot leak the outcome.

SHRINKAGE. A 7d mean over one post is one person's tone, so sentiment means are empirical-Bayes
shrunk: (sum + m * prior) / (n + m). The prior is the state's own mean over posts BEFORE the
window, itself shrunk toward the national mean over those posts, so it is past-only and defined
for a state with no history. m comes from the post-level and state-week variances through
FIT_END, without outcomes (estimate_pseudocount).

COLLECTOR BREAK. Posts come from different collectors either side of 2025-01-01, and volume falls
by an order of magnitude. x_share_7d is relative to the national count, so it survives the break.
x_anomaly_7d compares to the state's trailing eight weeks, so it is depressed for the eight weeks
after the break. log_x_posts_7d is exposed in full.

These are public and news posts, not farmer posts: farmer_keeper is under 2% of the subset.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STREAMS = ["prior", "en_test", "es", "fr"]
FIT_END = "2024-12-31"
VOLUME = ["log_x_posts_7d", "x_share_7d", "x_anomaly_7d"]
SENTIMENT = ["x_sent_7d", "x_sent_chg", "x_neg_share_7d", "x_sent_nonmedia_7d"]
X_FEATURES = VOLUME + SENTIMENT
TRAIL_DAYS, TRAIL_WEEKS, SENT_TRAIL_DAYS = 56, 8, 28


def estimate_pseudocount(posts: pd.DataFrame, fit_end: str = FIT_END) -> float:
    """Method-of-moments m = (post-level variance) / (true between-state-week variance), clipped to
    [1, 50]. Uses sentiment only, no outcomes."""
    d = _with_day(posts)
    d = d[d.day <= pd.Timestamp(fit_end)]
    wk = d.day.dt.to_period("W").astype(str)
    g = d.groupby([d.state, wk]).sentiment
    n, mean, var = g.size(), g.mean(), g.var()
    within = var[n > 1].mean()
    # one-way random effects, ANOVA form: the post-weighted variance of cell means has expectation
    # about between + within * cells / posts
    total = np.average((mean - np.average(mean, weights=n)) ** 2, weights=n)
    between = max(total - within * len(n) / n.sum(), 1e-6)
    return float(np.clip(within / between, 1.0, 50.0))


def _with_day(posts: pd.DataFrame) -> pd.DataFrame:
    return posts.assign(day=posts.ts.dt.tz_convert(None).dt.normalize())


def _daily(d: pd.DataFrame, value: str | None, days, states) -> pd.DataFrame:
    """Day x state matrix of the sum of `value`, or of the post count when value is None."""
    g = d.groupby(["day", "state"])
    s = g.size() if value is None else g[value].sum()
    return s.unstack(fill_value=0).reindex(index=days, columns=states, fill_value=0).astype(float)


def _window(m: pd.DataFrame, width: int, lag: int) -> pd.DataFrame:
    """Sum over [t - lag - width + 1, t - lag]."""
    return m.rolling(width, min_periods=1).sum().shift(lag).fillna(0.0)


def _prior(num: pd.DataFrame, den: pd.DataFrame, lag: int, m: float) -> pd.DataFrame:
    """State mean over everything before day - lag, shrunk toward the national mean to that point."""
    cn, cd = num.cumsum().shift(lag).fillna(0.0), den.cumsum().shift(lag).fillna(0.0)
    nat = (cn.sum(axis=1) / cd.sum(axis=1).where(lambda v: v > 0)).ffill()
    return (cn.add(nat * m, axis=0)).div(cd + m)


def build_features(posts: pd.DataFrame, states: list[str], sundays: pd.DatetimeIndex,
                   m: float, end_lag: int = 0) -> pd.DataFrame:
    """Posts need state, ts, account_type, sentiment, is_negative. Returns one row per (state, day)
    with x_n_posts_7d, x_covered and X_FEATURES. Features of uncovered rows are not for use."""
    d = _with_day(posts[posts.state.isin(states)])
    days = pd.date_range(min(d.day.min(), sundays.min()) - pd.Timedelta(days=1), sundays.max())
    d = d[d.day <= days.max()]
    n = _daily(d, None, days, states)
    s = _daily(d, "sentiment", days, states)
    neg = _daily(d.assign(is_negative=d.is_negative.astype(float)), "is_negative", days, states)
    nm = d[d.account_type != "media_aggregator"]
    nm_n, nm_s = _daily(nm, None, days, states), _daily(nm, "sentiment", days, states)

    n7, s7, neg7 = _window(n, 7, end_lag), _window(s, 7, end_lag), _window(neg, 7, end_lag)
    nat7 = n7.sum(axis=1)
    trail = _window(n, TRAIL_DAYS, end_lag + 7) / TRAIL_WEEKS

    # the prior stops where the 7d window starts, so the window is not shrunk toward itself
    before = end_lag + 7
    prior_s, prior_neg = _prior(s, n, before, m), _prior(neg, n, before, m)
    sent7 = (s7 + m * prior_s) / (n7 + m)
    n_tr = _window(n, SENT_TRAIL_DAYS, before)
    s_tr = _window(s, SENT_TRAIL_DAYS, before)
    feats = {
        "x_n_posts_7d": n7,
        "log_x_posts_7d": np.log1p(n7),
        "x_share_7d": n7.div(nat7.where(nat7 > 0), axis=0),
        "x_anomaly_7d": np.log((n7 + 1) / (trail + 1)),
        "x_sent_7d": sent7,
        "x_sent_chg": sent7 - (s_tr + m * prior_s) / (n_tr + m),
        "x_neg_share_7d": (neg7 + m * prior_neg) / (n7 + m),
        "x_sent_nonmedia_7d": (_window(nm_s, 7, end_lag) + m * prior_s)
        / (_window(nm_n, 7, end_lag) + m),
    }
    out = pd.concat({k: v.reindex(sundays).stack(future_stack=True) for k, v in feats.items()}, axis=1)
    out.index.names = ["day", "state"]
    out = out.reset_index()
    out["x_covered"] = out.x_n_posts_7d >= 1
    return out


def sighting_rows(ev: pd.DataFrame) -> pd.DataFrame:
    """One row per sighting at its state-week: `day` is the Sunday ending the week it was posted,
    so the forecast is made after the post. Input from x_analysis_table.sightings(), whose state is
    the event state. Each state-week has at most one."""
    day = ev.ts.dt.tz_convert(None).dt.to_period("W").dt.end_time.dt.normalize()
    out = ev.assign(day=day)[["_id", "state", "day", "ts"]]
    assert not out.duplicated(["state", "day"]).any(), "sightings must be one per state-week"
    return out.reset_index(drop=True)


def load_posts() -> pd.DataFrame:
    from h5n1.sources.x_analysis_table import sentiment_posts

    return sentiment_posts(STREAMS)


def coverage(posts: pd.DataFrame, states: list[str], sundays: pd.DatetimeIndex) -> pd.DataFrame:
    """Share of state-weeks with at least 1 and at least 5 posts, by quarter. X data only."""
    f = build_features(posts, states, sundays, m=estimate_pseudocount(posts))
    q = f.day.dt.to_period("Q").astype(str)
    return f.groupby(q).agg(state_weeks=("state", "size"),
                            posts=("x_n_posts_7d", "sum"),
                            covered_1=("x_n_posts_7d", lambda v: (v >= 1).mean()),
                            covered_5=("x_n_posts_7d", lambda v: (v >= 5).mean()),
                            median_posts_if_covered=("x_n_posts_7d", lambda v: v[v >= 1].median()))


def main() -> None:
    import pathlib

    from h5n1.db import get_engine

    grid = pd.read_sql("SELECT DISTINCT state, day FROM model_state_week WHERE day >= '2022-08-07'",
                       get_engine(), parse_dates=["day"])
    posts = load_posts()
    out = coverage(posts, sorted(grid.state.unique()), pd.DatetimeIndex(sorted(grid.day.unique())))
    dest = pathlib.Path(__file__).resolve().parents[2] / "data" / "X_derived" / "x_utility"
    dest.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest / "coverage_by_quarter.csv")
    print(f"pseudocount m = {estimate_pseudocount(posts):.2f}")
    print(out.round(3).to_string())


if __name__ == "__main__":
    main()
