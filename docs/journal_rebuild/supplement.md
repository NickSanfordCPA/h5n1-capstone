# Capstone supplement

*Written 2026-11-18. For the capstone report only; none of this is in the journal manuscript.
X numbers updated 2026-11-19 after Max's re-pull of the 2022-2024 English X posts, and again
the same day after he filled in the query-days that pull had missed. Parts 3 to 7 added
2026-11-20.*

The journal manuscript reports one analysis. The capstone asked for more than that, and the
project took several turns before reaching it. This supplement holds what the manuscript
leaves out.

| Part | Contents |
|---|---|
| 1 | Language comparison of the two primary results (sections "The models" to "Findings: language") |
| 2 | Emoji comparison ("Findings: emoji") |
| 3 | Sentiment compared between English and Spanish posts |
| 4 | Language yield: why Spanish and French searches return so few usable posts |
| 5 | County-level baseline |
| 6 | GDELT news results |
| 7 | Data history |
| 8 | Commitments ledger: every item in the proposal, and what became of it |

## What this analysis does

The journal analysis pools English and Spanish posts and scores sentiment on the text as
posted, emoji included. This supplement asks two questions the capstone raised and the paper
does not: do the two primary results look different when only English or only Spanish posts
are used, and does it matter whether emoji are left in the text when sentiment is scored?

Both questions reuse the journal pipeline unchanged. The state-week table, the baseline
forest (S3, searched setting, five seeds), the calibrated expected risk, the 2025-2026 test
period and the week-block bootstrap (2,000 replicates) are the ones in `PROTOCOL.md`. Only
the posts change. Nothing here was specified in the protocol beyond the one line that a
language and emoji comparison would be made, so every number is descriptive and none is
corrected for multiple comparisons.

Code: `h5n1/journal/supplement.py`. Outputs: `data/journal/supplement/` (`language_a.csv`,
`language_b.csv`, `emoji_posts.csv`, `emoji_transitions.csv`, `emoji_a.csv`,
`supplement.log`).

## The models

- **Track A** compares the baseline with three post features (the state's share of posts in
  the last 7 days, shrunk mean sentiment, and its change) against the baseline alone. The
  measure is the difference in PR-AUC at 14 days on new-onset state-weeks that had at least
  one post. A positive difference means posts helped.
- **Track B** takes state-weeks with a sighting (a post reporting sick or dead animals at a
  named local place) and divides the outbreaks observed in the next 14 days by the number the
  baseline expected, relative to state-weeks without a sighting. A ratio above 1 means
  sightings mark more outbreaks than the baseline already expects.

For the language comparison, each track was run once with English posts only and once with
Spanish posts only. Language is the detector's label from funnel step 2, the same detector on
both platforms.

For the emoji comparison, every sentiment-set post containing an emoji was rescored by the
same sentiment model (XLM-T) with the emoji removed, and Track A was rerun with those scores
in place of the originals. Emoji do not enter Track B, which uses no sentiment.

## Findings: language

**How much Spanish there is.** Spanish is a meaningful share of Facebook and a small share of
X.

| | Facebook, English | Facebook, Spanish | X, English | X, Spanish |
|---|---:|---:|---:|---:|
| Sentiment-set posts | 15,835 | 3,507 | 14,221 | 1,535 |
| Sighting posts | 1,503 | 167 | 1,253 | 64 |
| Sighted state-weeks, test period | 282 | 40 | 424 | 7 |

Every X state-week that has a sighting has an English one, so the English-only X result for
Track B is the pooled result.

**Track A shows no gain in either language.** Change in PR-AUC from adding post features, 14 days,
new-onset state-weeks (95% CI):

| Posts used | State-weeks (events) | Facebook | State-weeks (events) | X |
|---|---|---|---|---|
| Both languages (journal primary) | 490 (86) | −0.000 (−0.013 to 0.011) | 588 (97) | 0.000 (−0.011 to 0.011) |
| English only | 478 (86) | 0.002 (−0.010 to 0.015) | 581 (95) | −0.003 (−0.010 to 0.004) |
| Spanish only | 66 (11) | −0.006 (−0.023 to 0.011) | 22 (5) | 0.103 (−0.000 to 0.341) |

The Spanish-only X cell has 5 events and cannot be read. With post windows ending 5 days
early, every cell's interval again includes zero.

**Track B: the Facebook excess is an English-post result.** O/E ratio, sighted against other
state-weeks, 14 days (95% CI):

| Posts used | Facebook: followed / sighted (expected) | Facebook ratio | X: followed / sighted (expected) | X ratio |
|---|---|---|---|---|
| Both languages (journal primary) | 136 / 290 (112.9) | 1.21 (1.06 to 1.37) | 164 / 424 (143.5) | 1.15 (0.99 to 1.32) |
| English only | 133 / 282 (110.4) | 1.21 (1.06 to 1.38) | 164 / 424 (143.5) | 1.15 (0.99 to 1.32) |
| Spanish only | 12 / 40 (16.9) | 0.69 (0.43 to 0.96) | 4 / 7 (4.0) | 0.98 (0.33 to 1.68) |

- English-only sightings reproduce the pooled result on both platforms, so Spanish posts
  neither create nor mask the Track B excess.
- State-weeks with a Spanish-language Facebook sighting were followed by *fewer* outbreaks
  than expected (12 against 16.9). This rests on 40 state-weeks and one of several unplanned
  cells; with the first 5 days excluded it is 0.79 (0.37 to 1.21). It should be read as "no
  excess in Spanish posts", not as evidence of a protective signal. Why Spanish posts would
  differ was not investigated.
- With the first 5 days excluded, English-only ratios are 1.18 (0.96 to 1.42) on Facebook and
  1.30 (1.08 to 1.55) on X, the same as the pooled figures.

## Findings: emoji

**How common emoji are.** Of 35,098 sentiment-set posts, 6,042 (17%) contain an emoji. They
are far more common in Spanish posts.

| | Posts | With an emoji | Share |
|---|---:|---:|---:|
| Facebook, English | 15,835 | 1,552 | 10% |
| Facebook, Spanish | 3,507 | 1,634 | 47% |
| X, English | 14,221 | 2,262 | 16% |
| X, Spanish | 1,535 | 594 | 39% |

**Removing emoji barely moves the score.** Among posts with an emoji, the mean sentiment
score (positive minus negative probability, from −1 to 1) changed by 0.01 or less in every
platform and language cell; the mean absolute change per post was 0.04 to 0.05. The predicted
label changed for 4% to 7% of posts.

| | Mean score, as posted | Mean score, emoji removed | Mean absolute change | Label changed |
|---|---:|---:|---:|---:|
| Facebook, English | −0.424 | −0.422 | 0.042 | 5.9% |
| Facebook, Spanish | −0.593 | −0.595 | 0.037 | 5.3% |
| X, English | −0.497 | −0.491 | 0.047 | 5.7% |
| X, Spanish | −0.611 | −0.616 | 0.035 | 4.2% |

Label changes ran in both directions and nearly cancelled. On Facebook, 72 posts moved from
negative to neutral when emoji were removed and 86 moved from neutral to negative; on X, 73
and 69. Moves between negative and positive were rare (4 on Facebook, 6 on X). In these
posts, which are mostly relayed news, emoji carry little of the tone the model reads. (Which
emoji are used was not tabulated.)

**Track A does not change.** Change in PR-AUC, 14 days, new-onset state-weeks (95% CI):

| Sentiment scores | Facebook | X |
|---|---|---|
| As posted (journal primary) | −0.000 (−0.013 to 0.011) | 0.000 (−0.011 to 0.011) |
| Emoji removed | 0.000 (−0.013 to 0.011) | 0.000 (−0.012 to 0.011) |

The result with windows ending 5 days early is equally unchanged (Facebook −0.004 as posted,
−0.005 with emoji removed; X −0.002 and −0.003; every interval includes zero).

## What this means for the capstone

- The paper's choice to pool the two languages costs nothing: the results are the
  English-post results, and Spanish posts are too few (and on X far too few) to support a
  separate conclusion.
- The earlier capstone concern that emoji were scored as zero by the lexicon engine does not
  carry over. XLM-T reads emoji, and whether it reads them or not makes no difference to
  sentiment at the state-week level or to the forecast.
- The one cell that departs from the pooled picture, Spanish-language Facebook sightings,
  is small and unplanned. It is worth a sentence in the capstone report and no more.

## Limits

- The emoji pattern covers the main pictograph blocks, dingbats, miscellaneous symbols,
  variation selectors, the zero-width joiner and keycaps. Text emoticons such as ":(" were
  left in.
- The rescoring ran on CPU; the original scores ran on GPU. A 300-post comparison of the two
  agreed to within 0.00001, so hardware is not a source of difference.
- Language is the detector's label for the whole post. Mixed-language posts are counted under
  the label they received.
- Not independently recomputed. `supplement.py run` was executed once.

## Part 3. Sentiment compared between English and Spanish posts

**What this does.** The proposal's first hypothesis was that sentiment differs between
English posts and posts in other languages. This part compares the sentiment score of
English and Spanish posts in the sentiment set (posts a reader judged to be about avian
influenza in one US state). The score is the model's positive probability minus its negative
probability, from −1 to 1. Nothing was translated: the model (XLM-T) scores each language
directly.

**Spanish posts score more negative on both platforms.** Mean score, with the difference and
a 95% interval from 2,000 bootstrap resamples of posts:

| | English posts | Spanish posts | English mean | Spanish mean | Spanish minus English |
|---|---:|---:|---:|---:|---|
| Facebook, all | 15,835 | 3,507 | −0.478 | −0.593 | −0.116 (−0.124 to −0.106) |
| Facebook, 2022-24 | 8,777 | 2,115 | −0.477 | −0.587 | −0.110 (−0.122 to −0.098) |
| Facebook, 2025-26 | 7,058 | 1,392 | −0.479 | −0.603 | −0.124 (−0.139 to −0.110) |
| X, all | 14,221 | 1,535 | −0.463 | −0.600 | −0.137 (−0.149 to −0.124) |
| X, 2022-24 | 8,830 | 1,087 | −0.460 | −0.596 | −0.136 (−0.151 to −0.121) |
| X, 2025-26 | 5,391 | 448 | −0.469 | −0.611 | −0.142 (−0.165 to −0.119) |

The gap comes from more negative and fewer neutral or positive posts. Mean class
probabilities:

| | Negative | Neutral | Positive |
|---|---:|---:|---:|
| Facebook, English | 0.563 | 0.351 | 0.085 |
| Facebook, Spanish | 0.643 | 0.307 | 0.050 |
| X, English | 0.532 | 0.399 | 0.069 |
| X, Spanish | 0.635 | 0.331 | 0.035 |

**The gap holds within each kind of post.** Mean score by the reader's content class
(first-hand accounts are left out: 2 Spanish posts on Facebook and 1 on X):

| Content class | Facebook, English | Facebook, Spanish | X, English | X, Spanish |
|---|---:|---:|---:|---:|
| Announced domestic detection | −0.537 | −0.589 | −0.488 | −0.638 |
| Wildlife report | −0.564 | −0.701 | −0.512 | −0.700 |
| Other | −0.441 | −0.576 | −0.438 | −0.573 |

**Reading.**

- The hypothesis is supported as a description: Spanish posts about US avian influenza
  score 0.11 to 0.14 lower than English ones, in both periods, on both platforms and within
  each content class.
- The comparison cannot say why. Three explanations are not separated here: Spanish-language
  writers express more negative tone; the model scores Spanish text more negatively for the
  same content; or the two languages cover different stories. No bilingual post pairs were
  scored and no human rated tone, so the model explanation cannot be ruled out.
- The difference does not matter for the forecast. Track A is null in both languages
  (Part 1).
- The interval treats posts as independent. Many posts repeat one news story, so the true
  uncertainty is wider than shown.

Code: computed for this supplement from `journal.post_sentiment` and
`data/journal/posts/label_funnel.parquet`; not part of `supplement.py`.

## Part 4. Language yield

**What this does.** The collection searched in English, Spanish and French. This part counts
where the posts in each language went, to explain why Spanish contributes little and French
nothing.

**Posts collected, by search language.**

| | English | Spanish | French |
|---|---:|---:|---:|
| Facebook, unique posts (detected language) | 172,385 | 127,665 | 18 |
| X, unique posts (detected language) | 209,642 | 78,813 | 20,715 |

Facebook was searched with English and Spanish terms only. Another 976 Facebook posts and
1,032 X posts were in some other language.

**Where they went.** Each post is removed by the first rule that applies.

| Step | Facebook, English | Facebook, Spanish | X, English | X, Spanish | X, French |
|---|---:|---:|---:|---:|---:|
| Unique posts | 172,385 | 127,665 | 209,642 | 78,813 | 20,715 |
| Not English or Spanish | 0 | 0 | 0 | 0 | 20,715 |
| Outside the analysis window | 21,811 | 8,618 | 19,729 | 7,990 | |
| Quote or reshare | 3,596 | 1,377 | 18,691 | 4,271 | |
| Not about avian influenza | 24,508 | 3,477 | 18,056 | 3,806 | |
| No US state named | 93,161 | 106,730 | 132,479 | 60,664 | |
| Names more than one US state | 3,256 | 1,375 | 2,408 | 192 | |
| Copypasta repeat | 5,571 | 638 | 345 | 44 | |
| **Sent to the readers** | **20,482** | **5,450** | **17,934** | **1,846** | **0** |
| Share of unique posts | 11.9% | 4.3% | 8.6% | 2.3% | 0% |
| Kept by the readers (sentiment set) | 15,835 | 3,507 | 14,221 | 1,535 | |
| Sighting posts | 1,503 | 167 | 1,253 | 64 | |

**Why Spanish yields so little: the posts are about other countries.**

- The searches had no country filter. A Spanish search for "gripe aviar" returns posts from
  Spain and Latin America as well as the United States.
- The funnel keeps a post only if its text names exactly one US state. That rule removes
  84% of Spanish Facebook posts and 77% of Spanish X posts, against 54% and 63% of English
  ones.
- Among posts that reached the place rule, a foreign place was named and no US state in 42%
  of Spanish Facebook posts (19% of English) and 32% of Spanish X posts (17% of English).
  One US state was named in 5% of Spanish Facebook posts (21% of English) and 3% of Spanish
  X posts (12% of English).
- The author's profile location on X tells the same story: it points to a US place for 1.9%
  of Spanish posts and 21% of English posts.
- The readers then removed a further 36% of the Spanish Facebook posts sent to them (23% of
  English). On X they removed 17% of Spanish and 21% of English posts.

**Why French yields nothing.**

- French was searched on X only. Of 20,715 French posts, 19,282 mention avian influenza or
  H5N1, but only 1,492 mention the United States at all.
- The geocoder found exactly one US state in 329 French posts, 257 of them inside the
  analysis window and not quotes. The geocoder knows English and Spanish state names, so
  French spellings such as "Californie" are missed and the true number is somewhat higher.
- In October 2026, before the journal rebuild, the French posts were located by profile
  location instead: 141 pointed to a US state, all were read, and none reported sick or dead
  animals. French was dropped on that result. (From project notes; not rerun.)
- French was therefore removed at the language step in the rebuild, and no French post was
  sent to a reader. The 329 posts above were never read under the current guide.

**Limits.**

- Yield is measured against what the searches returned. The Spanish and French search terms
  used on X are not recorded with the data (the posts carry only the group names `phrases`
  and `subtypes_and_rest`), so whether the Spanish and French queries were as broad as the
  English ones was not checked.
- Spanish and French X pulls were not checked for missing days by query group, as the
  English pulls were (Part 7).
- A Spanish-language post about a US outbreak that names only a city or county, without the
  state, is removed at the place rule like any other.

## Part 5. County-level baseline

**What this does.** The project began with the county-day as its unit: one row per county
per day, and the outcome "a confirmed poultry detection in this county in the next k days".
The journal analysis moved to the state-week, because posts can be placed in a state far
more often than in a county. The county model was kept as a check. This part reports how the
county baseline performs and what was learned while building it. No social media enters any
model in this part.

**Why posts could not be used at county level.** In the sentiment set, the geocoder placed
17% of Facebook posts (3,356 of 19,342) and 13% of X posts (2,029 of 15,756) in a county.
The rest name only a state. The readers judged that 32% of Facebook and 29% of X posts
describe something at a named local place, but most of those do not resolve to one county by
rule.

**Model.** A random forest on 35 features (wild-bird detections, weather, poultry
inventory, population, migration pressure, and the county's and its neighbours' recent
detections). Training rows were every county-day with an outcome event and a 5% sample of
the rest, weighted by 20. Two settings were used: one chosen by a grid search on 2022-2024
folds (500 trees, depth 12, minimum leaf 50) and the fixed setting from the team's earlier
baseline (300 trees, depth 6).

**County performance, test period (2025-26), every county-Sunday.**

| Horizon | Events / county-Sundays | Outcome rate | Searched forest PR-AUC | Earlier fixed forest PR-AUC |
|---|---:|---:|---|---|
| 7 days | 622 / 245,532 | 0.25% | 0.143 (57 times the rate) | 0.157 (62 times) |
| 14 days | 1,112 / 242,424 | 0.46% | 0.128 (28 times) | 0.137 (30 times) |
| 28 days | 1,973 / 236,208 | 0.84% | 0.128 (15 times) | 0.135 (16 times) |

- The simpler fixed forest beat the searched one at every horizon. The search's own scores
  ranged only from 0.088 to 0.095, so it chose weakly.
- Calibration is close. In the highest tenth of predicted risk the searched forest predicted
  1.8% at 7 days and 1.6% was observed; at 14 days, 3.1% against 2.7%. Over all rows the mean
  prediction was 0.30% at 7 days against an outcome rate of 0.25%.
- Dating wild-bird features by collection date instead of detection date changes little
  (7 days: 0.143 against 0.143; 28 days: 0.131 against 0.128). Collection date looks ahead,
  so the detection-date version is the honest one.

**The county predictions themselves.** The model was rerun on 2026-11-20 to save one
prediction per county per Sunday (3,108 counties, 78 Sundays at 14 days; file
`data/journal/models/county_preds.parquet`, with the outcome beside each prediction). The
rerun reproduced every earlier score exactly. The figures below are for the searched forest
at 14 days; an "event" is a county-Sunday followed by a detection in that county within 14
days (1,112 of 242,424).

*Risk is concentrated in few county-weeks.*

| County-Sundays ranked by predicted risk | Share of all events they contain |
|---|---:|
| Highest 1% | 27% |
| Highest 5% | 47% |
| Highest 10% | 59% |
| Highest 25% | 79% |

At 7 days the same shares are 33%, 55%, 65% and 82%.

*A weekly watch list would be right about one time in five.* Taking the 10 counties with the
highest predicted risk each Sunday, 20% were followed by a detection within 14 days, and
those lists held 14% of all events. With 25 counties per week the hit rate is 12% and the
lists hold 21% of events. Against an outcome rate of 0.46%, that is a large gain, but most
outbreaks still fall outside any short list.

*The same counties stay at the top.* Counties with the highest mean predicted risk over the
test period:

| County | Mean predicted risk | Highest weekly risk | Event weeks of 78 |
|---|---:|---:|---:|
| LaGrange, IN | 0.157 | 0.403 | 34 |
| Elkhart, IN | 0.144 | 0.405 | 27 |
| Lancaster, PA | 0.118 | 0.419 | 25 |
| Queens, NY | 0.085 | 0.382 | 19 |
| Mercer, OH | 0.073 | 0.401 | 9 |
| Jay, IN | 0.073 | 0.394 | 12 |
| Weld, CO | 0.069 | 0.321 | 10 |
| Kings, NY | 0.069 | 0.384 | 15 |
| Darke, OH | 0.069 | 0.405 | 9 |
| Berks, PA | 0.054 | 0.150 | 0 |

- 367 of 3,108 counties had at least one event week. Of those, 190 are in the highest tenth
  of counties by mean predicted risk and 324 in the highest quarter.
- Across counties, predicted and observed event weeks correlate at 0.83. Of the 50 counties
  with the highest mean risk, 7 had no event.
- The model over-predicts in total: 1,317 event weeks expected against 1,112 observed. By
  state it under-predicts Indiana (80 expected, 120 observed) and South Dakota (39, 62) and
  over-predicts Ohio (58, 35) and Michigan (60, 33).

*Reading.* The county model mostly says "where it has been, it will be again": the list is
led by counties with repeated detections, and a county's own history is the strongest
feature. That is useful for ranking and weak as early warning, because a county's first
outbreak is the case it handles worst. These descriptions were computed once, after the
test period had been scored, and carry no intervals.

**County predictions combined to state level.** Summing county risk to one value per
state-week and comparing with the state baseline gives differences in PR-AUC that mostly
include zero, and whose sign depends on the forest setting (the full table is Multimedia
Appendix 5 of the manuscript). The state model is not worse than the combined county model,
which is the result that licenses the state-week unit.

**What was learned earlier, on the county model** (September 2026 notebooks; from project
notes, not recomputed here):

- *Feature selection.* The first baseline kept 23 features by importance and scored 57 times
  the outcome rate. The full 35 score about the same overall but do better on a county's
  first outbreak (difference in PR-AUC 0.0003 to 0.0024), which is the case early warning
  cares about. The journal analysis uses all 35.
- *Forest against boosting.* The shallow forest beat a tuned forest (by 0.014 PR-AUC) and a
  tuned gradient-boosting model (by 0.025 to 0.028). Settings picked on a late-2024
  validation window did not carry over to 2025. Boosting was not searched toward heavier
  regularisation, so this is not a verdict on boosting in general.
- *What drives the forecast.* The county's own recent outbreak history ranks first. That is
  real signal, and it is also why the baseline is hard to add to.

**Limits.**

- The saved prediction is the mean of five forests with different seeds; the five are not
  kept separately. Other files: `county_scores.csv`, `county_reliability.csv` and the
  state-level roll-up `county_rollup_preds.csv`, all in `data/journal/models/`.
- "Summing county risk" above is shorthand: the state value is one minus the product of
  (one minus each county's risk), which assumes counties are independent.
- No county-level test of posts was run, for the reason given above.

## Part 6. GDELT news results

**What this does.** When Facebook access looked unobtainable in September 2026, news coverage
from GDELT (a public index of world news) was built as a substitute signal. It was tested
three ways, all null. In the journal analysis it survives only as a control for news volume.

**The data.** Articles carrying one of GDELT's four avian-influenza themes, 2022-01-01 to
2026-07-21: 169,953 articles, 111,665 distinct stories, 1.23 million US place mentions.
GDELT holds no article text, only links and derived fields. It has a 17-day gap (2025-06-15
to 2025-07-01); windows touching the gap are treated as unknown. (Counts from project notes.)

**Test 1: does news volume improve the county baseline? No.** Change in PR-AUC from adding
GDELT features to the county forest, test period (September 2026; from project notes):

| Features added | Change in PR-AUC (95% CI) |
|---|---|
| State and national news volume | −0.012 (−0.018 to −0.005) |
| County news volume only | −0.002 (−0.005 to 0.002) |
| County news volume, a county's first outbreak only | 0.0007 (−0.0000 to 0.0015) |

- State and national volume made the forecast worse. The forest gave the news features about
  a quarter of its importance without predicting better.
- News is concurrent with outbreaks or follows them. The within-state weekly correlation
  peaks at the same week and the week after; for news leading by 1 to 3 weeks it is 0.11 to
  0.14.
- Washington, DC collects about six times the mentions of any county, from datelines.
  Removing such media hubs changed nothing.

**Test 2: does the tone of coverage lead outbreaks? No.** Five affect measures from GDELT's
GCAM dictionaries (anxiety, tentativeness, death, uncertainty, negativity) were registered
before the data was pulled (`docs/gcam_preregistration.md`). Each was tested at leads of 1 to
3 weeks, controlling for current outbreaks and news volume. All 15 intervals included zero.
The second stage of the registered plan was not run, as the plan required after a null
first stage.

**Test 3: does news volume explain the sighting result? No.** This is the one GDELT analysis
in the journal manuscript. Adding the state's 7-day story count and share to the state
baseline (14 days, test period, the 196 state-weeks with unknown GDELT left out of both):

| | Baseline | Baseline plus news |
|---|---|---|
| PR-AUC, all state-weeks | 0.549 | 0.550 |
| Facebook sightings, O/E ratio | 1.20 (1.05 to 1.35) | 1.18 (1.04 to 1.34) |
| X sightings, O/E ratio | 1.14 (0.97 to 1.31) | 1.13 (0.97 to 1.30) |

The excess after a sighting is not absorbed by news volume.

**A use that failed.** GDELT was also tried as a stand-in for the date of first public
report: was there already a news story naming the state before the sighting post? There was,
for 121 of the 136 Facebook sightings that were followed by an outbreak and 145 of 164 on X.
A state with outbreaks has avian-influenza news almost every week, so the test cannot tell
coverage of this flock from any other coverage. It is reported as uninformative.

**Reading.** Across volume, tone and timing, news coverage follows confirmed outbreaks. That
matches the earlier cohort's finding (peak correlation at a one-week lag) and is the reason
the sighting analysis had to test for a news echo at all.

## Part 7. Data history

**What this does.** The data in the manuscript is the fourth social-media plan the project
tried. This part records the sequence, because the changes shaped the design and because a
reader comparing the result with the proposal will otherwise not see why they differ. Dates
before November 2026 are from project notes.

| Date (2026) | Event | Consequence |
|---|---|---|
| Proposal | Facebook through the Meta Content Library was to be the primary source, with restricted location metadata. | |
| August | Collection and scoring notebooks written for Meta's secure research environment. | The environment allows no data out (aggregates included) and no files in. It has no county geography and no phrase search. Emoji were found to score zero under the lexicon method available there. |
| September 28 | Meta's environment abandoned. The alternative host (SOMAR) would have delivered a first reviewed output in mid-November at the earliest. | No analysis joining Facebook posts to outbreak data was possible inside the environment. |
| September 24-27 | GDELT news built and tested as a substitute. | Null (Part 6). |
| October 15 | X posts profiled: 532,537 unique posts stitched from two collectors. English 2022-06 to 2024-12 came from the earlier cohort's scraper (364,220 posts); English 2025 onward, Spanish and French came from twitterapi.io. | The English collector changed on exactly the date that separates development from test data. A drop in volume across that date could not be told apart from a change of method. |
| October 15 | A Facebook file arrived: an export from the Content Library's user interface, 485,652 rows, 301,044 unique posts, 2021-12 to 2026-07, searched with 29 English and Spanish terms. It has no location field. | Set aside at first. Loaded and geocoded from post text in late October. The team confirmed on November 11 that this export, unlike the secure environment, may be stored and analysed outside Meta. |
| October 19 | A pre-registered X analysis was withdrawn before it was run, because the data was still changing. | Replaced by a written specification approved before the test period was scored. |
| October 31 | X analysis run once on 2025-26 under that specification. | Both primary results failed: sentiment and volume added 0.008 PR-AUC (−0.006 to 0.021); sightings were followed by 0.85 times the expected outbreaks (0.67 to 1.09), where 2022-24 had shown 1.22. |
| November 11-12 | The same analysis applied to the Facebook export, with a revised sighting guide that excludes announcements of poultry detections. First paper draft. | Sightings on Facebook: 1.22. The two platforms now disagreed, and each had its own pipeline. |
| November 15-17 | Decision to write for a journal: Facebook primary and X secondary, one pipeline for both from raw posts, a committed protocol, human validation of labels, everything reported as exploratory. Protocol committed November 17 (`ef852d2`). | The test period had already been seen, so no result can be called confirmatory. |
| November 18 | English X posts for 2022-24 collected again with the same collector and queries as 2025 onward (287,777 unique X posts). | The collector change at the split date is gone. Posts whose wording was unchanged kept their labels; 1,065 others were read. |
| November 19 | Check for missing collection days: of 3,166 English query-days, 109 had returned nothing and 70 had been cut short. All were re-run. | 310,202 unique X posts; 7 query-days remain unfilled. 1,465 more posts read. No conclusion changed in either rerun (X sightings 1.13, then 1.15). |

**What the history means for reading the results.**

- *Nothing here is a confirmed prediction.* The 2025-26 data was examined under an earlier
  specification before the journal protocol was written. The manuscript says so.
- *The one pre-specified test failed.* The October 31 run on X is the only analysis in the
  project whose rules were fixed before its test data was scored, and both of its primary
  results were null. Later X results differ because the data, the sighting definition and the
  pipeline all changed; they are not a second attempt at the same test.
- *Facebook and X were collected differently.* Facebook is a keyword export of public posts,
  mostly from Pages; X is an API search. Differences between platforms may be differences in
  collection.
- *Neither source has reliable location metadata.* Place comes from the post text on both
  platforms, which is why the analysis is at state level (Part 5).
- *The X collection is nearly, not fully, complete.* Only English pulls were checked for
  missing days by query group.

## Part 8. Commitments ledger

**What this does.** The proposal (Milestone 3) made specific commitments. This ledger lists
each one and says whether it was done, changed or not done, and why. "Changed" means the
question was answered by a different method than the one proposed.

### Hypotheses and subproblems

| Commitment | Status | What happened |
|---|---|---|
| H1 and 1.2.2: sentiment differs between non-English and English posts | Done, with a change | Spanish posts score 0.11 to 0.14 more negative (Part 3). Posts were not translated; a multilingual model scored each language directly. Whether the gap is in the writers or in the model was not tested. |
| 1.2.1: integrate foreign-language posts into one model while preserving sentiment through translation | Changed | No translation. One multilingual model (XLM-T) for English and Spanish, so there is no translation step to lose sentiment in. French was dropped (Part 4). |
| H2 and 1.2.5: adding sentiment to the baseline improves prediction | Done; result null | Track A: no gain on either platform, in either language, with or without emoji. The baseline is the team's own model built on variables like Zou et al.'s, not a reproduction of theirs. |
| H2: sentiment increases lead time; "maximum lead time" (abstract) | Not done as stated | Fixed horizons of 7, 14 and 28 days were tested instead of estimating a lead time. With no gain at any horizon there is no lead time to report. |
| 1.2.3: what level of geolocation is reliable; narrow down the area of an outbreak quickly | Done in part | State is reliable (reader and geocoder agree for 99% of posts). County is available for 13% to 17% of posts. Zip code is not available. No model that narrows an outbreak area was built (Part 5). |
| H4 and 1.2.4: emoji and hashtags influence sentiment | Emoji done; hashtags not done | Removing emoji changes the label of 4% to 7% of posts and changes nothing at state-week level (Part 2). Hashtags were not analysed. |
| H3 and 1.2.6 (stretch): farmer sentiment follows policy announcements and events | Not done | Stretch goal. The posts turned out to be mostly relayed news, not farmers' own writing, which removes the premise. |

### Data

| Commitment | Status | What happened |
|---|---|---|
| Facebook from the Meta Content Library, with restricted metadata, as the primary source | Changed | The secure environment could not be used. A user-interface export of public posts is the primary source; it has no location metadata (Part 7). |
| Archived X snapshots | Changed | X posts were collected through a paid third-party API for 2022-06 to 2026-09, twice for the early English period (Part 7). |
| Google Trends, Reddit, GDELT as substitutes | GDELT done; others not used | GDELT was built and tested, null (Part 6). |
| Multilingual: English, Spanish, French; "all Western languages" | Changed | English and Spanish. French collected and dropped (Part 4). No other language was searched. |
| USDA poultry detections, county and daily | Done | Outcome for every model. |
| USDA egg production and chicken population, monthly, interpolated to daily | Changed | The 2022 Census of Agriculture's county poultry inventory is used as a fixed exposure measure. Withheld values are kept as unknown, not zero. |
| NOAA temperature and precipitation by county and day | Done | |
| USGS bird-band counts, smoothed, as a proxy for bird numbers | Not done | Planned and not built. Wild-bird HPAI detections and migration pressure derived from them are used instead. |
| Keyword list of 44 terms, including general poultry and economic terms | Changed | Narrowed to disease terms (for example "bird flu", "gripe aviar", "H5N1", "HPAI"). The general poultry and economic terms were not searched. |
| Images | Not done | The literature review proposed image embeddings. Neither post source used here was analysed for images. |

### Methods

| Commitment | Status | What happened |
|---|---|---|
| Multi-dimensional sentiment tagging (Pang et al.), falling back to a continuous score | Fallback used | Positive minus negative probability, −1 to 1. Separately, each post was labelled for content (what kind of report it is), which the proposal did not plan and which produced the one positive finding. |
| Sentiment models: RoBERTa, XLM-RoBERTa, LSTM | Changed | XLM-RoBERTa (the XLM-T variant) only. An earlier lexicon method was abandoned with the Meta environment. |
| Outbreak models: ARIMAX, SARIMAX, XGBoost, LSTM, GRU | Changed | Random forest, compared with gradient boosting and, in the first baseline, logistic regression (Part 5). Time-series and recurrent models were not fitted; a neural-network baseline was started and not finished. |
| Dependent variable: outbreak occurrence and intensity, county and daily | Changed | Occurrence only, at state-week level for the main analysis and county-day for the check. Intensity (birds affected) was not modelled. |
| Hold out the most recent 12 months | Changed | Development 2022-24, test 2025 to mid-2026. Longer, and fixed at a calendar boundary. |
| Metrics: RMSE, AUC-ROC, precision, recall, F1 | Changed | PR-AUC, calibration, and observed-to-expected ratios. With outcome rates under 1% at county level, AUC-ROC is uninformative and thresholded metrics depend on an arbitrary cut-off. |
| Permutation test: shuffle sentiment and look for a loss of performance | Changed | Replaced by the difference between nested models (with and without post features) with a week-block bootstrap. It answers the same question and respects the time structure, which shuffling does not. |
| Geolocation clustering (DBSCAN and alternatives) | Not done | Posts carry a state, not coordinates. |
| Controls: egg prices, policy events, other major events | Not done | |
| Controls: posting frequency and population | Done | The state's share of posts is a feature; population is in the baseline. |
| Economic impact through laying-hen losses and egg production | Not done | This is half of the project title. It depended on a working outbreak forecast with post data, and the time went to obtaining usable post data (Part 7). No part of it was started. |
| Report only aggregate, de-identified results | Done | No post is quoted and no account is named. |

### Commitments made after the proposal

| Commitment | Status | What happened |
|---|---|---|
| Score the 2025-26 X data once, under a specification approved first (October 31) | Done; both primaries null | Part 7. Later analyses of that period are exploratory and are labelled so. |
| GDELT tone: five registered measures, two stages (September 27) | Done; null | Stage 1 null; stage 2 not run, as registered (Part 6). |
| Journal protocol committed before any outcome-linked result (November 17) | Done | Commit `ef852d2`. Departures from it are listed in `PROGRESS.md`. |
| Human validation: three coders, 400 posts, blind | In progress | One of three workbooks is complete. |

### Summary

- **Done as proposed:** the central test (H2), the emoji question, the outcome and weather
  data, the reporting rule.
- **Done by a different method:** language comparison, multilingual scoring, model family,
  metrics, the control test, the test period.
- **Not done:** economic impact, the policy stretch goal, hashtags, images, bird-band
  counts, lead-time estimation, outbreak intensity.
- **Not in the proposal and done:** content labelling of every post by a language model with
  double reading; the sighting analysis (Track B); the news-echo checks; human validation.

The ledger was compiled from the proposal text and the project's records. The team should
check the "what happened" column against their own recollection before it is submitted.
