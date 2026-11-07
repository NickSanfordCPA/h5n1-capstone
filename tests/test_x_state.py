"""Hand-checked X state-week features. Pure: no DB, no network."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from h5n1.features.x_state import build_features, estimate_pseudocount, sighting_rows

STATES = ["IA", "MN"]
SUNDAY = pd.Timestamp("2023-03-12")
M = 2.0


def post(state, day, sentiment, account="general_public", negative=None):
    return {"state": state, "ts": pd.Timestamp(day, tz="UTC"), "account_type": account,
            "sentiment": sentiment, "is_negative": sentiment < 0 if negative is None else negative}


# History (before the window), the window Mon 03-06..Sun 03-12, and one post after it.
HISTORY = [post("IA", "2023-02-10", -0.2), post("IA", "2023-02-11", -0.4),
           post("MN", "2023-02-12", 0.3)]
WINDOW = [post("IA", "2023-03-06", -0.8), post("IA", "2023-03-12", 0.2, "media_aggregator"),
          post("MN", "2023-03-08", 0.1)]
LATER = [post("IA", "2023-03-13", -0.9)]


def row(posts, state="IA", **kw):
    f = build_features(pd.DataFrame(posts), STATES, pd.DatetimeIndex([SUNDAY]), m=M, **kw)
    return f[f.state == state].iloc[0]


def test_counts_share_and_anomaly():
    r = row(HISTORY + WINDOW)
    assert r.x_n_posts_7d == 2 and r.x_covered
    assert r.log_x_posts_7d == pytest.approx(math.log(3))
    assert r.x_share_7d == pytest.approx(2 / 3)
    # both IA history posts are inside the 56 days before the window: 2 posts / 8 weeks
    assert r.x_anomaly_7d == pytest.approx(math.log(3 / (2 / 8 + 1)))


def test_shrunken_sentiment_by_hand():
    r = row(HISTORY + WINDOW)
    nat = (-0.2 - 0.4 + 0.3) / 3
    prior = (-0.6 + M * nat) / (2 + M)
    assert r.x_sent_7d == pytest.approx((-0.6 + M * prior) / (2 + M))
    # non-media drops the media post from the window, leaving the -0.8
    assert r.x_sent_nonmedia_7d == pytest.approx((-0.8 + M * prior) / (1 + M))
    # the 28d trailing window holds both history posts (02-10 is 24 days before 03-06)
    assert r.x_sent_chg == pytest.approx(r.x_sent_7d - (-0.6 + M * prior) / (2 + M))
    neg_nat = 2 / 3
    neg_prior = (2 + M * neg_nat) / (2 + M)
    assert r.x_neg_share_7d == pytest.approx((1 + M * neg_prior) / (2 + M))


def test_post_after_the_week_changes_nothing():
    a, b = row(HISTORY + WINDOW), row(HISTORY + WINDOW + LATER)
    pd.testing.assert_series_equal(a, b)


def test_embargo_moves_the_window_back():
    # 5 days back the window is 02-27..03-07, which holds only the 03-06 post
    r = row(HISTORY + WINDOW, end_lag=5)
    assert r.x_n_posts_7d == 1
    # a post inside the embargo cannot change anything
    pd.testing.assert_series_equal(r, row(HISTORY + WINDOW + [post("IA", "2023-03-09", 0.9)], end_lag=5))


def test_empty_state_week_is_not_covered():
    r = row(HISTORY + [WINDOW[2]], state="IA")
    assert r.x_n_posts_7d == 0 and not r.x_covered


def test_sighting_rows_use_the_sunday_after_the_post():
    ev = pd.DataFrame({"_id": ["a", "b"], "state": ["IA", "IA"],
                       "ts": pd.to_datetime(["2023-03-06 01:00", "2023-03-12 23:00"], utc=True)})
    with pytest.raises(AssertionError):  # two in one state-week breaks the funnel's rule
        sighting_rows(ev)
    r = sighting_rows(ev.iloc[[0]])
    assert r.day.iloc[0] == SUNDAY


def test_pseudocount_recovers_a_known_variance_ratio():
    # true m = within / between = 0.16 / 0.01 = 16, with uneven cell sizes. One draw is noisy
    # because the between variance is small, so this checks the estimator's average.
    def one(seed):
        rng = np.random.default_rng(seed)
        rows = []
        for week in range(400):
            effect = rng.normal(0, 0.1)
            day = pd.Timestamp("2022-01-03") + pd.Timedelta(days=7 * (week % 100))
            for _ in range(int(rng.integers(1, 30))):
                rows.append(post(f"S{week // 100}", day, effect + rng.normal(0, 0.4)))
        return estimate_pseudocount(pd.DataFrame(rows), fit_end="2030-01-01")

    assert np.mean([one(s) for s in range(5)]) == pytest.approx(16, rel=0.1)


def test_pseudocount_is_bounded_and_outcome_free():
    m = estimate_pseudocount(pd.DataFrame(HISTORY + WINDOW + LATER), fit_end="2023-12-31")
    assert 1.0 <= m <= 50.0
