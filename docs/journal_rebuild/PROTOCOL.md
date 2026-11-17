# Protocol: social media and H5N1 poultry outbreaks, journal rebuild

*For Nick's commit (gate 1), 2026-11-17, revised the same day after his review (section 14). Status: **not yet committed**. Once committed,
its hash is recorded in `PROGRESS.md`, and every later departure is logged there with its reason.*

This protocol fixes the data pipeline, the labelling guide, the features, the models and the
analyses **before any model in this rebuild is fitted or scored against outcomes**. It is an
audit trail, not a pre-registration. The 2025+ outcome data were already seen in earlier
exploratory work on both platforms (section 12), so every result of this rebuild is exploratory
and will be described that way.

Everything built so far touches no outcome: raw loads, deduplication, language detection,
geocoding, the labelling pilot and sentiment scoring. The baseline tables contain `target_`
columns, but no model has been fitted to them.

## 1. Question

Does social media add to a strong environmental and epidemiological baseline for predicting
USDA-confirmed H5N1 outbreaks in US poultry, and if so where? Two platforms are analysed with one
identical pipeline: Facebook (Meta, primary) and X (secondary).

- **Track A** asks whether the volume and tone of posts about a state improve a state-week
  forecast.
- **Track B** asks whether posts reporting sick or dead animals at a named local place
  ("sightings") are followed by more outbreaks than the baseline already expects.
- **Mechanism checks** ask whether any Track B signal is early warning, or news reporting the gap
  between a public announcement and the APHIS confirmation date.

## 2. Data sources

| Source | What it is | Local table or file |
|---|---|---|
| Meta | Muthu's Meta Content Library UI export: 29 CSV files, one per search term, public Facebook posts, 2021-12-01 to 2026-07-31. The two `bird-flu-en-*` files have their date ranges swapped in the filenames | `data/Meta Public/*.csv` |
| X, prior cohort | The previous cohort's scraper export, English, one file per term, originals and replies, 2022-06-01 to 2024-12-31 | `x_csv_raw` |
| X, twitterapi.io | Pulls by Muthu in English, Spanish and French, 2021-09 to 2026-09/10. It has **no English posts for 2022-06 to 2024-12** | `x_raw` |
| APHIS poultry | Confirmed detections in commercial and backyard flocks. The only date is `Confirmed Diagnosis`; there is no presumptive, sampling or report date | `fact_h5n1_outbreak` |
| APHIS wild birds | HPAI detections in wild birds, with `Collection Date` and `Date Detected` | `fact_wild_bird_sample` |
| USGS WHISPers | Wildlife morbidity and mortality events reported voluntarily by partner agencies. Not a census | `whispers_event`, `whispers_event_county` |
| NOAA, NASS, migration, banding | Weather, 2022 Census of Agriculture poultry exposure, migration geometry | as in the shared database |
| GDELT | News articles tagged with avian-influenza themes, with locations | `fact_gdelt_*` |

All source tables were copied read-only from the shared database into the local database
`h5n1_journal`, and every row count was reconciled (`data/journal/source_row_counts.csv`).

**The English X series changes collector on 2025-01-01.** Posts before that date come from the
prior cohort's scraper, and posts from that date come from twitterapi.io with different search
terms. Development data and test data for English X therefore come from different collectors.
Meta has one collector throughout.

## 3. Analysis window and splits

- **Window:** posts dated 2022-06-01 to 2026-07-13 (UTC dates).
  - The start is the first day of the prior-cohort X pull.
  - The end is the last APHIS poultry confirmation in the outcome file. Meta ends 2026-07-31 and X
    ends 2026-09-30, so the outcome file is the earliest end.
- **Grain:** state-week, week ending Sunday, 48 contiguous states plus DC (49 rows a week).
- **Development:** Sundays from 2022-08-07 through 2024-12-31 minus the horizon k.
- **Test:** Sundays from 2025-01-05 whose whole outcome window ends by 2026-07-13.
- **Horizons:** k = 7, 14 and 28 days. All are whole weeks, to match the state-week grain. The
  earlier work used 30 days for the longest horizon.
- **New onset:** a state-week with no confirmed outbreak in the state in the previous 28 days
  (four weeks). The earlier work used 30 days, which cuts through a week.

## 4. Outcomes and the baseline

### 4.1 Outcome

A USDA-confirmed HPAI detection in a poultry flock in the state in the k days after the forecast
Sunday, dated by `Confirmed Diagnosis`. A confirmation is dated later than the first public news
of a suspected flock, and the data carry no earlier date. The mechanism checks (section 9) exist
because of this.

### 4.2 Wild-bird features are re-dated

The earlier baseline dated each wild-bird detection by its Collection Date. A detection was not
knowable until its Date Detected, a median of 15 days later (IQR 11 to 31, 90th percentile 151).
The earlier baseline therefore looked ahead, in its own favour.

- **Primary:** every wild-bird feature is dated by **Date Detected**. Date Detected is never
  blank in the file.
- **Sensitivity:** the same features dated by Collection Date (Date Detected where it is blank),
  which is the earlier definition.
- **Implementation:** `fact_wild_bird_sample` holds both dates for each of the 19,426 samples with
  a known county. The 103 samples with county "Unknown" are dropped, as before. Two county-day
  tables are built from it, and the existing feature SQL runs unchanged on each.
- **Checks already done, on features only:**
  - the collection-dated rebuild equals the shared database's tables exactly (state-week: 11,662
    rows × 46 columns, no differences);
  - the detected-dated tables differ from them in the wild-bird columns only (six at state-week,
    five at county-day), with every target column identical;
  - the 7-day state wild-bird feature correlates 0.39 between the two datings.
- **Remaining limit:** Date Detected is a laboratory date. Public posting may be later still.

### 4.3 Baseline models

- **State baseline S3:** the 37 columns of `model_state_week` (place, season, exposure, the
  state's own recent outbreaks, wild-bird detections, neighbouring states, migration pressure,
  weather). **S0** is the first 10 (place, season, exposure).
- **County baseline C3:** Waree's 35-feature model on `model_county_day_35`, reported as the
  baseline and in the county roll-up comparison (section 8.4).
- **Learner:** random forest, with hyperparameters chosen by a small search **on the baseline
  only**, inside the development period:
  - **Grid:** 500 trees; maximum depth 4, 6, 8 or 12; minimum leaf 2, 10 or 50; features per split
    square-root or one third. That is 24 settings, plus Waree's (300 trees, depth 6, minimum leaf
    2) as a 25th candidate.
  - **Folds:** three forward-chaining folds, all inside 2022–2024. Train through 2023-09-30,
    2024-03-31 and 2024-09-30; validate on the following period, ending 2024-12-31. No 2025+
    outcome is used.
  - **Criterion:** mean PR-AUC across the three folds. State (S3): k = 14, new-onset state-weeks.
    County (C3): k = 7, all rows. Ties go to the shallower forest, then the larger leaf.
  - **One choice per grain.** The setting chosen for S3 is used for every state-week arm (S0 to
    S4s, and the reference model in Track B). The setting chosen for C3 is used for the county
    model. No arm is tuned separately, so a difference between arms is never a difference in
    settings, and the tuning budget is equal by construction.
  - **Reported:** the grid, the fold scores of every setting, and the chosen setting.
  - **Sensitivity:** every primary result is repeated with Waree's fixed settings. Earlier work
    found that settings tuned on one 2024 validation window did worse on 2025, so this check
    matters.
- **Seeds:** five. The reported score uses the mean prediction of the five forests. Seed variance
  is the standard deviation of the score across the five single-seed models.
- **Fills:** medians for weather and layer inventory come from the training rows only. Suppressed
  NASS values stay missing until that fill; they are never set to zero.
- **Reported for the baseline:** PR-AUC and lift at each k under both wild-bird datings, with the
  paired difference and its interval, so the effect of the re-dating is explicit.

## 5. The post funnel: one code path for both platforms

Each step removes a post for one reason. A post's disposition is the first step that removes it.
Steps 1 to 8 are deterministic rules (`h5n1/journal/posts.py`, `funnel.py`, `textgeo.py`). Steps 9
onward use reader labels (section 6). Counts are from the build of 2026-11-17.

| # | Disposition | Technique | Meta | X |
|---|---|---|---:|---:|
| | **Rows collected** | | **485,652** | **603,265** |
| 1 | Duplicate search result | Same post ID | 184,608 | 63,040 |
| | **Unique posts** | | **301,044** | **540,225** |
| 2 | Not English or Spanish | Language detector | 994 | 22,830 |
| 3 | Outside the analysis window | Post date | 30,429 | 26,691 |
| 4 | Quote or reshare | Post type | 4,973 | 9,030 |
| 5 | Not about avian influenza | Phrase rule | 27,985 | 28,423 |
| 6 | No US state named | Text geocoder | 199,891 | 405,078 |
| 7 | Names more than one US state | Text geocoder | 4,631 | 4,372 |
| 8 | Copypasta repeat | Text fingerprint | 6,209 | 3,143 |
| | **Reader set** | | **25,932** | **40,658** |

The reader set is 66,590 posts: 47,972 dated 2022 to 2024 and 18,618 dated 2025 or later.

**Step rules.**

1. **Duplicate.** Rows with the same post ID are one post. The kept row follows a fixed order:
   collector, then search term, then a hash of the text. The X total equals the shared
   `x_post_unique` table (540,225).
2. **Language.** One detector for both platforms (lingua, 17 candidate languages), run on text
   with links, handles, digits and symbols removed. Posts with fewer than 20 letters keep the
   platform's own tag. English and Spanish are kept. The detector agrees with the platform tags on
   about 99.5% of posts. French is removed here: earlier work found no French sightings.
3. **Window.** Section 3.
4. **Quote or reshare.** X quote posts and Meta reshares are removed. The prior-cohort X pull has
   no quote posts, so this keeps the two X collectors alike; reshares are the Meta analogue.
   Replies are kept on X, as before.
5. **Not about avian influenza.** The text must name the disease as a phrase or subtype: "bird
   flu", "avian influenza", "HPAI", "H5N1", "gripe aviar" and their variants (`RELEVANT_RE` in
   `funnel.py`). Words that merely co-occur do not qualify: the Meta export matched search words
   independently, so many "duck flu" or "farm flu" hits are off topic. On X, most posts removed
   here matched their search term only in a link or a reply thread.
6. **No US state named** and 7. **More than one state.** Section 5.1.
8. **Copypasta.** A post's fingerprint is the first 80 characters of its lower-cased text with
   links, handles, digits and punctuation removed. A fingerprint shared by at least 5 posts from
   at least 2 authors is copypasta, and only the earliest copy is kept. Groups form within
   platform and language.

**Three choices here go beyond the brief. Nick approved them on 2026-11-17**, with the instruction
to decide steps 4 and 8 on their merits and not because earlier funnels had them:

- **Step 4 is kept.** A quote post or a reshare is a comment on content that is not in its own
  text, so the place and the animals it refers to belong to something the reader and the geocoder
  cannot see. On X there is a second reason: one of the two English collectors returned no quote
  posts at all.
- **Step 8 is kept.** Mass-posted copies inflate the volume feature in Track A and would inflate
  the sighting counts in Track B's dose-response analysis. One copy carries the information.
- **Hub authors are no longer removed.** The earlier funnels dropped the top 1% of authors by
  volume because, with geography taken from the author, a hub posted "from" one state about every
  state. With geography taken from the text, a hub's post is located by what it says. The author
  type and content tags (section 6.3) now carry that distinction, and the mechanism checks test it
  directly. The cost is that a few accounts dominate X: the ten most active authors hold 30% of the
  X reader set. A sensitivity check excludes them (section 8.5).
- **All word rules run on NFKC-normalised text**, so styled Unicode capitals read as plain letters.

### 5.1 Geocoding from post text

Geography is the US state, and where supported the county, that the post **names**. This is the
only rule both platforms can share, because Meta gives no author location. It measures where a
post is about, not where its author lives. The rules are fixed and use the 2021 Census gazetteer
(legacy Connecticut counties). Full rules are in the docstring of `h5n1/journal/textgeo.py`.

- **Evidence, strongest first:** a county name with its state, or a county name found in only one
  state; "Place, ST" or "Place, State" found in the gazetteer; one of 60 large unambiguous cities;
  a state named in full.
- **State names** must be capitalised, in English or Spanish. Two-letter codes count only in
  "Place, ST" where the place exists in that state.
- **Handled ambiguities:**
  - Georgia: ignored when the post has a cue for the country or South Georgia island.
  - Washington: DC when written as DC; the state when written "Washington state" or similar;
    ignored when a federal cue is present; otherwise the state, flagged ambiguous (1,047 posts).
  - A state name followed by "County" is a county ("Delaware County, Pennsylvania").
  - Outlets, species, rivers and flyways carrying a state name are removed first ("Washington
    Post", "California condor", "Mississippi Flyway").
  - "Baja California", "La Florida", "South Georgia" and "New South Wales" are foreign.
- **Classes:** exactly one state is kept. Two or more are removed. A post with one state and a
  foreign place is kept and flagged (6,496 posts); the reader decides.
- **Result in the reader set:** state only 49,578; county 10,649; large city 4,948; city with
  state 1,415.
- **The geocoder selects candidates; the reader confirms the state.** Readers give each post's
  state independently, without seeing the geocoder's answer (section 6). The analysis state is
  the adjudicated reader state. This measures the geocoder on every post, and the human sample
  checks it again.
- **X profile location** is kept in separate columns as a sensitivity tier only. In the X reader
  set a profile state is known for 33% of posts, and it equals the text state for 49% of those.

## 6. Reader labels

### 6.1 Readers and adjudication

- **Guide:** `docs/journal_rebuild/GUIDE_reader.md`, frozen with this protocol. It merges the Meta
  guide v2, the X poultry-announcement guide and the X topic guide.
- **Readers:** in-session Sonnet subagents. No API. Every reader-set post is read **twice,
  independently**. A reader sees the post's id, date, language and the first 1,500 characters of
  its text. A reader never sees the author, the platform, the geocoded state or any outcome data.
- **Fields per post:** `relevant`; `topic` (7 labels); `content_class` (4 classes); `local_place`;
  `illness`; `exclude` (one reason, or blank); `place_state`; `place_name`.
- **Adjudication:** where the two reads differ on a field that feeds the analysis (`relevant`,
  `place_state`, `content_class`, or sighting-candidate status), an Opus subagent reads the post
  and both answers and decides. Topic differences are reported, not adjudicated; the first read's
  topic is kept.
- **Reported:** agreement and Cohen's κ between the two Sonnet reads for every field, and the
  share of posts sent to adjudication.
- **Corrections:** readers' files are never edited. Any override is logged with its reason in
  `corrections.csv`, and only for errors found by a stated audit rule.

### 6.2 The four content classes

| Class | Meaning |
|---|---|
| `domestic_detection` | News, an agency notice, or someone relaying one, about a confirmed or suspected detection in domestic animals: a poultry flock, a dairy herd, other livestock |
| `wildlife_report` | News, an agency notice, or someone relaying one, about sick or dead wild birds, or wild, zoo or pet mammals |
| `first_hand` | The author, or someone close, describing in their own words what they saw or experienced |
| `other` | Everything else |

Precedence when a post mixes classes: `first_hand` > `wildlife_report` > `domestic_detection` >
`other`. The brief's class (a), "report of a confirmed or presumptive animal detection", is read
here as detections in **domestic** animals, so that it is distinct from class (b) and identifies
the posts that echo the outcome. Dairy-herd detections sit in the same class; they are not the
outcome, but they are agency announcements of the same kind.

### 6.3 Author type and the news-like tag

- **Author type** is `organisation` or `individual`, one value per author.
  - **Meta:** from `post_owner.type`: Page → organisation, profile → individual. In the reader
    set, 24,378 posts are from Pages and 1,554 from profiles.
  - **X:** the metadata is thin. Only 189 of 11,649 authors carry a `verifiedType` (Business or
    Government → organisation). Every other author is labelled by Sonnet readers from the
    display name, handle and bio (a bio exists for 92% of authors), twice, with Opus
    adjudication. Readers see no posts and no outcomes.
- **News-like vs personal** is derived per post:
  - **news-like** when the author is an organisation, or the content class is
    `domestic_detection` or `wildlife_report`;
  - **personal** otherwise: an individual author whose post is `first_hand` or `other`.

  These are tags on one funnel, not separate funnels.

### 6.4 Funnel steps that use the labels

| # | Disposition | Rule |
|---|---|---|
| 9 | Not about avian influenza (reader) | `relevant` = no |
| 10 | Not about one US state (reader) | `place_state` is `US`, `FOREIGN` or `NONE` |
| | **Sentiment set (Track A)** | every post that passes step 10, at the reader's state |
| 11 | No sick or dead animals at a local place | `illness` = no or `local_place` = no |
| 12 | Announced domestic detection | class `domestic_detection`: the outcome itself, or its dairy analogue |
| 13 | Other content | class `other` |
| 14 | Outside the US / Retrospective / Not HPAI / Not credible | the `exclude` reason, one each |
| 15 | Outside the contiguous states | Alaska, Hawaii, territories |
| 16 | Same state-week | a later post in the same state and week as an earlier sighting, per platform |
| | **Sighting (Track B)** | one per state-week per platform; the number of sighting posts and of distinct named places in that state-week is kept with it |

- **Sighting definition (guide v2, unchanged in substance):** a post about sick or dead animals
  at a named place at county level or smaller, in a US state, within about 30 days, that is a
  wildlife report or a first-hand account. An announcement of a poultry-flock detection is never
  a sighting.
- **State moves:** when the adjudicated reader state differs from the geocoder's state, the post
  moves to the reader's state. Moves are counted and reported.
- The funnel document `docs/journal_rebuild/funnel.md` will give every step's count for Meta and
  X side by side.

### 6.5 Pilot

The guide was piloted on 600 reader-set posts dated 2022 to 2024 only: 75 from each cell of
platform × language × (county or city tier vs state tier). The sample over-represents Spanish and
local posts on purpose, so its rates are not population rates. Each post was read twice by
independent Sonnet readers (four runs of 300 posts).

| Field | Agreement | Cohen's κ |
|---|---:|---:|
| `relevant` | 0.992 | 0.96 |
| `topic` | 0.955 | 0.93 |
| `content_class` | 0.987 | 0.98 |
| `local_place` | 0.965 | 0.93 |
| `illness` | 0.952 | 0.90 |
| `exclude` | 0.985 | 0.94 |
| `place_state` | 0.965 | 0.96 |
| Sighting candidate (derived) | 0.998 | 0.99 |

- **Adjudication load:** 29 of 600 posts (4.8%) differed on a field that feeds the analysis.
- **Sighting candidates:** 34 by one read and 35 by the other, 34 in common.
- **Content classes** (one read): other 384, domestic detection 106, wildlife report 105,
  first-hand 5. First-hand posts are rare, so the first-hand cells of the mechanism checks will be
  small.
- **Geocoder:** where a reader named a US state, it matched the geocoder's state in 99.7% to 100%
  of posts. But readers named a single US state for only about 65% of geocoded posts in this
  sample (Meta 58%, X 72%); the rest were foreign (82), about no place (85) or about the US
  generally (42). The geocoder is therefore precise about *which* state and loose about
  *whether* a post is about a state. Step 10 exists for that reason.
- **Changes after the pilot:** eight clarifications, marked *(pilot)* in the guide, each answering
  a question the readers raised: news roundups; human cases that mention a flock; detections with
  no animal stated; products and wastewater; an organisation speaking for itself; `local_place`
  for non-animal reports and for places abroad; `illness` at state level; when to use
  `retrospective`. **The revised guide was not re-piloted.** The clarifications narrow cases the
  readers flagged as unclear; none changes a definition.

No 2025+ post was read while writing or revising the guide.

### 6.6 Labelling volume

**Option A, decided by Nick on 2026-11-17: two reads of all 66,590 posts.**

- Measured in the pilot, a reader run of 300 posts uses about 155,000 tokens and takes about 3
  minutes. Two reads of every post is 444 runs, about 69 million tokens. Add Opus adjudication of
  roughly 5% of posts (about 3,200), and about 50 runs to label 11,460 X authors twice.
- **Pacing.** Labelling must stay inside Nick's 5-hour usage limit, which the session cannot
  read. So it runs in groups: a first group of 20 runs, then Nick reports his usage meter, and the
  group size is set so that labelling takes at most 60% of a window. Each group is resumable, and
  `PROGRESS.md` records which batches are done.
- Batches are fixed before labelling starts, in a random order, so that a pause never leaves one
  platform or period unread.

## 7. Sentiment

- **Model:** `cardiffnlp/twitter-xlm-roberta-base-sentiment`, fp32, 512 tokens, newline → space,
  `@user` and `http` masking. Score = p(positive) − p(negative). Identical for both platforms and
  both languages. Script: `h5n1/journal/xlmr_score.py`.
- **Scope:** every reader-set post is scored; the sentiment set is a subset. Done 2026-11-17 on
  an RTX 4070 (cuda2R): 66,590 posts, none missing, in `journal.post_sentiment`.
- **Check:** 300 randomly chosen posts were rescored on a CPU on another machine. The largest
  difference in any class probability was 1.7e-6; 15 of the 300 exceeded 1e-6 and none exceeded
  1e-5. This is floating-point difference between GPU and CPU, slightly outside the 1e-6 target.

## 8. Track A, Track B and the baseline comparisons

Both tracks run on **both platforms** with the same code. Designs follow
`docs/x_utility_spec.md` except where stated.

### 8.1 Features (Track A)

Built per platform from the sentiment set, at state-week, with every window ending on the
forecast Sunday:

- `share_7d`: the state's share of the platform's posts in the last 7 days;
- `sent_7d`: mean sentiment over the last 7 days, shrunk toward the state's own past mean;
- `sent_chg`: `sent_7d` minus the same mean over the 28 days before the window.

The shrinkage pseudocount is estimated per platform from posts through 2024, without outcomes.
The three features are the set frozen in the X specification; the four candidates dropped there
(redundant, or broken by the X collector switch) stay dropped on both platforms.

**Rows:** covered state-weeks only (at least one post in the 7-day window), the same rows in every
arm. Nothing is imputed.

### 8.2 Track A

- **Arms:** S0; S1 (posts only); S2 = S0 + posts; S3; S4 = S3 + posts; S4v = S3 + share; S4s =
  S3 + sentiment.
- **Primary outcome:** S4 − S3 PR-AUC at k = 14 on new-onset state-weeks, with a paired
  week-block bootstrap, 2,000 replicates, 95% interval. One per platform.
- **Reading rule, fixed now:** posts add to the baseline only if the interval excludes 0 on the
  positive side **and** the estimate stays positive when the post windows end 5 days before the
  forecast Sunday.
- **Secondary, labelled as such, no multiplicity correction:** the other horizons and all rows;
  S2 vs S0; S4v and S4s; the offset logistic with standard errors clustered on state and week;
  block permutation within state × season; residualised features; the 3-day embargo.

### 8.3 Track B

- **Rows:** one per sighting at its state-week.
- **Reference:** S3 fitted on all development state-weeks and Platt-calibrated on
  leave-one-season-out scores. It supplies the expected risk only. No model is trained on
  sightings.
- **Primary outcome:** the O/E ratio at k = 14, all sightings: (outbreaks after sightings ÷ summed
  expected risk) divided by the same quantity for non-sighting state-weeks in the same period.
  Week-block bootstrap, 2,000 replicates, 95% interval. One per platform.
- **Reading rule, fixed now:** sightings carry signal beyond the baseline only if the lower bound
  exceeds 1 **and** the estimate stays above 1 with the first 5 days after the post excluded from
  the outcome window.
- **Secondary:** k = 7 and 28; new onsets (descriptive only when the cell is small); trigger rate;
  share of outbreaks flagged; the season-matched permutation rate; 2025-Q1 and later weeks
  separately.
- **Dose-response (secondary).** The primary treats a state-week as sighted or not, because the
  outcome is one value per state-week: counting five posts as five sightings would count the same
  outbreak and the same expected risk five times. Whether *more* sightings mean more risk is
  tested separately:
  - the O/E ratio for state-weeks with 1, 2–3, and 4 or more sighting posts;
  - the same for 1, 2, and 3 or more **distinct named places**, which separates several die-offs
    from one story repeated by many accounts;
  - a trend: the coefficient on log(1 + count) in a logistic model of the outcome on sighted
    state-weeks, with logit of the calibrated S3 risk as an offset, standard errors clustered on
    state and week.

### 8.4 County roll-up

`county_rollup_2025.py` is rerun on the re-dated baseline: C3 is rolled up to state-week as
1 − ∏(1 − p) and compared with S3 and S4. It is read as in the `paper-draft-decisions` memory: S3
answers "which state", C3 answers "which county", and the comparison does not by itself decide
whether social media is used.

### 8.5 What is reported for every model

- PR-AUC and lift over the base rate;
- Brier score and a reliability curve (ten equal-count bins);
- event counts per split, for all state-weeks and for new onsets;
- seed variance across the five seeds;
- the interval's upper bound, read as the largest effect the data allow.

### 8.6 Sensitivity checks

| Check | What changes |
|---|---|
| X collector switch | X results for 2025-Q1 and for later weeks separately; a statement that development and test come from different collectors |
| First 5 days excluded | Section 8.2 and 8.3 reading rules; also 3 days |
| Poultry words | Posts and sightings whose text contains a poultry word are dropped |
| X profile-location tier | X posts placed by profile state where one exists, in place of the text state |
| Collection-date wild birds | Baseline and both tracks rerun on the collection-dated tables |
| At least 5 posts | Track A on state-weeks with 5 or more posts (descriptive where coverage is thin) |
| S3 + WHISPers | Section 10 |
| Most active authors | The top 1% of each platform's authors by 2022–2024 volume are dropped |
| Waree's fixed forest | 300 trees, depth 6, minimum leaf 2 for every model, in place of the searched setting |
| 30-day definitions | New onset and the longest horizon at 30 days, as in the earlier work |
| Language | English and Spanish separately (capstone supplement only) |

## 9. News-echo mechanism checks

**Question:** is a Track B signal on Meta news reporting the interval between a public
announcement and the APHIS confirmation, and not early warning?

1. **Tags × platform.**
   - The Track B O/E ratio at k = 14 for each content class (`wildlife_report`, `first_hand`) and
     for each tag (news-like, personal), on each platform. Every cell is reported with its
     interval and its count. A small cell is inconclusive, not null.
   - **The difference, with its own interval.** One pooled model over both platforms' test
     state-weeks: logistic regression of the outcome on sighting indicators by tag, platform, and
     their interaction, with logit of the calibrated S3 risk as an offset. The reported quantity
     is the interaction: the ratio of the news-like O/E ratio on Meta to that on X (and the same
     for personal). Its interval comes from a week-block bootstrap that resamples the same weeks
     for both platforms, 2,000 replicates. Two separate intervals are not compared.
2. **Timing.**
   - The distribution of days from each sighting to the next APHIS confirmation in its state,
     per platform and tag.
   - The O/E ratio recomputed with the outcome window starting N days after the post, for N = 3,
     5, 7 and 10. This is the estimand. Dropping sightings *because* a confirmation followed
     within N days would select on the outcome, so that version is shown only as description.
   - **First-public-report proxy.** For each state and confirmation date, the earliest day in the
     14 days before confirmation with a GDELT avian-influenza story located in that state. Each
     sighting followed by an outbreak is classed as before or after that proxy, and the O/E ratio
     is recomputed for sightings posted before it. GDELT has a known gap (2025-06-15 to
     2025-07-01) and ends 2026-07-21.
   - The same classification against WHISPers `known_date_lb` for HPAI-diagnosed events in the
     state, where one exists.
3. **GDELT news-volume control.** The state's 7-day GDELT avian-influenza story count and share
   are added to S3 (S3 + news), the expected risk is recomputed, and the Track B O/E ratio is
   reported against it. If the Meta effect disappears, the paper says so plainly.

The paper is written for either outcome. If the signal sits in news-like posts and vanishes under
the exclusion windows or the news control, the conclusion is that social media's apparent lead is
the reporting-to-confirmation lag. If it survives in first-hand posts after the exclusion windows,
it is a credible signal. No framing is chosen before the results.

## 10. WHISPers

- **Source:** the public REST API `https://whispers.usgs.gov/api/eventsummaries/` (the records
  behind the web app's export; data release doi:10.5066/P9B0A3IM; US Government work, public
  domain). Retrieved 2026-11-17: 12,481 events, of which 3,137 are US events starting in 2022 or
  later, 2,485 involve birds and 828 carry an HPAI diagnosis. Counties are FIPS-coded.
- **No public date exists.** The API gives each event's start and end date, but not the date the
  record was created or published. A start date alone is look-ahead, like the Collection Date.
- **What is done instead.** Event ids are assigned in creation order, and a record cannot predate
  the event it describes. So a record existed no earlier than the latest start date among all
  records with a lower or equal id. That running maximum, `known_date_lb`, dates each event. It is
  a **lower bound**: for US events since 2022 it is a median of 8 days after the start date (75th
  percentile 27, 90th percentile 70). Some look-ahead can remain, in the baseline's favour.
- **A diagnosis is added later, at an unknown date.** HPAI-diagnosed events are therefore **not**
  used as a dated predictor.
- **Use:**
  - **S3 + WHISPers (sensitivity):** counts of avian morbidity or mortality events of any cause in
    the state in the last 7, 21 and 60 days, and in neighbouring states, dated by `known_date_lb`.
  - **Reference date in the timing test** (section 9), for HPAI-diagnosed events.
- The data were retrieved four months after the analysis window ends, so records may have been
  revised since. The paper states this, and that reporting is voluntary.

## 11. Human validation

- **Sample:** 400 posts from the labelled reader set, drawn after labelling, stratified by
  platform (Meta, X) × language (en, es) × stratum:
  - adjudicated sightings (oversampled);
  - each content class;
  - posts on which the two readers disagreed;
  - posts the readers judged not relevant or not about one US state.

  Allocation is fixed before drawing and written to `coding/sample_design.csv` with each post's
  sampling weight, so agreement can be reweighted to the reader set.
- **Coders:** Nick, Max and Waree each code **every** post, blind to the reader labels, to each
  other and to outcomes. The post date is shown, because the retrospective rule needs it. The
  geocoded state is shown for one question only.
- **Fields:** relevant; sick or dead animals at a local place (the sighting question); content
  class; author type (name and handle or Page type shown); whether the geocoded state is correct.
- **Packets:** one `.xlsx` per coder with drop-down validation, plus a one-page guide: the
  readers' guide, rewritten for people.
- **Analysis:**
  - between coders: Krippendorff's α across the three, and pairwise Cohen's κ, per field;
  - human consensus (majority of three) against the LLM labels: κ, and sensitivity and positive
    predictive value for sightings, weighted to the reader set, with bootstrap intervals;
  - geocoder: share of posts whose state the majority judged correct.
- The results use the LLM labels. The human sample validates them; it does not replace them.

## 12. What was already known from earlier exploration

None of this is evidence for the rebuild's claims, and none of it is used as a prior. It is
recorded so the results cannot be read as if the choices were blind.

- **The 2025+ outcomes have been seen on both platforms.**
  - X, scored once on 2026-10-31 under a frozen specification: Track A S4 − S3 at k = 14 on new
    onsets was +0.008 (−0.006 to +0.021); Track B O/E ratio at k = 14 was 0.85 (0.67 to 1.09),
    and 0.83 (0.63 to 1.07) after rescoring under the poultry-announcement rule.
  - Meta, scored on 2026-11-11 without a frozen gate, on Max's geocoded posts: Track A was −0.021
    (−0.032 to −0.004); Track B O/E ratio was 1.22 (1.07 to 1.39) on 275 sightings, holding with
    the first 5 days excluded (1.25).
- **So the expectation going in** is: Track A null on both platforms; a Track B signal on Meta
  and none on X. The mechanism checks in section 9 were designed after seeing that pattern, to
  explain it.
- **In 2022–2024 exploration** the X Track B ratio was 1.22 (1.01 to 1.43) and did not hold in
  2025+; the Meta ratio was 1.05 (0.87 to 1.24).
- **The baseline's look-ahead was known** and left in place in the earlier work.
- **What differs in this rebuild**, each of which can move the numbers:
  - Meta is rebuilt from the raw export with a new geocoder; Max's tables are not used;
  - X geography moves from the author's profile to the post text, which changes which X posts
    enter at all;
  - hub authors are no longer removed;
  - wild-bird features are re-dated, which weakens the baseline;
  - one guide and two reads replace two guides and one read;
  - forest settings come from a search inside 2022–2024, not from Waree's fixed settings;
  - new onset and the longest horizon are 28 days, not 30.
- **The guide and the geocoder were refined on 2022–2024 posts only.** Geocoder and relevance
  rules were tuned by reading samples of pre-2025 posts, without outcomes.
- GDELT news volume and tone were tested earlier as predictors and found null or harmful.

## 13. Not in scope

- No county-level social-media arm, no imputation of uncovered state-weeks, no PCA.
- No tuning of any augmented model separately from the baseline, and no learner other than the
  random forest.
- The language and emoji comparisons are for the capstone supplement, not the journal paper.
- **Prospective holdout:** if Muthu's 2026-08-01+ Meta pull arrives, it stays unopened until this
  protocol is committed, and is then run once through this pipeline unchanged. It needs a newer
  APHIS file. The paper is written so that it is an optional subsection.

## 14. Decisions and who made them

**Decided by Nick on 2026-11-17, after reading the first draft:**

1. New onset is 28 days (section 3). The longest horizon moves to 28 days with it.
2. The forest's settings may change; a small search on the baseline is done, with the same
   setting for every arm and Waree's settings as a sensitivity check (section 4.3).
3. Steps 4 and 8 are judged on their merits, as in a new analysis; both are kept (section 5).
4. Hub authors are not removed, given the sensitivity check (sections 5 and 8.6).
5. Word rules run on NFKC-normalised text (section 5).
6. Track B keeps one sighting per state-week as the primary, and adds a dose-response analysis
   (section 8.3).
7. Labelling is option A: two reads of all 66,590 posts, paced inside the 5-hour usage limit
   (section 6.6).

**Defaults chosen by Claude and not objected to.** Committing the protocol accepts them:

8. Wild-bird tables: both dates in a sample table, two county-day tables (section 4.2).
9. Window 2022-06-01 to 2026-07-13 (section 3).
10. The reader gives the state without seeing the geocoder's; the reader's state is used (5.1).
11. Class (a) is read as *domestic* animal detections, including dairy herds (section 6.2).
12. The news-like tag's definition (section 6.3).
13. Adjudication only on fields that feed the analysis (section 6.1).
14. WHISPers dated by the lower bound; HPAI diagnosis not used as a predictor (section 10).
15. The timing test's estimand is the shifted outcome window (section 9).
16. The first-public-report proxy's definition (section 9).
17. The search grid, folds and criterion (section 4.3).
