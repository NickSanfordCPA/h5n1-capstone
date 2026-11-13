# X utility analysis: frozen specification

*Frozen 2026-10-31, approved by Nick. The 2025+ window is scored once, after this commit, and
`x_utility.py final` refuses to run without it.*

## Question

What is the X data worth for predicting USDA-confirmed H5N1 poultry outbreaks, at the state level?
There are two separate findings. X has no usable county signal, so there is no county X arm; the
county baseline (C3) is reported on its own.

All choices below were made on 2022-08-07..2024-12-31 data. Each row's whole outcome window ends by
2024-12-31, so no 2025+ outcome was used. X features were reduced on redundancy and collector grounds,
not on outcomes.

## Shared design

- **Grain:** one row per state and week ending Sunday, 49 rows a week (CONUS states and DC).
- **Timing:** predictors end at the Sunday d, inclusive; targets are an outbreak in the state in
  (d, d+k].
- **State baseline S3:** `model_state_week`, shallow RF (300 trees, depth 6, min_leaf 2), three seeds
  averaged, no tuning. S3 beat the county roll-up at every k in the folds, so S3 stands.
- **Training:** 2022-08-07 through 2024-12-31 minus k. **Test:** Sundays from 2025-01-05 with
  d + k ≤ the last confirmation date, 2026-07-13.

## Track A: sentiment and volume

- **Rows:** covered state-weeks only (at least 1 subset post in the 7d window), the same rows in every
  arm. Nothing is imputed.
- **X features:** `x_share_7d` (volume); `x_sent_7d` and `x_sent_chg` (sentiment, shrunk with m = 10.58,
  estimated through 2024 without outcomes).
- **Removed from the candidates, without using outcomes:**
  - `x_sent_nonmedia_7d`: Spearman 0.96 with `x_sent_7d`.
  - `x_neg_share_7d`: Spearman −0.92 with `x_sent_7d`.
  - `log_x_posts_7d`: its level changes about tenfold at the collector break.
  - `x_anomaly_7d`: its 8-week trailing window spans the break, so it is depressed for exactly the
    weeks of 2025-Q1 that hold most test rows.
- **Arms:** S0 place/season/exposure; S1 X only; S2 S0 + X; S3 baseline; S4 S3 + X; S4v S3 + share;
  S4s S3 + sentiment.

**Primary:** S4 − S3 PR-AUC, k=14, state new-onset, paired week-block bootstrap, 2,000 reps, 95% CI.

**Success rule:** the CI excludes 0 on the positive side, and the point estimate stays positive with X
windows ending 5 days before d. Lift that vanishes under that embargo is not early warning.

**Secondary, labelled as such, no correction applied:** the other k and slices; S2 vs S0; S4v and S4s;
offset logistic with SEs clustered on state and week; block permutation within state x season;
residualized X; shared/unique deviance; the 3-day embargo; at least 5 posts.

## Track B: sightings

- **Rows:** one per sighting at its state-week, in CONUS, using `event_state`. 161 training rows
  (not the plan's 181: 18 fall before the window start or in AK/HI).
- **Reference:** S3 fit on all state-weeks, Platt-calibrated, leave-one-season-out inside training. It
  is used only for expected risk. The sighting model never trains on non-post rows.

**Primary:** O/E ratio at k=14, all sightings: (outbreaks after sightings / summed S3 risk) divided by
the same O/E on non-sighting weeks in the same period. Week-block bootstrap, 2,000 reps, 95% CI.

**Success rule:** the CI lower bound exceeds 1, and the point estimate stays above 1 with the first
5 days after d excluded from the outcome.

**Secondary, labelled as such:** k=7 and 30; state new-onset, which is **descriptive only** because
training has 71 rows and the test will have fewer; trigger rate; share of outbreaks flagged;
season-matched permutation; results by collector period. The sighting-row logistic is reported only as
description: post volume and sentiment showed nothing in training.

## Collector break and thin coverage

Train and test features come from different collectors. Every 2025+ result carries that label.

Covered rows fall from 86% of state-weeks in 2025-Q1 to 22-46% afterward, and at least 5 posts falls
to 1-8%. So:

- The primary test uses all covered test rows, and the same results are reported **for 2025-Q1 and for
  later weeks separately**, with the row and positive counts. If later weeks can't support an estimate,
  the report says so instead of leaving 2025-Q1 to stand for the whole window.
- **The at-least-5-posts check on 2025+ is descriptive only**, because nearly all its rows are in 2025-Q1.
- Track B repeats its primary excluding 2025-Q1, which holds 33 of 62 test sightings.
- Each report gives the CI's upper bound as the largest effect the data allow, and the share of
  outbreaks on covered rows.

## Exploration record

Not evidence for the claim, and not reused as a prior. Recorded so that the test can't be read as if
the choices were blind.

- **Track A, primary, reduced features, 2022-24 folds:** Δ +0.0049, CI [−0.0073, +0.0244], 149 positives.
- **Track B, primary, 2022-24:** O/E ratio 1.22, CI [1.01, 1.43]; 1.21, CI [0.87, 1.55] with the
  5-day gap.
- **Track B, new-onset:** 1.57 falls to 0.94 with the 5-day gap, on 13 and 5 outbreaks.

## Departures from the approved plan

- S0 has no literal state ID, because the exposure columns identify each state.
- Train start is 2022-08-07, not 2022-07, for a full trailing window.
- The feature list is reduced from seven to three, as above.
- Track B gains a false-lead check.
- Track B's new-onset slice is set to descriptive.
