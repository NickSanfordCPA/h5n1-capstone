# Capstone supplement: language and emoji comparisons

*Written 2026-11-18. For the capstone report only; none of this is in the journal manuscript.
All X numbers are provisional until Max's re-pull is analysed.*

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
| Sentiment-set posts | 15,835 | 3,507 | 31,788 | 1,516 |
| Sighting posts | 1,503 | 167 | 2,960 | 64 |
| Sighted state-weeks, test period | 282 | 40 | 418 | 7 |

Every X state-week that has a sighting has an English one, so the English-only X result for
Track B is the pooled result.

**Track A is null in both languages.** Change in PR-AUC from adding post features, 14 days,
new-onset state-weeks (95% CI):

| Posts used | State-weeks (events) | Facebook | State-weeks (events) | X |
|---|---|---|---|---|
| Both languages (journal primary) | 490 (86) | −0.000 (−0.013 to 0.011) | 585 (96) | −0.002 (−0.010 to 0.006) |
| English only | 478 (86) | 0.002 (−0.010 to 0.015) | 578 (94) | −0.003 (−0.011 to 0.009) |
| Spanish only | 66 (11) | −0.006 (−0.023 to 0.011) | 22 (5) | 0.108 (−0.029 to 0.375) |

The Spanish-only X cell has 5 events and cannot be read. With post windows ending 5 days
early, every cell's interval again includes zero.

**Track B: the Facebook excess is an English-post result.** O/E ratio, sighted against other
state-weeks, 14 days (95% CI):

| Posts used | Facebook: followed / sighted (expected) | Facebook ratio | X: followed / sighted (expected) | X ratio |
|---|---|---|---|---|
| Both languages (journal primary) | 136 / 290 (112.9) | 1.21 (1.06 to 1.37) | 161 / 418 (142.0) | 1.13 (0.97 to 1.31) |
| English only | 133 / 282 (110.4) | 1.21 (1.06 to 1.38) | 161 / 418 (142.0) | 1.13 (0.97 to 1.31) |
| Spanish only | 12 / 40 (16.9) | 0.69 (0.43 to 0.96) | 4 / 7 (4.0) | 0.98 (0.33 to 1.68) |

- English-only sightings reproduce the pooled result on both platforms, so Spanish posts
  neither create nor mask the Track B excess.
- State-weeks with a Spanish-language Facebook sighting were followed by *fewer* outbreaks
  than expected (12 against 16.9). This rests on 40 state-weeks and one of several unplanned
  cells; with the first 5 days excluded it is 0.79 (0.37 to 1.21). It should be read as "no
  excess in Spanish posts", not as evidence of a protective signal. Why Spanish posts would
  differ was not investigated.
- With the first 5 days excluded, English-only ratios are 1.18 (0.96 to 1.42) on Facebook and
  1.27 (1.06 to 1.51) on X, the same as the pooled figures.

## Findings: emoji

**How common emoji are.** Of 52,646 sentiment-set posts, 7,386 (14%) contain an emoji. They
are far more common in Spanish posts.

| | Posts | With an emoji | Share |
|---|---:|---:|---:|
| Facebook, English | 15,835 | 1,552 | 10% |
| Facebook, Spanish | 3,507 | 1,634 | 47% |
| X, English | 31,788 | 3,608 | 11% |
| X, Spanish | 1,516 | 592 | 39% |

**Removing emoji barely moves the score.** Among posts with an emoji, the mean sentiment
score (positive minus negative probability, from −1 to 1) changed by 0.01 or less in every
platform and language cell; the mean absolute change per post was 0.04 to 0.05. The predicted
label changed for 4% to 7% of posts.

| | Mean score, as posted | Mean score, emoji removed | Mean absolute change | Label changed |
|---|---:|---:|---:|---:|
| Facebook, English | −0.424 | −0.422 | 0.042 | 5.9% |
| Facebook, Spanish | −0.593 | −0.595 | 0.037 | 5.3% |
| X, English | −0.492 | −0.484 | 0.050 | 6.5% |
| X, Spanish | −0.611 | −0.615 | 0.035 | 4.2% |

Label changes ran in both directions and nearly cancelled. On Facebook, 72 posts moved from
negative to neutral when emoji were removed and 86 moved from neutral to negative; on X, 126
and 100. Moves between negative and positive were rare (4 on Facebook, 17 on X). In these
posts, which are mostly relayed news, emoji carry little of the tone the model reads. (Which
emoji are used was not tabulated.)

**Track A does not change.** Change in PR-AUC, 14 days, new-onset state-weeks (95% CI):

| Sentiment scores | Facebook | X |
|---|---|---|
| As posted (journal primary) | −0.000 (−0.013 to 0.011) | −0.002 (−0.010 to 0.006) |
| Emoji removed | 0.000 (−0.013 to 0.011) | −0.002 (−0.010 to 0.006) |

The result with windows ending 5 days early is equally unchanged (Facebook −0.004 as posted,
−0.005 with emoji removed; X 0.002 and 0.001; every interval includes zero).

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
