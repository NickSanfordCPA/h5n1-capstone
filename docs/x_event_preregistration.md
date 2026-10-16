# X social-media features vs the production baseline: pre-registration

**Drafted on 2026-09-29, before Muthu's X repull was received or looked at.** Everything in
this file is fixed ahead of those data: the pipeline, the two X features, the model, the six
comparisons, the claim rule. Anything decided later is labelled as a deviation in the results.

This supersedes an uncommitted draft from 2026-09-28. That draft pre-registered state-day
logistic regressions. It was replaced once the project asked the question the way it asked
GDELT's: does X add lift to **Waree's production forest**, not to a weaker stand-in?

> **Amended 2026-10-16 — read [Amendment 1](#amendment-1-2026-10-16) first.** The repull
> turned out not to be a full-period pull, so the primary test changes. The amendment
> overrides anything below that it contradicts; everything it does not mention stands.

## What has already been seen, and so cannot confirm itself

Every analysis below was run on the prior cohort's X pull (2022-06-01..2024-12-31), before
this document existed. They chose the design:
- **Event study.** Screened firsthand illness reports led new state-level poultry outbreaks by
  about 1.45× vs a same-state, season-matched baseline (54 events; 30-day p = .023).
- **Regressions.** A state × month fixed-effects logit (poultry new-onset, 30 days) found post
  OR 3.63, sentiment OR 1.22 per SD, media share OR 3.39.
- **Weak-baseline forest.** A state-day forest that knew only state and season found an X lift
  of +0.023 PR-AUC [−0.000, +0.047], test year 2024.
- **Production-baseline forest** (this design). Train 2022-07..2024, test 2024. **Null at
  every horizon**, and significantly *worse* at 30 days on all positives: −0.0009 [−0.0019,
  −0.0001]. The firsthand feature ranked 25th of 25.

The honest prior is therefore **no lift**. The confirmatory run is a held-out check of that
null on the production split, as much as a search for a positive.

## Amendment 1 (2026-10-16)

**Drafted before any 2025–26 X feature was built, joined to outcomes, or scored.** What had
been done with 2025–26 posts at drafting: Waree's `x_post_sentiment` table scored the text of
every `x_post_unique` post with XLM-R (no outcomes involved, and not the registered sentiment),
and counts of posts by corpus, query, language, type and quarter were profiled. Nothing else. As
far as the drafter knows, no one has put 2025–26 X data next to outbreak data; reviewers confirm
or correct this in the PR.

### What the repull turned out to be

The posts are in Cloud SQL as `x_post_unique` (532,537 posts). Its `corpus` column shows a
**stitched** corpus, not the full-period repull this document assumed:

| corpus | posts | dates | queries |
|---|---|---|---|
| `prev_cohort_en` | 364,220 | 2022-06-01 .. 2024-12-31 | the prior cohort's per-term search (the `data/X` CSVs) |
| `twitterapi_en` | 78,591 | 2021-09-01 .. 2022-05-31 and 2025-01-01 .. 2026-09-30 | `phrases`, `subtypes` |
| `twitterapi_es` | 76,631 | 2021-09 .. 2026-09 | `phrases`, `subtypes_and_rest` |
| `twitterapi_fr` | 13,095 | 2022-04 .. 2024-12 | as above |

`twitterapi_en` has **no posts between 2022-06-01 and 2024-12-31**. So English collection
changes process on 2025-01-01, the exact train/test boundary: the forest would be trained on
features from one scraper and query set and scored on features from another. English volume
goes from 72,614 posts in 2024 Q4 to 28,116 in 2025 Q1 and 3,400–9,600 a quarter afterwards.
Some of that is the news cycle and some is collection, and the data cannot separate the two.
The query strings behind `phrases` and `subtypes` are not recorded in the database.

`x_firsthand_30d` is a count and `x_sent_7d` depends on who is posting, so a shift in either
at the seam is confounded with signal. A forest lift, or a harm, at 2025-01-01 could be the
change of scraper. The registered forest test therefore cannot support a claim either way.

### What changes

**1. Data.**
- Input: `x_post_unique` with `lang = 'en'`, `corpus IN ('prev_cohort_en', 'twitterapi_en')`,
  `post_type IN ('original', 'reply')`. Quote posts are dropped, because the prior cohort has
  none.
- **Training period: the exploratory outputs are reused as they are,** not rebuilt. There is
  no new-query pull for 2022–24 to rebuild from, and rebuilding the same posts with the same
  frozen code would reproduce them. The frozen files are listed below. The "Every step … is
  re-run on the whole repull" rule under Data is void.
- `twitterapi_en` posts before 2022-06-01 are not used. They would make the training period
  mixed-source too.
- **Test period:** pipeline steps 1–7 are applied to `twitterapi_en` posts from 2025-01-01 on.
  Bios come from `raw->'author'->'profile_bio'->>'description'` (94% filled) and location
  from `user_location`. Outputs go to `data/X_derived/test_2025/` and never overwrite the
  frozen training files.
- **Hubs in the test period:** an author is a hub if they were a hub in training, OR if they
  are in the top 1% of 2025+ `twitterapi_en` authors by post count and are not
  farmer_keeper or ag_institution. The training rule cannot find hubs that only exist in the
  new corpus; this rule uses post counts only, never outcomes. Copypasta is flagged within the
  test period.
- **Account labels:** existing labels are kept. New test-period authors that need an LLM label
  (step 2) are labelled by subagents using Appendix A.
- **Sentiment changes model** (replaces step 5 and the TweetEval row of Frozen inputs):
  - Model: `cardiffnlp/twitter-xlm-roberta-base-sentiment`, fp32, the model Waree's
    `x_post_sentiment` uses. One multilingual model scores English and Spanish on the same
    scale, and the team uses one sentiment definition throughout.
  - Preprocessing: line breaks → a space, then the Cardiff convention (user handles →
    `@user`, links → `http`), as `h5n1/sentiment/core.preprocess`.
  - Truncation at 512 tokens.
  - Scored on the filtered subset (step 4), in **both** periods. The training subset is
    re-scored, so `sentiment_subset.parquet` (TweetEval) is no longer a frozen input.
  - Score = p_pos − p_neg, as before. `x_sent_7d` is unchanged otherwise.
  - The `x_post_sentiment` values themselves are not used, because their preprocessing
    differs. The run records the model file's SHA-256 and Hugging Face revision; the same
    file scores both periods.

**2. Primary test: an out-of-sample replication of the event study.** It replaces the
forest's six comparisons as the claim-bearing test. It compares outbreak *rates* after a
firsthand report with the same state's season-matched rate, and uses no X volume level. A
change in collection changes how many events are found; it biases the lift only if the new
queries reach systematically different reporters, and that limit is stated with the result.
- **Events:** test-period `illness_seen` posts (step 6, threshold 0.55, every flagged post
  reviewed). Appendix C exclusions are made by a subagent that sees post text, author state and
  date only. Then one event per state per ISO week, the earliest post (step 7).
- **Window:** 2025-01-01 .. (the last `fact_h5n1_outbreak` date at run time − 30 days).
- **Target:** poultry (`fact_h5n1_outbreak`), **new-onset** (no detection in the state in the
  prior 30 days), **k = 30**: any detection in the state in (d, d+30].
- **Statistic:** lift = event rate ÷ B3, where B3 is the same-state, ±15 days-of-year
  permutation baseline drawn from onset-eligible days in the test window; 2,000 draws, seed
  20260928. B1, B2 and a 95% bootstrap CI on the event rate are reported alongside.
- **Hypothesis and claim rule:** one test, one-sided, H1 lift > 1, which is the exploratory
  direction (1.45×). A lead is claimed if the permutation p ≤ 0.05. Otherwise the result is
  no detectable lead. Either way the lift and n are reported.
- **Minimum size:** fewer than 10 new-onset events means no confirmatory test (the code
  returns nothing below 10). That is reported as such. The exploration found 54 in 31 months;
  scaled to 21 months and a corpus about a quarter as dense, that suggests **about 10**, so
  this rule may well trigger. **Even above it the test is underpowered.** A null is weak evidence against a lead, and the direction and size of the
  lift are reported as the main descriptive result.
- **Code:** `h5n1/models/x_event_study.py` (`events` step) as committed with this amendment,
  run as

      DB_PORT=5433 uv run python -m h5n1.models.x_event_study events --start 2025-01-01 --end <last poultry date − 30>

  pointed at the test-period event file. The only code change allowed is that input path.

**3. The registered forest test is still run once, as specified,** with one change: training
starts **2022-07-01**, not 2022-02-08, so that no training row predates the posts behind its
features (as in the exploratory `x_lift` run). All six rows are reported, labelled
**confounded by the 2025-01-01 collection change**. They are not used for a lift or a harm
claim.

**4. New secondary analyses** (reported, never used for claims):
- The forest test with both X features z-scored **within each period**: training rows by the
  training period's mean and SD, test rows by the test period's. This uses X values only,
  never outcomes. It removes level shifts but not changes in who posts.
- The event study at k = 7 and 14, on all events, and on the wild-bird target, in the test
  window.
- Event counts and screen yield (flagged, `local_obs`, `illness_seen`, excluded by reason),
  for each period.

**5. Spanish stream: a secondary nested arm, never used for claims.**
- **Why secondary.** Only about 2% of `twitterapi_es` originals and replies geocode to a US
  state: 1,472 posts from 478 authors, 2021-09..2026-09, 293 of them from 2025 on. Some are
  misplaced (for example "Bogotá, D.C., Colombia" → DC, "Tijuana, Baja California" → CA).
  The Spanish features will equal their fill value on nearly every state-day, so **a null is
  expected by construction** and says nothing about whether Spanish-language posts matter.
  Spanish is still worth reporting: it is the one stream collected by a single process over
  the whole period, so it has no 2025-01-01 seam. The counts are reported as a finding about
  the data.
- **Input:** `twitterapi_es`, `post_type IN ('original', 'reply')`, 2022-06-01 .. 2026-09-30.
- **Geography:** `x_location` as for English, then a Spanish-only filter. It drops any post
  whose `user_location` names one of the other Spanish-speaking countries (in Spanish or
  English), or Baja California. The English geocoder is not changed.
- **Accounts, hubs, copypasta:** as steps 1–3, computed within the Spanish stream. A hub is
  in the top 1% of Spanish authors by pre-2025 post count and is not farmer_keeper or
  ag_institution. The seam rule in item 1 does not apply, because there is no seam.
- **Sentiment:** the XLM-R spec in item 1.
- **Firsthand reports:** the MiniLM screen and the gold set are English-only, so there is no
  screen. **Every** US-geocoded Spanish subset post, in both periods, is reviewed by subagents
  with Appendix B, then Appendix C. Then one event per state per ISO week.
- **Features:** `x_firsthand_30d_es` and `x_sent_7d_es`, defined exactly as the English two.
  They are separate columns, not pooled, so a change in the language mix cannot move the
  English means.
- **Arms and comparisons:**
  - C = B + `x_firsthand_30d_es` + `x_sent_7d_es`.
  - C − B on the six (k, slice) cells is Spanish's increment over English.
  - A + the two Spanish features − A is Spanish alone, with no seam in either arm.
  - Both use the registered bootstrap and are reported with 95% CIs. No multiplicity
    correction is applied, because no claim is made from them.
- **Event study:** the Spanish event count is reported for each period. If there are at least
  10 test-period new-onset events (not expected), the item 2 event study is also run on
  Spanish events alone and on English and Spanish pooled, as a secondary result.

**6. Compute.** Every model inference step (XLM-R sentiment and MiniLM embeddings) runs in
fp32 on `gpu1` (CUDA), for both periods and both languages, so no score depends on which
machine produced it. The run records the device, the CUDA and torch versions, and the model
hashes.

**Frozen derived inputs** (training period, SHA-256 at amendment):

| file under `data/X_derived/` | hash |
|---|---|
| `labels/illness_posts_screened.parquet` | `772f3cba…b9b0c9` |
| `topic_subset.parquet` | `29531f40…85e27e` |
| `authors.parquet` | `b72bc951…01dc68` |
| `local_observation.parquet` | `42089ab9…acd51c` |

### Forking paths, stated

The new primary is the design that looked best in exploration (the 30-day new-onset lift of
1.45×, p = .023). The forest was null, and it is the one that was demoted. The reason for
switching is the collection seam, which was found from post counts and not from any result.
But the choice of replacement was made knowing the exploratory results, and readers should
weigh it that way. The exploratory p = .023 does not survive the 12 tests run at the time, so
the replication is the first real test of that lead.

The sentiment model also changes after exploration, from TweetEval to XLM-R. All exploratory
sentiment results used TweetEval, so they say nothing about how XLM-R will behave; the switch
was made for consistency across languages and the team, not from any XLM-R result.

## Question

Adding two X state features to Waree's production random forest on `model_county_day`'s
23 predictors, does test PR-AUC improve for county-day poultry outbreaks within 7, 14 or 30
days? This is asked on all positives and on the new-onset slice.

X enters as social media, not news. News was tested separately with GDELT and was null.

## Data

**X posts: Muthu's repull, all dates.**
- **Every step of the pipeline below is re-run on the whole repull.** New search terms (e.g.
  `bird flu`) change the corpus in 2022–24 as well as 2025–26, so nothing from the prior pull
  is reused. There is one exception: authors already labelled in `labels/bio_labels_*.csv`
  keep their labels.
- Requested coverage: 2021-12-01 to the present, English, no retweets, `userLocation` kept.

**Split: the production split, as in `regression_baseline.ipynb` and `gdelt_lift.ipynb`.**
- **Train:** 2022-02-08 .. (2025-01-01 − k days). Training ends k days early, so no training
  outcome window reaches into the test period.
- **Test:** 2025-01-01 .. 2026-12-31. Rows are further limited to d + k ≤ the last
  `fact_h5n1_outbreak` date loaded at run time (2026-07-13 at drafting), so every test
  outcome window is complete.
- CONUS only (`is_conus`).

**Blinding**
- The pipeline must process 2025–26 posts to build features. Reading post text to label it is
  allowed; subagents do it.
- **Not allowed before the pipeline is frozen and this file is committed:**
  - joining 2025–26 X features to outcomes;
  - scoring any model on the test period;
  - looking at 2025–26 outbreak data alongside X data.
- The test period is scored **once**.
- The 2022–24 part of the repull may be inspected freely; that period was seen in the
  exploratory work.

**Frozen inputs** (SHA-256 at drafting; the run reports the hashes it used):

| input | hash / version |
|---|---|
| `data/X_derived/labels/topic_gold.parquet` (2,000 hand-labelled posts) | `d582ec02…800118` |
| `data/X_derived/labels/lo_screen.json` (screen thresholds) | `0a29f0b1…b7744` |
| `docs/x_topic_guide.md` (topic definitions) | `f826e6ed…11894b` |
| sentiment model `cardiffnlp/twitter-roberta-base-sentiment-latest`, `pytorch_model.bin` | `4d24a3e3…62cbae` |
| embedding model `sentence-transformers/all-MiniLM-L6-v2` | revision `1110a243fdf4…` |

**Frozen code**
- `h5n1/sources/x_location.py`, `x_posts.py`, `x_topics.py`
- `h5n1/sentiment/core.py`
- `h5n1/models/x_lift.py`

These files as committed alongside this document. Code changes after that commit which alter
any number are deviations.

## Pipeline, fixed

1. **Build** (`x_posts build`)
   - Dedup by post id.
   - Geocode the profile location to a state (`x_location`).
   - Flag copypasta: a normalized 80-character prefix shared by ≥5 posts from ≥2 authors;
     the earliest post of each group is kept.
2. **Account type** (`x_posts accounts`)
   - Existing labels are kept.
   - New authors with a farm/ag/animal-health bio term, and every new top-1% author, are
     labelled in-session by an LLM reading the bio, with the Appendix A definitions. No
     paid API call.
   - Everyone else gets the regex fallback.
3. **Hubs**: an author is a hub if in the top 1% by pre-2025 post count AND not farmer_keeper
   or ag_institution. The rule is in the `x_posts.py` docstring. Hubs are removed.
4. **Subset** (`x_posts subset`): state-geocoded, not a hub, not a non-first copypasta post.
5. **Sentiment** (`x_posts sentiment`)
   - fp32 TweetEval RoBERTa on every subset post.
   - Score = p_pos − p_neg.
6. **Firsthand-illness events** (`x_topics`)
   - (a) Screen: the MiniLM + logistic-regression screen, trained on the frozen gold set only
     (never retrained on repull posts), at review threshold **0.55**.
   - (b) **Every** flagged post is reviewed in-session with the Appendix B prompt, giving
     `local_obs` and `illness_seen`. There is no subsampling, however large the flagged set.
   - (c) Every `illness_seen` post then passes the Appendix C exclusion screen, done by a
     subagent that sees only post text, author state and date.
7. **Dedup**: one event per state per ISO week, the earliest post in that week.

## Features, fixed

Both are state-level, broadcast to every county in the state, and past-only (windows end at
day d; the outcome starts at d+1).

- `x_firsthand_30d`: the number of deduplicated firsthand-illness events in the state over
  [d−29, d]; 0 when there are none.
- `x_sent_7d`: the mean sentiment of the state's subset posts over [d−6, d]. It is filled
  with the **training-rows mean** where the state had no posts.

**Media share is excluded.** It measures news mix, and news was already null with GDELT. No
other X feature enters any primary comparison.

## Model, fixed

`h5n1/models/x_lift.py` is the `gdelt_lift.ipynb` design with X in place of GDELT:
- **Arms**
  - A: baseline, the 23 `model_county_day` predictors.
  - B: baseline + `x_firsthand_30d` + `x_sent_7d`.
- **Fills** (baseline predictors), as `gdelt_lift.prepare()`: train-window median for the
  three weather columns and `log_layer_inventory`, and 4.0 for censored recency.
- **Model:** Waree's production forest, `RandomForestClassifier(n_estimators=300,
  max_depth=6, min_samples_leaf=2)`, seeds 20260728, +1 and +2. Predictions are
  seed-averaged.
- **Training rows:** every positive plus negatives hash-sampled at 5% on (fips, day),
  weighted 1/0.05. For each k the training set is that k's positives plus the hash-sampled
  rows.
- **Test rows:** every eligible CONUS county-day, unsampled.
- **Targets**
  - `y_k` = any `fact_h5n1_outbreak` row in the county in (d, d+k], for k ∈ {7, 14, 30}.
  - For k = 7 the code asserts equality with `model_county_day.target_outbreak_next_7d`.
- **Slices**
  - all: every test row.
  - new-onset: `log_own_outbreaks_60d == 0`, as in `gdelt_lift`.

Run with:

    DB_PORT=5433 uv run --extra modeling python -m h5n1.models.x_lift --train-start 2022-02-08 --test-start 2025-01-01 --test-end 2026-12-31 --boot 2000 --ci 99.1667 --tag confirmatory

## The six comparisons and the claim rule

Δ = PR-AUC(B) − PR-AUC(A) on the same test rows, for each (k, slice):

| # | horizon | slice | exploratory Δ (95% CI), test year 2024 |
|---|---|---|---|
| 1 | 7 days | all | +0.0005 [−0.0016, +0.0020] |
| 2 | 7 days | new-onset | +0.0005 [−0.0002, +0.0018] |
| 3 | 14 days | all | −0.0007 [−0.0024, +0.0015] |
| 4 | 14 days | new-onset | +0.0002 [−0.0004, +0.0011] |
| 5 | 30 days | all | −0.0009 [−0.0019, −0.0001] |
| 6 | 30 days | new-onset | −0.0003 [−0.0009, +0.0002] |

**Inference:** a paired bootstrap over test ISO weeks (all counties in a week resampled
together), 2,000 reps. Resamples with no positives, or no negatives, are dropped.

**Multiplicity:** six comparisons, Bonferroni, so each uses a two-sided **99.17% CI**
(percentiles 0.4167 and 99.5833).

**Claim rules**
- **Lift** is claimed if at least one comparison's 99.17% CI lies entirely above 0. The claim
  names the comparisons that pass. It does not generalize to horizons or slices that fail.
- **Harm** (X makes the forest worse) is reported if a CI lies entirely below 0.
- Otherwise the result is **no detectable lift**. That is stated as a null over these six
  comparisons, not as proof of zero effect.
- All six rows are reported whatever the result, with base rates and baseline PR-AUC.

**Power note:** the exploratory 95% CIs had half-widths of about 0.001–0.002 PR-AUC. At
99.17% they are roughly 1.4× wider, and the 2025–26 test period is longer than 2024. So the
test can detect a lift of roughly 0.003 or more on all positives (3–4% of the baseline's
PR-AUC) and somewhat less on the new-onset slice. It cannot rule out smaller effects.

## Secondary analyses (reported, never used for claims)

- Impurity importance and rank of each X feature, per horizon.
- Arm C: B + media share.
- Each X feature alone (A + `x_firsthand_30d`, A + `x_sent_7d`).
- The same six comparisons on the repull's 2022–24 period (train 2022-02..2023, test 2024).
  This is the exploratory test rerun on the new corpus, and it is not independent.
- The state-day event study, the fixed-effects logits (the old P1–P3) and the state-day
  forest, on the full repull.
- The wild-bird target.
- Sentiment computed only on posts whose topic is not `outbreak_report`, if topic labels have
  been propagated.

## Things not decided in advance, and how they're handled

- **Repull composition.** New terms change who is in the corpus. No reweighting is attempted.
  Term-level post counts are reported.
- **Too few posts after 2025-01-01**, or an export that truncates the window: there is no
  confirmatory test. That is reported, and the exploratory null stays labelled as
  exploratory.
- **Package or model upgrades** that change outputs are deviations. Use the hashed files and
  the locked `uv.lock`.
- **`model_county_day` rebuilds** between now and the run (e.g. the 35-feature question in
  `rf_vs_gbm.ipynb`) are deviations. The run uses the 23-predictor table as built at run
  time, and reports its `model_build_log` build_id.

---

## Appendix A — account-type definitions (bio labelling)

One label per author:
- `farmer_keeper`: a person or family operation that actually raises animals or crops.
  - Includes farmers, ranchers, dairy/poultry/egg/turkey/hog growers, homesteaders,
    backyard chicken or duck keepers, a farm spouse or kid describing their own farm, and
    4-H/FFA youth.
  - Excludes people who merely like farms or food, chefs, crypto "yield farming", "Ranch"
    as a place name, and "beef" as slang.
- `ag_institution`: organizations or professionals serving agriculture or animal health.
  - Extension, state or federal ag departments, USDA/APHIS, state veterinarians,
    individual veterinarians, ag colleges and scientists, farm bureaus, commodity boards
    and producer associations, agribusiness companies, ag lenders, insurers and
    consultants.
- `media_aggregator`: news outlets, journalists, reporters, editors, TV/radio producers,
  anchors, ag media, outbreak trackers, alert accounts and bots, newsletters, and bloggers
  or podcasters whose account mainly relays news.
- `general_public`: everyone else. This includes scientists and clinicians outside
  agriculture, activists, politicians, and incidental ag words.

Precedence rules:
- An author who clearly farms is `farmer_keeper`.
- An ag journalist is `media_aggregator`.
- An ag vet is `ag_institution`.
- An empty bio is judged by display name, else `general_public`.

## Appendix B — firsthand-report review (two yes/no questions per post)

1. `local_obs`: is the post mainly a firsthand or near-firsthand local observation?
   - **Yes** if the author, or someone near them, saw or experienced it: family, a neighbor,
     their farm, their town's park, their organization's own animals.
   - That includes sick or dead wild birds or animals, sick or dying poultry or livestock,
     their own flock or herd affected, tested or kept indoors, a neighbor's farm quarantined,
     or a local closure they witnessed.
   - **No** if the post relays an announcement, news, a case count, statistics, a policy
     opinion, general fear or a joke.
2. `illness_seen`: stricter.
   - **Yes** only if it describes sick animals, dead animals, or a suspected or confirmed
     infection among animals the author or their circle directly observed or owns.
   - Precautions alone are **no**: birds kept inside, an aviary closed, a negative test.
   - `illness_seen` implies `local_obs`.

## Appendix C — exclusion screen for `illness_seen` posts

Exclude a post, recording the reason, if it is:
- `outside_us`: the described event is outside the US, whatever the author's profile says.
- `retrospective`: the event happened more than about 30 days before the post, or it is a
  general career or past-experience anecdote.
- `not_hpai`: the author says it tested negative or is another disease.
- `secondhand`: a report relayed from outside the author's state, with no local link.
- `not_credible`: a joke, sarcasm, or a conspiracy claim presented as an observation.

If the text names a different US state than the author's profile, the event is assigned to
the named state.
