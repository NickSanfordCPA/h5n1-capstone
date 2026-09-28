# GDELT GCAM: pre-registration

**Committed on 2026-09-27, before any GCAM value was pulled or looked at.** Everything in
this file is fixed ahead of the data: the dimensions, the feature form, the screen, the
decision rules. Anything decided later is labelled as a deviation in the results notebook.

## Why this test exists

The GDELT lift test (`notebooks/gdelt_lift.ipynb`) found that news *volume* adds no
out-of-time lift, and that coverage peaks in the same week as outbreaks or the week after.
The only emotional measure in that test was V2Tone, a general-purpose score. So the
"incremental sentiment" hypothesis has not yet been tested with a real affect measure.
GCAM is the closest one available outside Meta's environment.

The one mechanism by which GCAM could beat volume is **pre-confirmation language**:
coverage written before APHIS confirms, e.g. "birds dying, cause unknown, farmers
worried". This coverage might carry more anxiety, hedging and uncertainty than reports
of confirmed outbreaks. If no dimension leads outbreaks once current outbreaks and news
volume are controlled for, GCAM cannot add lift, and we stop.

GDELT is news, not social media. A result here is about news affect.

## Dimensions (5, English dictionaries, all fixed)

| id | GCAM variable | dictionary / dimension | why |
|---|---|---|---|
| anxiety | `c5.33` | LIWC Anxiety | worry about spread |
| tentative | `c5.26` | LIWC Tentative | hedging: "suspected", "possible", "may" |
| death | `c5.2` | LIWC Death | die-off reports before a diagnosis |
| uncertainty | `c6.6` | Loughran-McDonald Uncertainty | unknown cause or pending tests |
| negative | `c3.1` | Lexicoder Sentiment NEGATIVE | news-validated negativity (Young & Soroka 2012) |

Hypothesised sign for all five: **positive**, i.e. more of this language now means more
outbreaks later.

No other GCAM dimension will be used for any claim. The full GCAM string is archived in
staging for reproducibility only.

## Article-level measure

For each dimension, rate = count / `wc` (GCAM's total word count). An article with
`wc` = 0 or no GCAM gets NULL. Syndicated copies (same `syndication_key`) are collapsed
to one story using the mean over its articles, as V2Tone is in 014.

## Stage 1: lead screen (training window only)

**Data window**
- Full ISO weeks (Monday start) from 2022-02-07 to 2024-12-30.
- This is the training window. The 2025+ test window stays untouched for stage 2.
- CONUS states only.
- Weeks whose trailing window touches the GKG outage are excluded; the outage falls in
  2025, so none should be.

**Unit and variables.** One row per state-week.
- `dim_t`: the mean over the state's distinct stories that week. A state-week with no
  stories is dropped, since affect is undefined there.
- `y_t`: log1p(number of outbreak county-days in the state that week), using the same
  `fact_h5n1_outbreak` source as `target_outbreak_next_7d`.
- `vol_t`: log1p(distinct stories in the state that week).

**Model.** For each dimension d and lead k in {1, 2, 3} weeks, fit an OLS with state
fixed effects (within-state demeaning):

    y_{t+k} ~ dim_t + y_t + y_{t-1} + vol_t

`dim_t` is standardised within the screen sample. Controlling for `y_t` and `y_{t-1}`
removes the fact that outbreaks persist, which alone makes any contemporaneous news
series look like it leads. Controlling for `vol_t` asks whether affect adds anything
beyond how much coverage there is.

**Inference**
- 2,000 bootstrap reps, resampling whole calendar weeks (all states in a week together).
- 5 dimensions × 3 leads = 15 tests. Bonferroni gives a two-sided **99.67% CI**
  (percentiles 0.167 and 99.833).

**Pass rule.** A dimension passes if, at any lead k, its coefficient is **positive** and
its 99.67% CI excludes 0. Negative significant coefficients are reported but do not pass.

**If nothing passes:** record the null and stop. There is no stage 2.

## Stage 2: out-of-time lift (only for dimensions that pass stage 1)

**Feature**
- Per passing dimension: the state-level trailing 21-day mean over distinct stories,
  with a past-only window [day-21, day-1].
- The same state layer as 014, i.e. any type-2 or type-3 location in the state,
  broadcast to every county in the state.
- NULL when the state had no stories in the window. The fill is 0, matching
  `gdelt_state_tone_mean_21d` in the lift notebook; `stories_21d` is not added, so the
  arm is baseline + GCAM columns only.

**Design.** Identical to `gdelt_lift.ipynb`:
- same rows, split (2025-01-01) and hash-sampled negatives;
- same RF (300 trees, max_depth 6, min_samples_leaf 2, 3 seeds);
- outage rows dropped from every arm;
- paired week-block bootstrap, 300 reps.

**Arms**
- A: baseline, 23 predictors.
- G: baseline + passing GCAM columns.
- There is no volume arm. Volume was already tested and was null or harmful.

**Claim rule.** Lift is claimed only if the 95% CI of the paired ΔPR-AUC (G − A)
excludes 0, on all positives or on the new-onset slice (as defined in `gdelt_lift.ipynb`).
Both slices are reported whatever the result.

## Decisions I'm not making in advance, and how they'll be handled

- **County-level GCAM:** not tested. Volume at county level was null, and affect over a
  handful of articles per county is too noisy to pre-specify a useful form.
- **Non-English articles:** GCAM English dictionaries score English text only. The share
  of non-English articles in the corpus is reported, not modelled.
