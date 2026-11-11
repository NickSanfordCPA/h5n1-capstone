# Meta utility analysis: design (exploratory)

*Written 2026-11-11. **Nick decided the Meta run is exploratory**: the 2025+ window was scored with
`meta_utility.py exploratory` without a frozen gate, and every result is labelled exploratory, not a
confirmatory test. The design below was nonetheless fixed before 2025+ was scored.*

## Question

What are the Meta (Facebook) posts worth for predicting USDA-confirmed H5N1 poultry outbreaks, at the
state level? This replicates the X utility analysis (`docs/x_utility_spec.md`, frozen 2026-10-31) on a
second source. The design, arms, features, primaries and success rules are X's, unchanged. Only the
input and the source-forced departures below differ.

Every choice was made on 2022-08-07..2024-12-31 data, and the design was fixed before any 2025+ outcome
was scored for Meta. The 2025+ post counts and coverage below were computed from posts alone. The
2025+ results are in `docs/meta_utility_results.md`.

## Input and funnel

- **Source.** Muthu's Meta Content Library UI export, loaded into Cloud SQL by Max (`meta_post*`).
  The funnel starts from Max's 30,369 geocoded posts (`meta_post_analysis WHERE use_sentiment`). His
  steps before that point are taken as given: dedup, geocoding from post text, and the foreign,
  multi-state, keyword, copypasta and hashtag cuts. His build code is not in the repo.
- **Funnel.** `h5n1/sources/meta_funnel.py` applies the X funnel (`docs/x_data_funnel.md`). It writes
  the table `meta_post_funnel`, with one row per post, its disposition, `use_sentiment` and
  `use_sightings`.

| Step | Disposition | en | es |
|---|---|---:|---:|
| 2 | Outside date window (before 2022-06-01) | 4,354 | 547 |
| 3 | Reshare | 369 | 23 |
| 6 | Hub author | 4,624 | 909 |
| 7 | Copypasta repeat | 115 | 58 |
| | **Filtered subset** (`use_sentiment`) | **15,400** | **3,970** |
| 8 | Not a local observation | 13,428 | 3,609 |
| 9 | No sick or dead animals | 425 | 15 |
| 10 | Retrospective | 115 | 56 |
| 11 | Outside the US | 85 | 176 |
| 12 | Not HPAI | 27 | 9 |
| 14 | Not credible | 3 | 0 |
| 15 | Same state-week | 819 | 62 |
| | **Sighting** (`use_sightings`) | **498** | **43** |

- **Sentiment.** Max's `meta_post_sentiment`, scored with cardiffnlp/twitter-xlm-roberta-base-sentiment,
  the same model as Waree's `x_post_sentiment`. Verified 2026-11-11: 200 posts rescored independently
  matched to 0.0 difference. Sentiment = positive − negative. Every subset post is scored.

## Departures from X, all forced by the source

- **Streams.** en and es, split on `lang`. Meta has one collector throughout, so there is **no collector
  break**. The hub window is pre-2025 for both streams.
- **Geography.** The state is where the post TEXT names a place (Max's geocoder), not where the author
  lives. Meta gives no author location. Track A therefore measures tone about a state, not tone from it.
- **Step 3.** Meta has no quote posts, so reshares are dropped as their analog.
- **Step 6.** Authors are Pages and profiles with no bio. Account types were labelled from the name,
  username and type (723 authors, by Sonnet readers under Appendix A). 531 author-streams are hubs.
- **Steps 8–9: guide v2.** Most Meta posts come from news and agency Pages.
  - Under the unchanged X guide, readers split 7–24% per batch on official announcements. An
    announced poultry-flock detection is also the outcome itself.
  - Nick's rule (2026-11-11): a poultry-flock detection announcement is never a local observation.
    These are recorded in `poultry_announcement` (2,832 posts, plus 11 caught by audit).
  - Wild-bird and mammal die-offs at a named place, at county level or smaller, count, as in X.
  - All 39 batches were labelled under v2. The v1 wave is archived. Sick-or-dead rates per batch
    now run 7.8–12.6%.
  - Guide: `data/Meta_derived/labels/GUIDE_local_obs.md`.
- **Steps 10–14.** The exclusion reader saw the post's geocoded state, where X's saw the author's
  profile state. English was split into four date-ordered chunks of about 390 posts, because one
  context could not hold the full set; Spanish was one chunk. All readers worked from the same guide
  (`GUIDE_exclusion.md`).
- **Corrections.** 45 reviewed overrides of reader answers, each with a reason, in
  `labels/corrections.csv`:
  - 29 posts echoing a 2024 study of 2022–23 New York City park birds → retrospective;
  - 2 reposts of a December 2024 Washington sanctuary event → retrospective;
  - 1 June 2025 post about flock detections the previous December → retrospective;
  - 2 human-case posts → not animal sightings;
  - 11 leaked poultry announcements, found by a single-reader audit of the 209 kept posts that use
    poultry words.
- **Pseudocount.** Re-estimated on Meta posts through 2024, without outcomes: **m = 3.3342**. X's was
  10.58, so Meta tone varies more between state-weeks than X's did.

## Shared design, Track A and Track B: identical to X

As in `docs/x_utility_spec.md`:
- **Grain and baseline.** State-week grain, 49 CONUS states plus DC. Baseline S3 is a shallow RF with
  three seeds. Training runs 2022-08-07 to 2024-12-31 minus k. The test starts on Sundays from
  2025-01-05.
- **Track A.**
  - Features: covered rows only, nothing imputed, and the same three frozen features (`x_share_7d`,
    `x_sent_7d`, `x_sent_chg`, built from Meta posts; the x_ prefix is kept so the X code runs
    unchanged).
  - Arms: S0–S4v/S4s.
  - **Primary:** S4 − S3 PR-AUC, k=14, state new onset, paired week-block bootstrap, 2,000 reps, 95% CI.
  - **Success:** the CI excludes 0 on the positive side, and the point estimate stays positive under
    the 5-day embargo.
- **Track B.**
  - Rows: one row per sighting at its state-week, using `event_state`, CONUS only.
  - Expected risk comes from S3 with Platt calibration.
  - **Primary:** O/E ratio, k=14, all sightings, week-block bootstrap, 2,000 reps, 95% CI.
  - **Success:** the CI lower bound is above 1, and the point estimate stays above 1 with the first 5
    days excluded.
- **Secondary analyses.** The same list as X, labelled as such, with no correction.

The X spec's collector-break caveats do not apply. Its quarter split is kept as a check on period
dependence: the primary is reported for 2025-Q1 and for later weeks separately.

## Coverage (posts only, no outcomes)

- **Covered state-weeks.** The share with at least 1 subset post in the trailing 7 days was 14–48% per
  quarter in 2022–24. It was 77% in 2025-Q1 and 24–41% afterwards. The share with at least 5 posts was
  1–12%, and 38% in 2025-Q1.
- **Sightings.** CONUS sightings pooled one per state-week: 2022 99, 2023 61, 2024 59, 2025 207,
  2026 66. The test weeks hold 277 sightings: those 273, plus 4 posted 2024-12-30..31 whose week ends
  on 2025-01-05. 275 have a known k=14 outcome; the last 2 are too recent. X had 62.

## Exploration record

None of this is evidence for the claim, and none of it is reused as a prior. It was run on
2022-08..2024-12 folds after the funnel and pseudocount were fixed. No choice was changed afterwards.

- **Track A, primary** (k=14, new onset): Δ +0.0091, CI [−0.0036, +0.0325], 52 positives.
- **Track A, volume:** S4v on all rows, +0.0066 [0.0006, 0.0132]. It vanishes under the 5-day embargo
  (−0.0017), so it reads as reactive.
- **Track A, controls.** The planted column wins (+0.31) and the shuffled column does not (−0.005).
- **Track B, primary** (k=14, all): O/E ratio 1.05, CI [0.87, 1.24], 168 sightings. At k=7 it is 1.16
  [0.93, 1.40].
- **Track B, within sighting weeks.** Post volume and sentiment add nothing beyond logit(S3).

## Known limits, stated before the test

- **Location.** Location comes from post text matched by an unreviewed geocoder, and Pages are mostly
  news. Both tracks therefore lean towards news about a state.
- **Exclusion of the outcome.** Announcements of the outcome are excluded by LLM judgement, not by a
  rule. The audit found 11 leaks in 209 posts that use poultry words; posts without those words were
  not audited.
- **Reader consistency.**
  - Mixed poultry-plus-wildlife posts were settled after wave 1, about 1 post per batch.
  - Some readers accepted sub-state regions ("eastern Arkansas"); the guide asks for county level
    or smaller.
- **Data access.** Settled 2026-11-11 (Nick): the team is allowed to save this export in the shared
  database.
