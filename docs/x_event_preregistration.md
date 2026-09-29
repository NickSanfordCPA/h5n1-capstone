# X social-media features vs the production baseline: pre-registration

**Drafted on 2026-09-29, before Muthu's X repull was received or looked at.** Everything in
this file is fixed ahead of those data: the pipeline, the two X features, the model, the six
comparisons, the claim rule. Anything decided later is labelled as a deviation in the results.

This supersedes an uncommitted draft from 2026-09-28. That draft pre-registered state-day
logistic regressions. It was replaced once the project asked the question the way it asked
GDELT's: does X add lift to **Waree's production forest**, not to a weaker stand-in?

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
