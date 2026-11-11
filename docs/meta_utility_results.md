# What is the Meta data worth? Exploratory results on 2025+

*2026-11-11. **Exploratory.** Nick chose not to freeze a gate for Meta, so nothing here is a
confirmatory test. The design (`docs/meta_utility_spec.md`) was fixed, and explored on 2022-24 only,
before any Meta data was scored against a 2025+ outcome. The funnel is in `docs/meta_data_funnel.md`.*

This analysis repeats the X utility analysis (`docs/x_utility_results.md`) on a second source: public
Facebook posts about bird flu, from a Meta Content Library export. It asks whether those posts help
predict USDA-confirmed H5N1 poultry outbreaks beyond what surveillance data already shows. Everything
is at state level and uses the same state baseline (S3), forecast windows and measures as X.

Meta posts enter in two ways, tested separately:
- **Volume and sentiment** of the posts that name a state are added to the models as features.
- **Sightings**, the 541 posts reporting sick or dead wild birds or animals at a named place, are
  treated as alerts. We check whether outbreaks follow them more often than the baseline expects.

Most Meta posts come from news outlets and agencies, so the state is the place a post talks about,
not where its author lives. Announcements of poultry-flock detections, the outcome itself, were
excluded from sightings by rule.

## The models

### Track A: sentiment and volume

Track A adds the volume and sentiment of Meta posts to the state models as features. It asks whether
forecasts improve on state-weeks that have at least one post.

The PR-AUC column is from 2025+, for an outbreak within 14 days (k=14) in states with no outbreak in
the prior 30 days (new onset). Higher is better; a model with no skill scores the base rate of
**0.162**. The three Meta features are those frozen for X: the state's share of national posts, its
7-day sentiment, and the change in sentiment.

| Arm | Contents | Question | PR-AUC |
|---|---|---|---:|
| S0 | Place, season, fixed poultry exposure | What do those alone give? | 0.332 |
| S1 | Meta only | The Meta-only model | 0.138 |
| S2 | S0 + Meta | Does Meta know anything beyond season and place? | 0.287 |
| S3 | State baseline: S0 plus recent outbreaks, neighbouring states, wild-bird detections, migration, weather | Reference | 0.395 |
| S4 | S3 + Meta | Does Meta add beyond surveillance data? | 0.374 |
| S4v | S3 + volume only | Does volume contribute? | 0.393 |
| S4s | S3 + sentiment only | Does sentiment contribute? | 0.387 |

### Track B: sightings

Track B treats each sighting as an alert. It asks whether an outbreak follows in the sighting's state
within 14 days more often than the baseline predicted for that week. Comparisons are on 2025+, at k=14.

| Comparison | Contents | Question | Result |
|---|---|---|---:|
| Observed vs expected | Outbreaks after sightings against S3's prediction for those weeks, relative to weeks with no sighting | Do sightings add anything beyond the baseline? | O/E ratio **1.22** |
| Season and place | The same state at the same time of year in other years | Are outbreaks more common after a sighting than usual for that state and season? | 48.7% vs 28.6% |
| Which sightings | Post volume, sentiment and S3's risk, fit on the 275 sightings as description | Do some sightings signal more than others? | Only S3's risk separates them |

## What we found

**Sentiment and volume add nothing; if anything they hurt.**
- On new onsets, adding Meta to the baseline lowered PR-AUC from 0.395 to 0.374. The change is
  −0.021 (95% CI −0.032 to −0.004).
- Meta also made season-and-place forecasts worse (S2 below S0).
- On all state-weeks the change is +0.002 (−0.004 to +0.008).
- No feature, horizon or check reaches significance:
  - the offset logistic: joint p = 0.50;
  - the block permutation: p = 0.12 for all rows, 0.98 for new onsets.
- Meta's unique share of explained deviance is 0.02–0.12%.
- This is the same answer as X. Two sources and two kinds of authors (X's public, Meta's news Pages)
  agree: tone and volume about bird flu in a state do not forecast its next outbreak.

**Sightings are followed by more outbreaks than the baseline expects.**
- After a Meta sighting, 49% of states had an outbreak within 14 days. S3 had predicted 115 of
  those 275 weeks to be followed by one; 134 were.
- Relative to weeks without a sighting, the O/E ratio is **1.22 (95% CI 1.07 to 1.39)**. It stays
  above 1 with the first 5 days after the post excluded: 1.25 (1.04 to 1.48).
- **It is consistent across horizons and periods:**

  | Check | O/E ratio (95% CI) |
  |---|---|
  | k=7 | 1.26 (1.08 to 1.47) |
  | k=30 | 1.22 (1.11 to 1.34) |
  | 2025-Q1 | 1.17 (1.04 to 1.34) |
  | Later weeks | 1.21 (0.98 to 1.49) |

- **X found the opposite on the same test:** 0.85 (0.67 to 1.09) on 62 sightings.
- **On X's pre-set rule this would pass.** It is not a confirmed result, because Meta was not gated.
- **It did not show up in exploration.** On 2022-24 the same measure was 1.05 (0.87 to 1.24). A
  signal that appears only in the scored window deserves caution, even though 2025+ has 275
  sightings against 168.

**What a Meta sighting is.**
- It is usually local news or an agency notice of a wildlife die-off: dead geese at a reservoir,
  vultures at a park, sick snow geese in a county.
- S3 already includes USDA wild-bird detection counts, so the signal is beyond them. The likely
  reason is timing. News of a die-off appears days to weeks before the birds are lab-confirmed and
  enter the detection data.
- That is real early warning, but it is not specific to social media. A system that reads state
  wildlife agencies' and local newsrooms' announcements directly would get the same information.

**Sightings cover few outbreaks.** Only 19% of state-weeks followed by an outbreak had a sighting.

## How it was tested

| | Track A: sentiment and volume | Track B: sightings |
|---|---|---|
| **Question** | Does Meta improve the state baseline? | Given a sighting, is an outbreak more likely than the baseline expects? |
| **Rows** | State-weeks with at least 1 Meta post | One row per sighting; the model runs only when one is posted |
| **Meta input** | State share of national posts; 7-day sentiment; change in sentiment | The sighting itself |
| **Primary measure** | Change in PR-AUC, new onsets, 14 days | O/E ratio, all sightings, 14 days |
| **X's rule to pass** | 95% interval above 0, and still positive with Meta ending 5 days earlier | 95% interval above 1, and still above 1 with the first 5 days excluded |
| **Test size** | 825 rows, 134 positive | 275 sightings |

Training is 2022-08-07 to 2024-12-31 minus k. The test runs on Sundays from 2025-01-05 to the last
outbreak date. Intervals are week-block bootstraps with 2,000 replicates. Terms are as in
`docs/x_utility_results.md`.

**Differences from X.**
- **The source.** Meta posts are mostly from Pages, located by the place their text names.
- **Sightings exclude poultry announcements.** Most news Pages announce flock detections, which are
  the outcome itself, so a poultry announcement is never a sighting. The audit and sensitivity check
  below test how well that held.
- **No collector break.** Meta has one collector throughout. Coverage is 24–77% of state-weeks per
  quarter in 2025+, against X's collapse to 22–46%.

## Track A results: sentiment and volume

PR-AUC on 2025+ by horizon and slice. **Bold** is the primary cell.

| k | Rows | Base rate | S0 | S1 (Meta only) | S2 | S3 | S4 |
|---|---|---|---:|---:|---:|---:|---:|
| 7, all | 1,582 | 0.199 | 0.342 | 0.245 | 0.365 | 0.555 | 0.557 |
| 7, new onset | 840 | 0.094 | 0.216 | 0.090 | 0.183 | 0.252 | 0.243 |
| 14, all | 1,565 | 0.291 | 0.489 | 0.319 | 0.492 | 0.632 | 0.633 |
| **14, new onset** | **825** | **0.162** | **0.332** | **0.138** | **0.287** | **0.395** | **0.374** |
| 30, all | 1,522 | 0.425 | 0.671 | 0.464 | 0.663 | 0.756 | 0.758 |
| 30, new onset | 787 | 0.266 | 0.505 | 0.244 | 0.483 | 0.568 | 0.569 |

| Secondary check, k=14 | All rows | New onsets |
|---|---|---|
| S4 − S3 | +0.0015 (−0.0039 to +0.0075) | −0.0209 (−0.0324 to −0.0036) |
| Volume only, S4v − S3 | +0.0025 (−0.0018 to +0.0063) | −0.0027 (−0.0133 to +0.0092) |
| Sentiment only, S4s − S3 | +0.0002 (−0.0044 to +0.0053) | −0.0079 (−0.0195 to +0.0112) |
| Meta ending 5 days earlier | +0.0013 (−0.0029 to +0.0055) | −0.0099 (−0.0224 to +0.0072) |
| Only the part of Meta the baseline can't explain | −0.0008 (−0.0051 to +0.0036) | −0.0092 (−0.0214 to +0.0087) |
| At least 5 posts (descriptive) | +0.0069 (−0.0025 to +0.0175) | −0.0063 (−0.0434 to +0.0287) |
| Block permutation p | 0.12 | 0.98 |
| Meta's unique share of explained deviance | 0.02% | 0.12% |

**2025-Q1 and later weeks**, S4 − S3 on new onsets:
- 2025-Q1: −0.039 (−0.066 to −0.008), 44 positives.
- Later weeks: −0.005 (−0.025 to +0.015), 90 positives.

The harm is concentrated in the first quarter. That is the 2025 peak, when Meta coverage was highest
(77% of state-weeks) and posting tracked the epidemic everywhere at once.

**Coverage.** Of state-weeks followed by a new-onset outbreak within 14 days, 53% had a Meta post. For
all outbreaks the figure is 65%.

## Track B results: sightings

| Test (k, days excluded, slice, period) | Sightings | Followed | Rate | O/E ratio | 95% CI |
|---|---:|---:|---:|---:|---|
| 7, 0, all, all | 275 | 99 | 0.36 | 1.26 | 1.08 to 1.47 |
| **14, 0, all, all** | **275** | **134** | **0.49** | **1.22** | **1.07 to 1.39** |
| 14, 5, all, all | 275 | 91 | 0.33 | 1.25 | 1.04 to 1.48 |
| 14, 0, all, 2025-Q1 | 132 | 72 | 0.55 | 1.17 | 1.04 to 1.34 |
| 14, 0, all, later weeks | 143 | 62 | 0.43 | 1.21 | 0.98 to 1.49 |
| 14, 0, new onset, all (descriptive) | 88 | 24 | 0.27 | 1.34 | 0.91 to 1.84 |
| 30, 0, all, all | 275 | 186 | 0.68 | 1.22 | 1.11 to 1.34 |

**Same state and season in other years:**
- At k=14, 28.6% of matched weeks were followed by an outbreak, against 48.7% after a sighting.
- In 2,000 draws, a matched rate never reached the observed rate.
- **Recall:** sightings flagged 19% of outbreak state-weeks at k=14, and 9% of new-onset ones.

**Which sightings.** On the 275 sightings, S3's risk predicts which are followed by an outbreak
(β 1.00, p < 0.001). Post volume and sentiment add nothing:

| Term | β | p |
|---|---:|---:|
| Post volume | 0.11 | 0.51 |
| Post sentiment | 0.17 | 0.24 |

**Sensitivity: sightings that mention poultry removed.**
- This is the strongest check against leaked poultry announcements.
- Every sighting whose text uses a poultry word is dropped, whatever it describes: 120 of the
  scored set, leaving 216.
- The O/E ratio barely moves:

  | Check | O/E ratio (95% CI) |
  |---|---|
  | k=14 | **1.20 (1.04 to 1.40)** |
  | First 5 days excluded | 1.19 (0.97 to 1.43) |
  | k=7 | 1.20 (1.01 to 1.41) |
  | k=30 | 1.18 (1.06 to 1.29) |

- With the first 5 days excluded the interval now reaches below 1, but the point estimate stays
  above 1.
- The signal does not come from posts that talk about poultry. That rules out the obvious leak,
  though not a subtler one.
- This was a one-off script that reruns `x_sightings.final` with those sightings filtered out; it is
  not in the repo. Output: `data/Meta_derived/meta_utility/explore2025_nopoultry_sightings.csv`.

## Limits

- **Exploratory.** These results were not gated. In particular, Track B's positive result
  appeared only on 2025+. It needs to hold on a new window, or a second source, before it is
  presented as a finding.
- **The outcome was excluded by judgement.**
  - Poultry announcements were removed by LLM readers, and an audit found 11 that got through in
    209 posts using poultry words.
  - Leaks would inflate Track B. Dropping every sighting that uses a poultry word leaves the ratio
    at 1.20 (1.04 to 1.40), so the obvious leak is not the source.
- **Location comes from the text.** The geocoder misplaced about 1 in 7 sick-or-dead posts, mostly
  foreign places; the exclusion screen caught those it saw. Max's geocoder is not in the repo.
- **Sightings are news, not crowd observation.** The value found is early news of wildlife
  mortality, available from agencies and newsrooms directly.
- **Data access.** Settled 2026-11-11 (Nick): the team is allowed to save this export in the shared
  database.
