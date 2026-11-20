# Multimedia Appendices 5 to 7 (draft)

Draft 1, 2026-11-18, for `manuscript.md`. Appendices 1 to 4 are separate files (the
checklists, the protocol with its departures, the post-processing counts, and the labelling
guides). X numbers were updated on 2026-11-19 after the English X posts of 2022-2024 were
re-collected, and again the same day after the query-days that collection had missed were
filled in.

## Multimedia Appendix 5. Baseline details

### 5.1 Calibration of the state baseline

The table compares the mean predicted probability of the state baseline (S3) with the
observed outcome rate, in tenths of predicted risk, for the 3,822 test-period state-weeks at
the 14-day horizon. These are the forest's predictions before the logistic calibration used
in Track B.

| Tenth of predicted risk | State-weeks | Mean prediction | Observed rate |
|---|---:|---:|---:|
| 1 (lowest) | 383 | 0.007 | 0.013 |
| 2 | 382 | 0.022 | 0.029 |
| 3 | 382 | 0.044 | 0.029 |
| 4 | 382 | 0.073 | 0.055 |
| 5 | 382 | 0.105 | 0.126 |
| 6 | 382 | 0.150 | 0.113 |
| 7 | 382 | 0.207 | 0.199 |
| 8 | 382 | 0.279 | 0.285 |
| 9 | 382 | 0.374 | 0.359 |
| 10 (highest) | 383 | 0.611 | 0.632 |

Over all test state-weeks the mean prediction was 0.187 and the outcome rate 0.184.

### 5.2 County-level baseline and its combination to state level

**What this does.** The main analysis uses the state-week as its unit. As a check on that
choice, a county-day random forest (35 features) was fitted on the same development period,
scored on county-Sundays in the test period, and its county predictions were combined to one
value per state-week. That combined prediction was then compared with the state baseline.

**Model.** Training rows were every county-day with an outcome event and a 5% sample of the
rest, with the sampled rows weighted by 20 (968,124 rows from February 8, 2022). Test rows
were every county-Sunday, unsampled. Two forest settings were used: the one chosen by a grid
search on 2022-2024 folds (500 trees, depth 12, minimum leaf size 50, one-third of features
per split) and a fixed alternative (300 trees, depth 6). The grid's mean PR-AUC ranged only
from 0.088 to 0.095, so the search determined the setting weakly.

**County-level performance, test period.** PR-AUC, with its ratio to the outcome rate:

| Horizon | Events / county-Sundays | Searched forest | Alternative forest |
|---|---:|---|---|
| 7 days | 622 / 245,532 | 0.143 (57 times) | 0.157 (62 times) |
| 14 days | 1,112 / 242,424 | 0.128 (28 times) | 0.137 (30 times) |
| 28 days | 1,973 / 236,208 | 0.128 (15 times) | 0.135 (16 times) |

**State baseline minus combined county model**, difference in PR-AUC on test state-weeks
(95% CI). A positive value favors the state baseline.

| Horizon | State-weeks | Searched county forest | Alternative county forest |
|---|---|---|---|
| 7 days | All | −0.031 (−0.051 to −0.011) | −0.019 (−0.039 to 0.002) |
| 7 days | New onset | −0.016 (−0.048 to 0.017) | 0.027 (−0.005 to 0.063) |
| 14 days | All | −0.013 (−0.030 to 0.005) | 0.003 (−0.015 to 0.020) |
| 14 days | New onset | −0.025 (−0.063 to 0.009) | 0.031 (−0.003 to 0.068) |
| 28 days | All | 0.003 (−0.016 to 0.021) | 0.018 (0.001 to 0.035) |
| 28 days | New onset | −0.028 (−0.065 to 0.013) | 0.049 (0.004 to 0.089) |

**Reading.** Which model does better depends on the county forest's settings, and most
intervals include zero. The combined county model is not worse than the state baseline. The
comparison does not bear on whether posts add to the forecast; Track A answers that at state
level, and on the state-weeks used in Track A the state model with post features did not
differ from the combined county model (Facebook 0.033, 95% CI −0.050 to 0.121; X 0.059, 95%
CI −0.018 to 0.140).

## Multimedia Appendix 6. Comparison with an earlier version of the Facebook data set

**What this does.** Before this analysis, one of the authors had built a Facebook data set
from the same Meta Content Library export with a different pipeline: a different location
method, a different labelling guide, and removal of high-volume "hub" authors. This appendix
reports how far the two versions agree, so that a reader can judge how much the results
depend on pipeline choices. The earlier version labels a week by its Monday and this one by
the Sunday that ends it; weeks were aligned before state-weeks were compared.

| Comparison | Result |
|---|---|
| Unique posts in both versions | 301,044 |
| Posts given a state by both | 22,990 |
| Same state among those | 22,862 (99.4%) |
| Given a state only in this version | 9,151 |
| Given a state only in the earlier version | 7,379 |
| Posts sent for labelling: this version / earlier / both | 25,932 / 30,369 / 22,723 |
| Posts judged for sightings in both | 17,612 |
| Agreement on sighting (Cohen κ) | 0.79 |
| Sighting only in the earlier version / only in this version | 357 / 119 |
| Sighting posts: this version / earlier / both | 1,670 / 1,422 / 964 |
| Same state among posts both call sightings | 99.5% |
| Sighted state-weeks: this version / earlier / both | 505 / 506 / 376 |

Of the 7,646 posts labelled only in the earlier version, 4,943 fall outside this analysis's
date window and 1,488 name no state under this version's rules. Of this version's 1,670
sighting posts, 212 were written by authors the earlier version removed as hubs and 375 were
not in its candidate set.

**Reading.** Where both versions locate or judge a post, they agree closely. The versions
differ mainly in which posts they admit, and they share about three-quarters of their sighted
state-weeks. The Facebook Track B estimate was nearly the same in the two (1.22 in the
earlier version and 1.21 here).

## Multimedia Appendix 7. Sensitivity analyses

**What this does.** Each row changes one element of the analysis and recomputes both primary
outcomes at 14 days in the test period. Track A is the change in PR-AUC from adding post
features, on new-onset state-weeks. Track B is the O/E ratio for sighted against other
state-weeks, followed by its estimate with the first 5 days of the outcome window excluded.
Values in parentheses are 95% CIs.

| Analysis | Track A, Facebook | Track A, X | Track B, Facebook | Track B, X |
|---|---|---|---|---|
| Primary analysis | −0.000 (−0.013 to 0.011) | 0.000 (−0.011 to 0.011) | 1.21 (1.06 to 1.37); 1.19 | 1.15 (0.99 to 1.32); 1.30 |
| Wild-bird features dated by collection | 0.001 (−0.011 to 0.013) | −0.005 (−0.014 to 0.003) | 1.18 (1.03 to 1.33); 1.17 | 1.12 (0.96 to 1.28); 1.27 |
| Alternative forest (300 trees, depth 6) | 0.006 (−0.009 to 0.022) | −0.017 (−0.028 to −0.006) | 1.21 (1.06 to 1.37); 1.19 | 1.15 (1.00 to 1.33); 1.32 |
| WHISPers mortality counts added to the baseline | 0.005 (−0.014 to 0.022) | −0.001 (−0.009 to 0.009) | 1.21 (1.06 to 1.37); 1.18 | 1.14 (0.98 to 1.32); 1.30 |
| Posts containing a poultry word removed | −0.013 (−0.026 to 0.006) | −0.005 (−0.013 to 0.005) | 1.17 (1.01 to 1.35); 1.15 | 1.15 (0.98 to 1.33); 1.35 |
| Most active 1% of authors removed | −0.003 (−0.015 to 0.008) | −0.011 (−0.044 to 0.024) | 1.20 (1.04 to 1.35); 1.21 | 1.15 (0.97 to 1.36); 1.19 |
| X posts located by author profile | not applicable | 0.001 (−0.006 to 0.007) | not applicable | 1.15 (1.00 to 1.32); 1.34 |
| 30-day onset and 30-day horizon | 0.001 (−0.006 to 0.008) | 0.003 (−0.004 to 0.009) | 1.28 (1.17 to 1.39); 1.25 | 1.28 (1.16 to 1.39); 1.32 |

Notes.

- A poultry word is one of a fixed English and Spanish list (chicken, turkey, flock, layer
  and similar); 32% of Facebook and 23% of X sentiment-set posts contain one.
- On X, 1 Track A interval lies below zero (the alternative forest): adding post features
  made the forecast slightly worse there. No interval lies above zero.
- The most active authors are the top 1% of each platform's authors by number of labelled
  posts dated through 2024 (58 of 5,701 on Facebook; 39 of 3,844 on X). Removing them
  removed 5% of Facebook and 60% of X sighting posts and left 122 sighted X state-weeks in
  the test period.
- A 30-day definition of new onset with the 14-day horizon gave results identical to the
  primary analysis, because no confirmation in the outcome file is dated on a Saturday or
  Sunday.
- Additional analysis added after the results were seen (post hoc): comparison within fifths
  of expected risk, reported in the main text.
