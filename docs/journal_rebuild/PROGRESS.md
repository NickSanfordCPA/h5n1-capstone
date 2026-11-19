# Journal rebuild: progress log

Brief: `docs/journal_rebuild/FABLE_INSTRUCTIONS.md`. Updated after every completed step.

## Status at a glance

| Step | State |
|---|---|
| 6.1 Local environment | **Done.** Source tables copied and reconciled |
| 6.2 Outcomes and wild birds | **Tables done and verified.** Baseline *numbers* (the effect of re-dating on PR-AUC) wait for gate 1 |
| 6.3 WHISPers | **Loaded.** No public record-created date; lower-bound date built. Features wait for gate 1 |
| 6.4 Post pipeline | Steps 1–8 **done** for both platforms (`journal.post`). Guide J1 piloted. Full labelling waits for gate 1 |
| 6.5 Sentiment | **Done.** All 66,590 reader-set posts scored and loaded |
| **Gate 1: `PROTOCOL.md`** | **Committed.** |
| 6.6–6.10 | **Done** (2026-11-18). Human validation: packets out, agreement pending the coded files |
| Paper | **Draft 1 written**, self-reviewed; open items at the top of `paper/manuscript.md` and in `paper/self_review.md`. All X numbers provisional until Max's re-pull |

Protocol commit hash: `ef852d252226fd905b1a40a798dc6cc40956cca1` (committed 2026-11-17 at Nick's instruction; guide J1 frozen at `a03dcaf`)

## Environment facts (verified 2026-11-17)

- **Local Postgres:** service `postgresql-x64-18` (18.4), port 5432, user `postgres`.
  - The password is in `%APPDATA%\postgresql\pgpass.conf`, keyed to host **`localhost`**.
    Connecting to `127.0.0.1` fails with "no password supplied".
  - Client tools: `C:\Program Files\PostgreSQL\18\bin` (not on `PATH`).
- **Local database:** `h5n1_journal`. Source copies and the rebuilt baseline chain live in schema
  `public` (so the existing SQL runs unchanged); post tables are in schema `journal`.
- **Shared database:** proxy binary is `cloud-sql-proxy.exe` in the repo root; start it with
  `--port 5433`. Read-only for this job.
- **Git:** local branch `journal/rebuild`, from `main` at `8357ed6`. Not pushed.
- **`data/journal/`:** covered by `.gitignore` line 9 (`data/`).
- **Python:** new optional extra `journal` in `pyproject.toml` (`lingua-language-detector`,
  `krippendorff`).
- **Console:** set `PYTHONIOENCODING=utf-8` before running anything that prints post text.
- **XLM-R weights:** local copy in `data/reference/models/twitter-xlm-roberta-base-sentiment`
  (the Hugging Face hub drops connections from primary). Pass it with `--model`.
- **cuda1R cannot run a new Python environment:** its application-control policy blocks numpy's
  DLLs. Not worked around. GPU work went to cuda2R (`Start-FleetJob.ps1 -Gpu`).
- **`C:\Users\NickS\xlmr_score` on cuda1R no longer exists.** The scorer is now
  `h5n1/journal/xlmr_score.py`.

## Code (all under `h5n1/journal/`)

| File | What it does |
|---|---|
| `localdb.py` | `source_engine()` (read-only sessions, 5433) and `local_engine()` (refuses anything but 5432 / `h5n1_journal`) |
| `copy_source.py` | `pg_dump` → local `psql` for the source tables, then exact row counts. Never overwrites a table that has a `<t>_shared` copy |
| `wild_birds.py` | `fact_wild_bird_sample` (both dates), `fact_wild_bird_detection` (Date Detected), `fact_wild_bird_detection_coll` (Collection Date) |
| `build_baseline.py` | Runs 008 → 009 → 011 → 012 → rf35 → state_week locally, `--wild coll` then `--wild detected` |
| `posts.py` | Raw rows, dedup, language detector (steps 1–2) |
| `textgeo.py` | Text-only geocoder |
| `funnel.py` | Steps 3–8, `journal.post`, funnel counts |
| `whispers.py` | WHISPers download and load, with `known_date_lb` |
| `labels.py` | Reader batches, CSV checker, agreement between reads, adjudication batches |
| `label_funnel.py` | Merged label table, funnel steps 9–16 (`journal.post_label`, `journal.post_funnel`), `funnel.md` |
| `xlmr_score.py` | Standalone XLM-R scorer |
| `models.py`, `baseline.py` | Shared modelling core (splits, seeds, bootstrap, scores); state baseline search and re-dating report |
| `authors.py` | X author-type reader batches, checker, agreement, adjudication, `journal.author_type`, `journal.post_tag` |
| `extras.py` | `journal.state_week_extra` (WHISPers and GDELT state-week features), `journal.gdelt_state_day` |
| `tracks.py` | Track A and Track B on both platforms; Track A is resumable per platform and horizon (`track_a_part_*.pkl`) |
| `mechanism.py` | News-echo mechanism checks (protocol 9) |
| `sensitivity.py` | Sensitivity checks on both primaries (protocol 8.6); resumable per check |
| `county.py` | County baseline C3: cached modelling rows, search, test scores, roll-up to state-weeks (protocol 8.4) |
| `kappa.py` | Human validation: sample draw, coder workbooks, agreement analysis (protocol 11) |
| `crosscheck.py` | Read-only comparison with Max's `meta_post_analysis` / `meta_post_funnel` (brief 6.10) |
| `supplement.py` | Capstone supplement: language split of both primaries; emoji-removed rescoring inputs and comparison (brief 6.9) |
| `figures.py` | The four manuscript figures (`paper/figures/`) |

Documents: `GUIDE_reader.md` (version J1), `PROTOCOL.md` (gate 1 draft).

## Verified results

**Source copy (6.1).** 24 tables; every row count equals the shared database
(`data/journal/source_row_counts.csv`).

**Wild birds (6.2).** From `hpai-wild-birds.csv` (19,529 rows):
- 103 rows with county "Unknown" dropped, as in the original loader; 19,426 load.
- Date Detected is never blank. Lag (detected − collected): median 15 days, IQR 11–31, p90 151.
- Collection-dated rebuild equals the shared `fact_wild_bird_detection` exactly (8,465
  county-days). Detected-dated table: 7,331 county-days.

**Baseline chain (6.2).**
- Local collection-dated `model_state_week_coll` equals the shared `model_state_week` on all
  11,662 rows × 46 columns.
- Detected-dated `model_state_week` differs from it in the six wild-bird columns only; every
  target column and `state_new_onset` are identical. Rows that differ: 4,084 (own 7d) to 10,570
  (neighbour 60d). The 7-day own-state feature correlates 0.39 between the two datings.
- County: `model_county_day_35` vs `_coll` (7,524,342 rows) differ in wild-bird columns only;
  targets identical.

**WHISPers (6.3).** Public API, retrieved 2026-11-17, 12,481 events; 3,137 US events starting
2022+, 2,485 avian, 828 with an HPAI diagnosis. 65 event-county rows dropped (not in `dim_county`:
`02261`, territories). `known_date_lb − start_date`: median 8 days, p75 27, p90 70.

**Posts, steps 1–8 (6.4), final build.**

| | Meta | X |
|---|---:|---:|
| Rows collected | 485,652 | 603,265 |
| Unique posts | 301,044 | 540,225 |
| 2 Not English or Spanish | 994 | 22,830 |
| 3 Outside the window | 30,429 | 26,691 |
| 4 Quote or reshare | 4,973 | 9,030 |
| 5 Not about avian influenza | 27,985 | 28,423 |
| 6 No US state named | 199,891 | 405,078 |
| 7 More than one state | 4,631 | 4,372 |
| 8 Copypasta repeat | 6,209 | 3,143 |
| **Reader set** | **25,932** | **40,658** |

- Reader set 66,590: 47,972 dated 2022–24, 18,618 dated 2025+.
- X reader set by collector: prev_cohort_en 31,998; twitterapi_en 6,835; twitterapi_es 1,819;
  twitterapi_fr 6 (posts the detector reads as English or Spanish).
- Geo tier: state 49,578; county 10,649; large city 4,948; city with state 1,415.
- The X total equals the shared `x_post_unique` (540,225).
- `journal.post` holds all 841,269 unique posts with their dispositions.

**Pilot (6.4).** 600 posts dated 2022–24, read twice (`data/journal/labels/pilot1/`). κ between
reads: relevant 0.96, topic 0.93, content class 0.98, local place 0.93, illness 0.90, exclude 0.94,
place state 0.96, sighting candidate 0.99. 4.8% of posts differ on an analysis field. Readers
name one US state for about 65% of geocoded posts; where they do, it matches the geocoder
(99.7–100%). Eight clarifications added to the guide afterwards; **not re-piloted**. A reader run
of 300 posts used about 155,000 tokens and about 3 minutes.

**Sentiment (6.5).** 66,590 posts scored on cuda2R; `journal.post_sentiment`; none missing.
Control rescore of 300 posts on primary's CPU: maximum difference 1.7e-6, 15 over 1e-6, none over
1e-5. That is outside the brief's 1e-6 target by float noise, and is stated in the protocol.

**JMIR instructions for authors** (checked 2026-11-17,
`publichealth.jmir.org/author-information/instructions-for-authors`): IMRaD; AMA Manual of Style
11th edition; required end sections Acknowledgements, Funding Statement, Conflicts of Interest,
Data Availability, Authors' Contributions, Abbreviations; ethics in Methods. Abstract and word
limits and the AI-use policy are on linked pages, **not yet checked**.

## Mistakes caught and fixed

1. **Re-dated wild-bird table overwritten.** Re-running `copy_source.py` re-copied the legacy
   table over the re-dated one, so the first "detected" build was collection-dated. Caught
   because the two state-week tables were identical. Fixed; rebuilt; verified above.
2. **`exec_driver_sql` and percent signs** in `011`. `build_baseline.py` uses a raw psycopg cursor.
3. **Relevance rule missed styled Unicode capitals and subtypes glued to the next word.** Word
   rules now run on NFKC-normalised text.
4. **"Baja California" read as California.** Foreign places containing a state name are flagged
   and hidden.
5. **`funnel load` ran out of memory** building one CSV string; it now copies 50,000 rows at a
   time.

Fixes 3 and 4 were found by reading samples of 2022–2024 posts only.

## Defaults chosen without asking

All are listed for Nick's decision in `PROTOCOL.md` section 14: the wild-bird table shape; the
window; steps 4 and 8 kept and hub authors not removed; the reader's state used; class (a) read as
domestic detections; the news-like tag; adjudication scope; labelling volume; the WHISPers date;
the timing estimand; the first-public-report proxy.

Not in the protocol, because they are plumbing:
- Only base and source tables were copied; Max's `meta_post*` tables are read remotely in 6.10.
- GPU scoring ran on cuda2R, not cuda1R.

## Departures from the protocol

The protocol was committed as `ef852d2` (gate 1, 2026-11-17).

1. **Labelling scope (2026-11-17, Nick's instruction).** The protocol's option A is two reads of
   all 66,590 reader-set posts. After wave 1 batches 0-94 had been read, Nick reported that Max
   is re-pulling the X data with his own method, covering the prior-cohort period and the dates
   after his last pull. The unread prior-cohort X posts (`collector = prev_cohort_en`, 18,408
   posts) are therefore **not read for now**; the unread Meta posts and Max's `twitterapi_*` X
   posts were re-cut into `wave1f` and read twice. Reason: reading posts that the re-pull may
   replace would spend reader budget for nothing. Consequence to settle before any analysis:
   prior-cohort X is only partly labelled (the share that fell in wave 1 batches 0-94), so the
   X arm cannot be run on the protocol's reader set until the re-pull is in and labelled.

   **Amended 2026-11-18 (Nick's instruction).** A read-only check of the shared database
   showed Max had not updated the X data (`x_raw` and `x_csv_raw` identical to the 2026-11-17
   copy; still no `twitterapi_en` posts dated 2022-06-01 to 2024-12-31). Nick chose to read
   the prior-cohort X posts now. Labels are keyed by X post id and a reader sees only the id,
   date, language and text, so a label carries over to the re-pull wherever the same id
   returns with the same text; a changed text is re-read, a new id is read, and an id the
   re-pull does not return leaves its label unused. The 18,408 posts were cut into `wave1x`
   (62 batches, `labels.py focusx`, which checks that `wave1x` and `wave1f` partition the
   unread wave 1 posts). This returns the labelling to the protocol's option A.

2. **Exclusion days counted from the forecast Sunday (2026-11-18).** The protocol says "after
   the post". The Sunday is on or after the post, so this is at least as strict.

3. **Post hoc risk-level check (2026-11-18).** Not in the protocol; added after the Track B
   results were seen, because the self-review raised that sighted state-weeks sit at more
   than twice the expected risk. Details under "Update, 2026-11-18, third session, second
   part". Labelled post hoc in the manuscript.

4. **Supplement design (2026-11-18).** The protocol names a language and emoji comparison
   without specifying it. What was run is described in `supplement.md`.

## Next step

**Gate 1.** Nick reads `PROTOCOL.md`, changes what he wants, and commits it. Then:
1. Record the hash here.
2. Full labelling (option A or B from protocol 6.6), X author typing, adjudication.
3. `funnel.md`, then Tracks A and B, the baseline re-dating numbers, sensitivity and mechanism
   checks, the κ packets (gate 2), and the paper.

## Nick's review of the protocol draft (2026-11-17)

Applied to `PROTOCOL.md` (section 14 lists who decided what):
- New onset 28 days; longest horizon 28 days (30-day versions kept as a sensitivity check).
- Small hyperparameter search on the baseline only, inside 2022–2024, same setting for every arm;
  Waree's fixed settings as a sensitivity check.
- Steps 4 and 8 kept, each on its own merits (treat this as a new analysis).
- Hub authors not removed; NFKC normalisation approved.
- Track B: one sighting per state-week stays primary; dose-response added as secondary.
- Labelling option A (two reads of all 66,590), paced inside Nick's 5-hour usage limit: first
  group of 20 runs, then Nick reports his meter and the group size is set to at most 60% of a
  window.
- GPU work: cuda1L and cuda2L first; cuda2R only after testing. The sentiment scoring was already
  done on cuda2R before this rule and is verified against a CPU rescore, so it was not redone.

Code still to change to match (after the commit): `sql/builds/model_state_week.sql` needs a
28-day onset flag and a 28-day target; the model code needs the search and five seeds.

**Gate 1 passed 2026-11-17.** Next: fix batches, first 20 reader runs after the usage window resets.

## Update, end of 2026-11-17 (after gate 1)

*This section is current. "Status at a glance" and "Next step" above were written before gate 1
and are superseded by it.*

**Gate 1.** `PROTOCOL.md` committed as `ef852d252226fd905b1a40a798dc6cc40956cca1`. Post-gate code
committed as `f1a4077` (`models.py`, `baseline.py`, `build_state_week.py`, `authors.py`, the
28-day columns in `sql/builds/model_state_week.sql`).

**State-week tables.** `model_state_week` and `model_state_week_coll` rebuilt: 11,662 rows by 49
columns, differing in the six wild-bird columns only. New-onset share 0.7531 at 28 days.

**Baseline search** (2022-2024 folds only, S3, k = 14, new onset): chosen setting 500 trees,
depth 12, min leaf 50, features one third; mean new-onset PR-AUC 0.2181 against 0.1933 for
Waree's fixed setting. Depth 12 is the edge of the grid; the grid was not extended.

**State baseline, test period 2025+** (PR-AUC, mean of five seeds; detected minus collection
with its 95% interval). Files in `data\journal\models\`: `baseline_state.csv`,
`baseline_state_redating.csv`, `baseline_state_reliability.csv`.

| Setting | k | Slice | Detected | Collection | Difference |
|---|---|---|---:|---:|---|
| searched | 7 | new onset | 0.159 | 0.170 | -0.011 (-0.031, +0.007) |
| searched | 14 | all | 0.547 | 0.556 | -0.008 (-0.016, -0.000) |
| searched | 14 | new onset | 0.254 | 0.276 | -0.022 (-0.039, -0.007) |
| searched | 28 | new onset | 0.406 | 0.434 | -0.028 (-0.045, -0.009) |
| Waree | 14 | new onset | 0.266 | 0.287 | -0.021 (-0.038, -0.004) |
| Waree | 28 | new onset | 0.425 | 0.444 | -0.020 (-0.039, +0.002) |

Removing the wild-bird look-ahead lowers the baseline at every horizon. The searched forest does
not beat Waree's settings on the test period at k = 14 new onset (0.254 against 0.266) although
the development folds preferred it; both are reported and the primary setting is not switched.
These numbers have not been recomputed independently.

**Labelling.** One fresh Sonnet reader per read per batch of 300, one-line prompt pointing at
`READER_INSTRUCTIONS.md`; a read counts only if its CSV passes `labels check`.

| Set | Batches | Posts | Reads valid | Posts differing on an analysis field |
|---|---|---:|---|---:|
| wave 1, batches 0-94 (all sources, random order) | 95 | 28,500 | A 95, B 95 | 7.7% (on 27,900, before the B 84 and B 86 reads were counted) |
| `wave1f` (unread Meta, then twitterapi X) | 66 | 19,682 | A 66, B 66 | 6.5% (1,276 posts) |
| `wave1x` (prior-cohort X) | 62 | 18,408 | A 62, B 62 | 7.4% (1,368 posts) |

`wave1f` holds 14,821 Meta posts (batches 0-49) and 4,861 twitterapi X posts (batches 49-65:
3,814 en, 1,044 es, 3 fr). It was cut by `labels.py focus`, which keeps the wave 1 random order
within each group and refuses to rebuild. Wave 1 batches 95-221 are superseded.

Agreement between reads on `wave1f` (19,682 posts; `labels agree wave1f`, disagreements in
`data\journal\labels\wave1f\disagreements.csv`): kappa relevant 0.941, topic 0.877, content
class 0.947, local place 0.946, illness 0.922, exclude 0.917, place kind 0.923, place state
0.967, sighting candidate 0.947. Sighting candidates: A 1,546, B 1,552, both 1,474. The largest
content-class confusion is domestic detection against other (206 and 131).

Agreement on all of `wave1x` (18,408 posts, 2026-11-18; all 124 reads launched pass the
checker, none rerun): kappa relevant 0.886, topic 0.840, content class 0.947, local place
0.974, illness 0.928, exclude 0.958, place kind 0.874, place state 0.959, sighting candidate
0.972. Sighting candidates: A 1,373, B 1,364, both 1,333. The largest content-class confusion
is again domestic detection against other (179 and 236). 1,368 posts (7.4%) differ on an
analysis field.

`labels status <wave>` now runs the checker on every CSV (it previously only counted files).

Agreement on wave 1, rerun 2026-11-18 on all 28,500 twice-read posts (`labels agree wave1`):
kappa relevant 0.909, topic 0.847, content class 0.939, local place 0.956, illness 0.919,
exclude 0.926, place kind 0.893, place state 0.953, sighting candidate 0.949. Sighting
candidates: A 2,110, B 2,123, both 2,016. 2,182 posts (7.7%) differ on an analysis field.
The largest content-class confusion is again domestic detection against other (321 and 250).

**Adjudication (2026-11-18).** `labels.py adjbatches <wave>` fixes the adjudication batches
(refuses to rebuild): every post in the wave's `disagreements.csv`, 100 per batch, in
`data\journal\labels\<wave>\adj\`. Each line carries the post and both reads as `read_1` and
`read_2`, shuffled per post so the adjudicator cannot tell read A from read B (key in
`<wave>\adj_key.csv`, outside the folder the adjudicator reads). One fresh Opus subagent per
batch reads the guide and gives one full label row per post; it may give a third answer where
the guide requires one. Its CSV must pass the same `labels check`. Wave 1: 2,182 posts, 22
batches. `wave1f`: 1,276 posts, 13 batches. `labels adjstatus <wave>` runs the checker;
`labels adjsummary <wave>` counts decisions with read A, with read B, or neither.

All 35 Opus runs passed the checker (`adjstatus`: wave 1 22 of 22, `wave1f` 13 of 13); none
was rerun. Each run used 98k-114k tokens and about 3 minutes. Decisions on the posts where the
two readers differed on that field:

| Field | Wave | Differed | With A | With B | Neither |
|---|---|---:|---:|---:|---:|
| relevant | wave 1 | 452 | 255 | 197 | 0 |
| relevant | `wave1f` | 253 | 118 | 135 | 0 |
| content class | wave 1 | 947 | 506 | 434 | 7 |
| content class | `wave1f` | 562 | 253 | 309 | 0 |
| place state | wave 1 | 1,250 | 705 | 482 | 63 |
| place state | `wave1f` | 617 | 285 | 307 | 25 |
| sighting candidate | wave 1 | 201 | 115 | 86 | 0 |
| sighting candidate | `wave1f` | 150 | 79 | 71 | 0 |

Sighting candidates among adjudicated posts: wave 1 A 112, B 125, adjudicated 116; `wave1f`
A 90, B 96, adjudicated 66. Where the readers disagreed on sighting status the adjudicator
said yes for 96 of 201 (wave 1) and 49 of 150 (`wave1f`). The adjudicator also changed a
field both readers agreed on in a few posts (content class 23, place state 34, sighting
status 3 across both waves); these stand, because the adjudicator gives the whole row.

Position check (the shuffle key): the adjudicator sided with the first-listed read slightly
more often on place state (wave 1 658 against 529; `wave1f` 318 against 274) and about evenly
on content class (472 against 468; 295 against 267). Because the order was shuffled per post,
this lean does not favour either reader.

**`wave1x` adjudication (2026-11-18, after all 62 batches had both reads).** 1,368 posts in 14
batches; all 14 Opus runs pass the checker (`adjstatus wave1x`: 14 of 14), none rerun, each
88k-103k tokens and about 2 minutes. Decisions where the readers differed on that field:

| Field | Differed | With A | With B | Neither |
|---|---:|---:|---:|---:|
| relevant | 275 | 133 | 142 | 0 |
| content class | 559 | 298 | 260 | 1 |
| place state | 706 | 329 | 325 | 52 |
| sighting candidate | 71 | 35 | 36 | 0 |

Sighting candidates among adjudicated posts: A 46, B 37, adjudicated 33. Fields both readers
agreed on but the adjudicator changed: content class 3, place state 40, sighting status 1;
these stand. The position check on the shuffle key was not rerun for `wave1x`.

With this, every one of the 66,590 reader-set posts has two valid reads, and all 4,826
posts that differed on an analysis field (2,182 + 1,276 + 1,368) are adjudicated.

**Merged labels and funnel steps 9-16 (2026-11-18).** `label_funnel.py merge` writes one label
row per reader-set post (`data\journal\labels\merged.parquet`, `journal.post_label`): the
adjudicated row for the 4,826 adjudicated posts (Meta 1,707, X 3,119), read A's row for the
other 61,764. Read A's topic is kept on adjudicated rows unless the adjudicated `relevant`
makes it invalid. The step aborts unless the labels are exactly one row per reader-set post,
the adjudicated ids are exactly the posts whose reads differ, and every row passes the
checker's consistency rules. `label_funnel.py build` applies steps 9-16
(`journal.post_funnel`) and aborts unless steps 9-14 reproduce the sighting-candidate
definition; `report` writes `funnel.md`.

| | Meta | X |
|---|---:|---:|
| Reader set | 25,932 | 40,658 |
| 9 Not about avian influenza (reader) | 3,526 | 2,794 |
| 10 Not about one US state (reader) | 3,064 | 4,560 |
| **Sentiment set (Track A)** | **19,342** | **33,304** |
| 11 No sick or dead animals at a local place | 14,791 | 23,171 |
| 12 Announced domestic detection | 2,229 | 4,065 |
| 13 Other content | 511 | 612 |
| 14 Retrospective / Not HPAI / Not credible / Outside the US | 76 / 24 / 0 / 0 | 2,216 / 4 / 4 / 0 |
| 15 Outside the contiguous states | 41 | 208 |
| **Sighting posts** | **1,670** | **3,024** |
| 16 Same state-week as an earlier sighting | 1,165 | 2,197 |
| **Sightings (Track B)** | **505** | **827** |

- Sighted state-weeks by period: Meta 216 (2022-24) and 289 (2025+); X 413 and 414.
- State moves: the reader's state differs from the geocoder's for 117 Meta and 144 X posts in
  the sentiment set (0.6% and 0.4%).
- **X sightings are concentrated in three relay accounts.** Of 3,024 X sighting posts, 1,279
  are from `account A`, 400 from `account B` and 209 from `account C` (62% together); they
  supply the first post in 506 of the 827 sighted state-weeks. `account A` posts one line per
  row of the APHIS wild-bird table; 2,127 more of its posts were removed at step 14 as
  retrospective because the row's date was more than about 30 days old. So much of the X
  sighting series restates the wild-bird detections that the baseline already uses, at their
  publication date. Meta has no such account (the largest author has 15 of 1,670 sighting
  posts). This was found from author counts only, with no outcome data; the protocol's
  most-active-authors check (8.6) will show its effect.

**Track B, both platforms (2026-11-18; `tracks.py b`, searched forest, five seeds, 2,000
week-block replicates).** Files in `data\journal\models\`: `track_b.csv`, `track_b_dose.csv`,
`track_b_trend.csv`, `track_b_panel.parquet`, `track_b.log`. On the 49-state grid there are 482
Meta and 789 X sighted state-weeks; 290 and 418 are in the test period. The O/E ratio compares
sighted with non-sighted test state-weeks against the calibrated S3 risk.

| | Meta | X |
|---|---|---|
| **Primary: k = 14, all sightings** | **1.21 (1.06, 1.37)**; 136 of 290 followed, 112.9 expected | **1.13 (0.97, 1.31)**; 161 of 418 followed, 142.0 expected |
| Same, first 5 days excluded | 1.19 (0.96, 1.41) | 1.27 (1.06, 1.51) |
| Same, first 3 days excluded | 1.26 (1.06, 1.47) | 1.18 (1.00, 1.38) |
| Reading rule (lower bound > 1 and 5-day estimate > 1) | **met** | **not met** (interval includes 1) |
| k = 7 | 1.36 (1.19, 1.56) | 1.12 (0.95, 1.29) |
| k = 28 | 1.27 (1.15, 1.38) | 1.28 (1.16, 1.41) |
| k = 14, new onsets | 1.50 (1.07, 1.98); 26 of 95 | 1.11 (0.79, 1.47); 36 of 174 |
| k = 14, 2025-Q1 | 1.12 (0.98, 1.30) | 1.15 (0.96, 1.34) |
| k = 14, later weeks | 1.21 (0.98, 1.46) | 1.09 (0.87, 1.33) |
| k = 14, development period (cross-fitted) | 1.16 (0.97, 1.35) | 1.17 (1.02, 1.33) |
| Share of outbreak state-weeks flagged, k = 14 | 19% | 23% |

- Dose-response: no rising pattern on either platform. At k = 14 the ratio for 1, 2–3 and 4+
  sighting posts is 1.27, 1.24, 1.10 on Meta and 1.11, 1.25, 1.11 on X; the trend coefficient
  on log(1 + posts) is −0.31 (SE 0.22) on Meta and +0.12 (SE 0.30) on X.
- The Meta interval with the first 5 days excluded reaches below 1; the rule asks only that
  the estimate stay above 1, which it does. The two platforms' intervals overlap, so this
  table alone does not show that Meta differs from X.
- Not yet verified by an independent recomputation.

**X author types and the news-like tag (2026-11-18).** `authors.py` gained reader
instructions, a checker, `agree`, `adjbatches` and `merge`. Pilot: both reads of batch 0 (500
accounts) agreed on 498 (kappa 0.991); both readers' scratch scripts only wrote out lists of
the accounts they had judged, so the guide was used unchanged. Then the other 44 Sonnet runs:
23 of 23 batches valid on both reads, none rerun. On 11,460 accounts agreement is 0.992,
kappa 0.982; the 91 that differed went to one Opus run (checker-valid; 51 organisation, 40
individual). `authors merge` writes `journal.author_type` and `journal.post_tag`.

| | Meta | X |
|---|---:|---:|
| Authors: organisation | 8,080 (Pages) | 4,125 (189 from `verifiedType`) |
| Authors: individual | 845 (profiles) | 7,524 |
| Reader-set posts by an organisation (all news-like) | 24,378 | 15,175 |
| Posts by an individual, news-like by content class | 283 | 9,845 |
| Posts by an individual, personal | 1,271 | 15,638 |

Meta has few personal posts (1,271 of 25,932 in the reader set), so the personal cells of the
mechanism checks will be small on Meta. The author guide was not piloted beyond batch 0 and
has no human check yet.

**Cost and limits measured.** A reader run uses 125k-165k Sonnet tokens and 3-4 minutes, about
0.5% of a 5-hour usage window. The harness refuses more than 64 concurrent subagents.

## Update, 2026-11-18, third session

*This section is current; it supersedes the "Not started" and "Next step" notes that used to
end this file.*

**Track A had died mid-run** in the previous session (log stopped at Meta k = 28, no result
files). `tracks.py a` now saves each finished platform and horizon and resumes from them; it
was rerun from the start.

**Mechanism checks (protocol 9; `mechanism.py`, k = 14, 2,000 week-block replicates).** Files in
`data\journal\models\`: `mechanism_cells.csv`, `mechanism_interaction.csv`,
`mechanism_timing.csv`, `mechanism_timing_rows.parquet`, `mechanism_dropped.csv`,
`mechanism_proxy.csv`, `mechanism_news.csv`, `mechanism_panel.parquet`, `mechanism.log`. A
state-week is sighted for a group if it has a sighting post of that group; the reference is
always the state-weeks with no sighting post on that platform. The gap-0 "all" rows reproduce
Track B exactly (Meta 1.21, 136 of 290, 112.9 expected; X 1.13, 161 of 418).

Sighted test state-weeks by group: Meta wildlife report 251, first-hand 80, news-like 285,
personal 12; X wildlife report 410, first-hand 19, news-like 413, personal 12. Almost every
sighted state-week is news-like on both platforms, so the news-like cell is nearly the
whole-platform result and the personal cell cannot be estimated.

O/E ratio at k = 14, test period, by days excluded from the start of the outcome window (the
estimand of the timing test):

| Group | Platform | 0 days | 3 days | 5 days | 7 days | 10 days |
|---|---|---|---|---|---|---|
| All | Meta | 1.21 (1.06, 1.37) | 1.26 (1.06, 1.47) | 1.19 (0.96, 1.41) | 1.19 (0.96, 1.41) | 1.43 (1.10, 1.82) |
| All | X | 1.13 (0.97, 1.31) | 1.18 (1.00, 1.38) | 1.27 (1.06, 1.51) | 1.26 (1.04, 1.51) | 1.40 (1.08, 1.79) |
| Wildlife report | Meta | 1.22 (1.06, 1.40) | 1.31 (1.10, 1.55) | 1.24 (1.00, 1.49) | 1.24 (1.00, 1.49) | 1.47 (1.11, 1.88) |
| Wildlife report | X | 1.15 (0.98, 1.32) | 1.19 (1.01, 1.40) | 1.28 (1.07, 1.52) | 1.27 (1.05, 1.52) | 1.41 (1.08, 1.80) |
| First-hand | Meta | 1.22 (0.94, 1.50) | 1.12 (0.85, 1.43) | 1.05 (0.72, 1.40) | 1.04 (0.71, 1.40) | 1.19 (0.66, 1.86) |
| First-hand | X | 0.78 (0.31, 1.29) | 0.91 (0.38, 1.51) | 1.00 (0.33, 1.71) | 1.01 (0.33, 1.72) | 1.28 (0.00, 2.37) |
| Personal | Meta | 1.28 (0.70, 2.02) | 0.83 (0.17, 1.66) | 0.75 (0.00, 1.79) | 0.75 (0.00, 1.80) | 1.08 (0.00, 3.00) |
| Personal | X | 1.09 (0.43, 1.77) | 1.30 (0.56, 2.17) | 1.36 (0.39, 2.28) | 1.37 (0.39, 2.30) | 1.45 (0.00, 2.77) |

- The 5- and 7-day columns are nearly identical because no confirmation in the data is dated
  on a Saturday or Sunday.
- **The ratio does not shrink when the first days are excluded.** On both platforms it is as
  large or larger with a 10-day exclusion (only days 11 to 14 count) as with none. A signal
  that was only news of an outbreak awaiting confirmation would fade as the window moves away
  from the post; this one does not.
- **The signal sits in wildlife reports.** First-hand posts show no clear excess on either
  platform once the first days are excluded (Meta 1.05 at 5 days), with wide intervals; the
  personal cells have 12 state-weeks each and are inconclusive.
- **The platforms do not differ.** Pooled offset-logistic model, tag by platform: the
  news-like odds ratio on Meta relative to X is 1.15 (0.82, 1.62) at 0 days and 0.95 (0.70,
  1.28) at 5 days; the ratio of the two O/E ratios is 1.06 (0.89, 1.26) and 0.94 (0.78, 1.12).
  For wildlife reports: 1.12 (0.77, 1.59) and 1.01 (0.74, 1.38). First-hand: 2.47 (0.69,
  9.32) at 0 days, 1.11 (0.30, 4.97) at 5. Personal: 1.50 (0.17, 13.8). Bootstrap replicates
  that failed to fit (an empty cell): 4 to 123 of 2,000, most in the personal model.
- **News-volume control.** With the state's 7-day GDELT story count and share added to S3 (196
  state-weeks with unknown GDELT dropped from both models), the Meta ratio is 1.18 (1.04,
  1.34) against 1.20 (1.05, 1.35) for S3 on the same rows; X 1.12 (0.96, 1.29) against 1.13
  (0.97, 1.30). S3 + news does not rank better than S3 (PR-AUC 0.550 against 0.549, all rows).
  The Meta effect does not disappear.
- **Timing (description).** Of 292 Meta sighted test state-weeks, the next confirmation in the
  state came within 3 days of the first sighting post for 76, within 7 for 113, within 14 for
  150; median 5 days among those within 28. X: 76, 126 and 184 of 419; median 7 days.
- **First-public-report proxy (GDELT): it does not discriminate.** For 121 of the 136 followed
  Meta sightings (142 of 161 on X) a GDELT story naming the state already existed in the 14
  days before the confirmation and before the post. A state with outbreaks has avian-influenza
  news nearly every week, and the proxy cannot tell a story about this flock from any other
  story. Removing those sightings leaves O/E ratios of 0.30 and 0.26, which are lower bounds
  with no interpretive value. The paper should report the proxy as uninformative, not as
  evidence for or against the echo.
- **WHISPers reference.** An HPAI-diagnosed WHISPers record in the state in the 14 days before
  the confirmation exists for only 22 of 136 followed Meta sightings (17 of 161 on X); 13 and
  10 of those were posted on or after the record's lower-bound date. Without them the ratios
  are 1.17 (1.01, 1.33) and 1.11 (0.94, 1.28).
- **Descriptive only:** removing sighted state-weeks whose confirmation came within N days of
  the Sunday drives the ratio below 1 at every N (0.90 at 3 days, 0.55 at 5, 0.21 at 10 on
  Meta). This selects on the outcome, as the protocol says, and is not an estimate.

**Reading, under the protocol's two framings.** The signal does not vanish under the exclusion
windows or the news control, so it is not explained by the announcement-to-confirmation
interval. It does not sit in first-hand posts either; it sits in relayed reports of wild-bird
and mammal die-offs, on both platforms about equally. So neither framing fits cleanly: the
supportable statement is that news and agency reports of local wildlife die-offs, as they
circulate on social media, are followed by more poultry outbreaks than the baseline expects
over the following two weeks. Whether that adds to official wild-bird surveillance is what the
S3 + WHISPers check tests (below). Not independently recomputed.

**Departures and defaults in the mechanism checks.**
- The exclusion gap is counted from the forecast Sunday, not from the post (the protocol says
  "after the post"). The Sunday is on or after the post, so this is at least as strict.
- A group's reference is state-weeks with no sighting post at all; state-weeks sighted only by
  another group are left out of that group's comparison.
- The proxy and WHISPers recomputations remove the "after" sightings (only sightings that were
  followed can be classed), so they are lower bounds.

**Track A, both platforms (`tracks.py a`, searched forest, five seeds, 2,000 week-block
replicates).** Files in `data\journal\models\`: `track_a_lift.csv`, `track_a_arms.csv`,
`track_a_offset.csv`, `track_a_permutation.csv`, `track_a_coverage.csv`, `track_a_meta.csv`,
`track_a_reliability.csv`, `track_a_oof_<platform>_<variant>_k<k>.csv`, `track_a.log`.
Sentiment set 19,342 Meta and 33,304 X posts; shrinkage pseudocount 3.41 and 4.66. Rows are
covered test state-weeks (at least one post in the 7 days): at k = 14, 1,148 of 3,822 on Meta
and 1,316 on X; 490 and 585 of them are new onsets, with 86 and 96 events (of 254 new-onset
event state-weeks in the test period, so about a third of the events are on covered rows).

S4 − S3 PR-AUC on new-onset state-weeks:

| | Meta | X |
|---|---|---|
| **Primary: k = 14** | **−0.000 (−0.013, +0.011)**; S3 0.353, S4 0.353 | **−0.002 (−0.010, +0.006)**; S3 0.378, S4 0.376 |
| Post windows ending 5 days early | −0.004 (−0.014, +0.005) | +0.002 (−0.003, +0.006) |
| Post windows ending 3 days early | −0.004 (−0.012, +0.006) | −0.007 (−0.018, +0.000) |
| Reading rule (interval above 0 and 5-day estimate positive) | **not met** | **not met** |
| k = 7 | +0.001 (−0.011, +0.013) | −0.001 (−0.011, +0.011) |
| k = 28 | +0.001 (−0.004, +0.006) | +0.003 (−0.005, +0.008) |
| k = 14, all rows | −0.001 (−0.002, +0.001) | −0.000 (−0.001, +0.001) |
| k = 14, 2025-Q1 | −0.003 (−0.027, +0.016) | −0.003 (−0.012, +0.031) |
| k = 14, later weeks | +0.001 (−0.014, +0.015) | −0.004 (−0.012, +0.003) |
| k = 14, at least 5 posts | −0.001 (−0.034, +0.019) | −0.007 (−0.023, +0.015) |
| k = 14, volume only (S4v − S3) | −0.005 (−0.021, +0.006) | −0.002 (−0.009, +0.005) |
| k = 14, sentiment only (S4s − S3) | −0.003 (−0.012, +0.004) | −0.004 (−0.011, +0.001) |
| k = 14, residualised features | −0.009 (−0.022, −0.001) | −0.003 (−0.010, +0.005) |
| k = 14, block permutation gain (p) | +0.005 (0.18) | −0.003 (0.90) |
| k = 14, S2 − S0 (no epidemiology) | −0.017 (−0.048, +0.014) | −0.036 (−0.077, −0.001) |

- **Posts add nothing to the baseline on either platform at any horizon.** The upper bounds
  say the largest gain the data allow at k = 14 is about +0.011 PR-AUC on Meta and +0.006 on
  X, against a baseline of 0.35 to 0.38.
- Posts alone (S1) rank no better than chance on new onsets (lift 1.08 on Meta, 0.82 on X),
  and adding them to place and season (S2 − S0) does not help.
- Seed standard deviation of the new-onset PR-AUC is 0.005 to 0.009, the same size as the
  differences. Brier scores are equal with and without posts (0.132 on Meta, 0.121 on X).
- One secondary points the other way: in the offset logistic model on new onsets, the state's
  share of posts has a positive coefficient per standard deviation (Meta +0.21, SE 0.10,
  p = 0.04; X +0.21, SE 0.08, p = 0.007); sentiment terms are null. The offset there is the
  forest's raw prediction, not a calibrated one, and no multiplicity correction is applied, so
  this is weak; it does not appear as a ranking gain (S4v − S3 above).
- Not independently recomputed, except that `sensitivity.py` reproduces the two primary
  figures through a separate code path.

**County baseline C3 and its roll-up (protocol 4.3 and 8.4; `county.py`).** Files in
`data\journal\models\`: `county_sample_<dating>.parquet`, `county_search.csv`,
`chosen_county.json`, `county_scores.csv`, `county_reliability.csv`,
`county_rollup_preds.csv`, `county_rollup.csv`.
- **Rows.** 968,124 contiguous-US county-days from 2022-02-08: every positive, a 5% hash
  sample of the rest (negatives weighted 20 in training), and every county-Sunday. Validation
  and test rows are county-Sundays, unsampled. This is a default: the protocol says "all rows".
- **Search** (2022-2024 folds, k = 7, one seed per fit, also a default): chosen 500 trees,
  depth 12, min leaf 50, features one third, mean PR-AUC 0.0945 against 0.0908 for Waree's
  setting. The whole grid spans 0.088 to 0.095, so the choice is weakly determined.
- **County test scores** (county-Sundays 2025+, five seeds), PR-AUC and lift over the base rate:

  | k | Events | Searched, detected | Waree, detected | Searched, collection |
  |---|---:|---|---|---|
  | 7 | 622 of 245,532 | 0.143 (57x) | 0.157 (62x) | 0.143 |
  | 14 | 1,112 of 242,424 | 0.128 (28x) | 0.137 (30x) | 0.131 |
  | 28 | 1,973 of 236,208 | 0.128 (15x) | 0.135 (16x) | 0.132 |

  As at state level, Waree's fixed setting beats the searched one on the test period although
  the development folds preferred the searched one. Re-dating wild birds costs the county
  model 0.003 to 0.004 at k = 14 and 28 and nothing at k = 7.
- **Roll-up against S3** (S3 − roll-up PR-AUC, all test state-weeks; S3 is the searched state
  forest, detected dating):

  | k | Slice | Searched C3 | Waree C3 |
  |---|---|---|---|
  | 7 | all | −0.031 (−0.051, −0.011) | −0.019 (−0.039, +0.002) |
  | 7 | new onset | −0.016 (−0.048, +0.017) | +0.027 (−0.005, +0.063) |
  | 14 | all | −0.013 (−0.030, +0.005) | +0.003 (−0.015, +0.020) |
  | 14 | new onset | −0.025 (−0.063, +0.009); S3 0.254, roll-up 0.279 | +0.031 (−0.003, +0.068) |
  | 28 | all | +0.003 (−0.016, +0.021) | +0.018 (+0.001, +0.035) |
  | 28 | new onset | −0.028 (−0.065, +0.013) | +0.049 (+0.004, +0.089) |

  **This differs from the earlier exploratory result**, where S3 beat the roll-up on new
  onsets at k = 14 by +0.042 (+0.014, +0.075). With Waree's county forest the direction is the
  same as before (+0.031). With the searched county forest the roll-up is at least as good as
  S3 on every slice and better at k = 7 on all rows. So which model wins depends on the county
  forest's settings, and the earlier "S3 for which state" reading is not robust to them.
- **On each platform's Track A rows** (covered state-weeks), neither S3 nor S4 differs from the
  roll-up on new onsets at k = 14 (Meta S4 − roll-up +0.033 (−0.050, +0.121); X +0.034 (−0.035,
  +0.096)), and S4 − roll-up equals S3 − roll-up to the third decimal, as it must given the
  Track A null.
- **Reading (protocol 8.4).** The roll-up comparison does not decide whether social media is
  used; Track A does, and it is null. The comparison now says only that the county model,
  summarised to states, is not worse than the state model.
- The collection-dated roll-up rows in `county_rollup.csv` are compared with the
  detected-dated S3, so they mix datings; use `county_scores.csv` for the re-dating effect.
- Not independently recomputed. `county.py rollup` ran once, with no smoke test before it.

**Sensitivity checks (protocol 8.6; `sensitivity.py`, k = 14 unless stated, 2,000 replicates).**
Files in `data\journal\models\`: `sensitivity_a.csv`, `sensitivity_b.csv`,
`sensitivity_counts.csv`, `sensitivity.log`. The "nothing changed" row reproduces Track B
exactly. Track A is S4 − S3 PR-AUC on new-onset state-weeks; Track B is the O/E ratio on all
sightings, then with the first 5 days excluded.

| Check | Track A, Meta | Track A, X | Track B, Meta (0 d; 5 d) | Track B, X (0 d; 5 d) |
|---|---|---|---|---|
| Nothing changed | −0.000 (−0.013, +0.011) | −0.002 (−0.010, +0.006) | 1.21 (1.06, 1.37); 1.19 | 1.13 (0.97, 1.31); 1.27 |
| Collection-date wild birds | +0.001 (−0.011, +0.013) | +0.002 (−0.004, +0.007) | 1.18 (1.03, 1.33); 1.17 | 1.11 (0.95, 1.27); 1.25 |
| Waree's forest | +0.006 (−0.009, +0.022) | +0.001 (−0.006, +0.012) | 1.21 (1.06, 1.37); 1.19 | 1.14 (0.99, 1.31); 1.29 |
| S3 + WHISPers baseline | +0.005 (−0.014, +0.022) | +0.003 (−0.004, +0.008) | 1.21 (1.06, 1.37); 1.18 | 1.13 (0.97, 1.30); 1.27 |
| No poultry words | −0.013 (−0.026, +0.006) | −0.002 (−0.009, +0.004) | 1.17 (1.01, 1.35); 1.15 | 1.14 (0.96, 1.32); 1.32 |
| No top-1% authors | −0.003 (−0.015, +0.008) | +0.005 (−0.008, +0.015) | 1.20 (1.04, 1.35); 1.21 | 1.10 (0.88, 1.35); 1.14 |
| X profile state | n/a | −0.000 (−0.006, +0.004) | n/a | 1.13 (0.98, 1.30); 1.30 |
| k = 30, 30-day onset | +0.001 (−0.006, +0.008) | +0.001 (−0.003, +0.006) | 1.28 (1.17, 1.39); 1.25 | 1.28 (1.16, 1.39); 1.32 |

- **Track A is null under every check on both platforms.** No interval excludes zero on the
  positive side. (These Track A figures come from `sensitivity.py`; the full `tracks.py a` run,
  with all arms and secondaries, is recorded separately below once it finishes.)
- **The Meta Track B result holds under every check**: the lower bound stays above 1 with no
  days excluded, and the 5-day estimate stays above 1. Adding WHISPers avian-event counts to
  the baseline does not reduce it (1.21), so the sightings are not just restating WHISPers.
- **X stays short of the reading rule at k = 14** in every check with no days excluded, and
  exceeds 1 clearly with 5 days excluded or at the 30-day horizon.
- **Top authors.** Dropping the top 1% of authors removes 72% of X sighting posts (3,232 to
  908) and 46% of X sentiment posts; 106 sighted X test state-weeks remain, ratio 1.10 (0.88,
  1.35). On Meta it removes 5% of sighting posts and changes nothing.
- **Poultry words.** 32% of Meta and 23% of X sentiment-set posts contain one; the Meta ratio
  falls slightly, to 1.17 (1.01, 1.35).
- **30-day onset at k = 14 is identical to 28-day** on every row: the two definitions differ
  only by confirmations dated on a Saturday or Sunday, and the data have none.
- New-onset Track B cells (Meta 26 of 95 followed, ratio 1.50 (1.07, 1.98)) are in the CSV.

**Defaults in the sensitivity checks.** A poultry word is the English and Spanish list in
`POULTRY_RE` (it catches "Turkey" the country on purpose). Top authors are the top 1% of each
platform's authors by reader-set posts dated through 2024 (Meta 58 of 5,701 authors, at least 20 posts each; X 104 of 10,342, at least 25 each). The
profile-state check uses the X profile state where one exists (10,955 posts; it differs from
the text state for 5,436) and re-admits sighting candidates removed at step 15 when their
profile state is on the grid. Not independently recomputed.

**Human validation packets (gate 2; `kappa.py`).** 400 posts drawn once (seed 20261118) from
the 66,590 labelled posts, stratified by platform, language and stratum; the stratum is the
first that applies of: sighting post (100), readers disagreed (60), not relevant or not one
state (40), then content class (50 each). Within a stratum the split is 35% each for English
Meta and X and 15% each for Spanish, with a small cell's surplus given to the others (there is
no Spanish first-hand Meta post outside the earlier strata, and one on X). 345 distinct authors.
- `docs\journal_rebuild\coding\coding_Nick.xlsx`, `coding_Max.xlsx`, `coding_Waree.xlsx`
  (ignored by git through `*.xlsx`): sheet "Posts" (date, language, first 1,500 characters;
  drop-downs for `relevant`, `sick_dead_local`, `content_class`, `state_correct`) and sheet
  "Authors" (name, handle and bio, or Facebook type; `author_type`), each in a different order
  per coder, with a made-up example row.
- `docs\journal_rebuild\coding\CODER_GUIDE.md` (one page) and `sample_design.csv` (cell
  populations, sample sizes, weights).
- The key linking items to posts, strata and reader labels is in `data\journal\coding\`
  (`sample_key.parquet`, `sample_weights.csv`), outside the folder the coders open.
- The human sighting question bundles the reader fields `illness`, `local_place` and `exclude`
  into one yes/no (`sick_dead_local`); a human sighting is that answer plus a wildlife or
  first-hand class. Author type is coded on its own sheet without posts, as the readers did it.
- `kappa.py analyse` was tested on simulated coder files (reader labels with 10% noise): it
  runs end to end, and the test caught a row-alignment error in the derived sighting field,
  now fixed. Real agreement is pending the three coded files.

## Update, 2026-11-18, third session, second part

*Everything after the validation packets: the risk-level check, the cross-check, the
supplement, the figures and the paper package.*

**Post hoc risk-level check (`mechanism.py --risk-check`; file `mechanism_risk_adjusted.csv`).**
Sighted state-weeks have a mean expected risk of 0.39 against 0.16 for the rest on Meta (0.34
against 0.16 on X), so a baseline that under-predicts at high risk could give an O/E ratio
above 1 with no information in the posts. The check compares sighted with unsighted
state-weeks within fifths of expected risk (a risk-standardised ratio) and fits a logistic
model of the outcome on the sighting indicator with the baseline logit as a recalibrated
covariate, standard errors clustered on week.

| Days excluded | Platform | Crude ratio | Risk-standardised | OR, recalibrated (95% CI) | Top-fifth O/E, all rows |
|---|---|---|---|---|---|
| 0 | Meta | 1.21 | 1.24 | 1.59 (1.20, 2.11) | 1.01 |
| 0 | X | 1.13 | 1.14 | 1.32 (0.99, 1.75) | 1.01 |
| 5 | Meta | 1.19 | 1.21 | 1.34 (0.95, 1.89) | 1.07 |
| 5 | X | 1.27 | 1.30 | 1.49 (1.12, 1.98) | 1.07 |
| 10 | Meta | 1.43 | 1.34 | 1.43 (1.03, 1.98) | 1.34 |
| 10 | X | 1.40 | 1.32 | 1.41 (1.01, 1.99) | 1.34 |

- The excess is not a risk-level artefact at 0 to 7 days: standardising changes nothing.
- The rise at a 10-day exclusion is partly miscalibration: for that late window the baseline
  under-predicts in the top fifth of risk (O/E 1.34 over all rows), and the standardised
  ratios (1.34, 1.32) are close to those at shorter exclusions. The abstract quotes the
  standardised figures.
- The standardised ratios have no bootstrap interval yet (self-review M3).
- Not independently recomputed.

**Cross-check against Max's tables (brief 6.10; `crosscheck.py`, shared database read-only
through the proxy on 5433, since stopped; files in `data\journal\crosscheck\`).** Max labels a
week by its Monday and this rebuild by the Sunday that ends it; his weeks were shifted by 6
days before state-weeks were compared.
- 301,044 unique Meta posts in both pipelines.
- Geocoded state: both give one for 22,990 posts and agree on 22,862 (99.4%); 9,151 have a
  state only here and 7,379 only in Max's.
- Reader set 25,932 here against 30,369 candidates in his, 22,723 in common. Of the 7,646
  only in his, 4,943 fall outside this rebuild's window and 1,488 name no state by its rules.
- Sighting posts 1,670 here, 1,422 in his, 964 in both. On the 17,612 posts judged for
  sightings in both, κ = 0.79 (357 his only, 119 ours only). The state agrees on 99.5% of
  posts both call sightings.
- Sighted state-weeks 505 here, 506 in his, 376 in common. Of this rebuild's sighting posts,
  212 are by authors he removed as hubs and 375 are not in his funnel.
- In the paper as Multimedia Appendix 6 (`paper/appendices.md`).

**Supplement (brief 6.9; `supplement.py`; `docs\journal_rebuild\supplement.md`; files in
`data\journal\supplement\`).** Language: both primaries with English-only and Spanish-only
posts. Emoji: the 7,386 sentiment-set posts containing an emoji (Meta 3,186; X 4,200) were
rescored with the emoji removed, on primary's CPU, and Track A rerun with those scores.
- Track A is null in each language (English-only Meta +0.002 (−0.010, +0.015), X −0.003
  (−0.011, +0.009); Spanish-only Meta −0.006 (−0.023, +0.011); Spanish-only X has 5 events).
- Track B: English-only sightings reproduce the pooled result (Meta 1.21 (1.06, 1.38) from
  282 sighted state-weeks; X identical to pooled, since every sighted X state-week has an
  English sighting). Spanish-only Meta: 12 followed of 40 where 16.9 were expected, ratio
  0.69 (0.43, 0.96); 0.79 (0.37, 1.21) with 5 days excluded. Unplanned small cell.
- Emoji: 14% of sentiment-set posts have one (10% and 11% of English, 47% and 39% of Spanish
  on Meta and X). Removing them changes the mean score by 0.01 or less in every cell, the
  label of 4% to 7% of those posts, and Track A not at all (Meta +0.000 (−0.013, +0.011), X
  −0.002 (−0.010, +0.006)).
- The `sighting_posts` column in `language_b.csv` and `sensitivity_counts.csv` includes the
  step-15 posts (outside the contiguous states: 41 Meta, 208 X), which the state-week grid
  then drops. That is why it sums to 1,711 and 3,232 and not to the funnel's 1,670 and 3,024.
  `supplement.md` reports the funnel counts.
- `supplement run` executed once, without a smoke test, and ran clean. Not independently
  recomputed.

**Figures (`figures.py`; `paper\figures\fig1_funnel.png`, `fig2_track_a.png`,
`fig3_track_b.png`, `fig4_mechanism.png`).** Static PNG; two hues (Facebook `#2a78d6`, circle;
X `#eb6834`, square) that passed the dataviz skill's contrast and colour-blind checks;
dot-and-interval charts with a reference line; the funnel is points on a log axis. Each was
rendered and inspected. Known flaw: two count labels touch in figure 4B.

**Paper package (`docs\journal_rebuild\paper\`).**
- `manuscript.md` (source) and `manuscript.docx` (pandoc, rebuilt after the last edit).
  Abstract 444 words; main text about 6,300 words.
- `appendices.md`: Multimedia Appendices 5 (calibration table and county baseline), 6
  (cross-check) and 7 (sensitivity). Appendices 1 to 4 are `checklists.md`, `PROTOCOL.md`
  with this file's departures, `funnel.md`, and the two guides. Appendices were renumbered
  in order of first citation.
- `checklists.md`: EPIFORGE 2020 (19 items, read from the PLoS Medicine article 2026-11-18)
  and TRIPOD+AI machine-learning topics (**from memory**, flagged in the file).
- `self_review.md`: `paper-review` skill, self-review mode. Four major comments (the result
  re-estimates a pattern already seen; no human validation yet; risk-level confounding,
  partly addressed; X provisional and dependent on three accounts) and eleven minor.
- Tables 1 to 3 and the dose-response figures were re-checked against the result CSVs on
  2026-11-18 and agree.

**Changes made to the manuscript after the self-review.** Abstract quotes the
risk-standardised 10-day ratios; "stronger baseline" and "gain of practical size" reworded;
a calibration sentence and table; the residual-confounding reading of the missing
dose-response; one sentence on the cross-check; references corrected and extended.

**JMIR checks (2026-11-18).** Structured abstract (Background, Objective, Methods, Results,
Conclusions), 450 words maximum: from a web search of JMIR support pages; the abstract
guideline page itself returned 403. AI use is declared in a submission form and published as
its own statement, and authors are asked to keep prompts and transcripts and submit them as
a Multimedia Appendix: from a search summary of the JMIR policy page, not read directly. The
instructions-for-authors page was fetched. These should be re-read by an author at
submission.

**Reference check (2026-11-18, Sonnet subagent, web search and fetch).** References 1-4, 8,
9, 12-14 confirmed; 6 corrected (Movahedi Nia et al, J R Soc Interface 2026, Canadian data)
and 7 completed (Zou, Carlton, Grover; medRxiv, April 1, 2026); 10 and 11 partly confirmed; 5
unpublished. Added as references 15 to 17: Pang et al 2025 (Reddit, JMIR), Soltani et al
2025 (social media and Google Trends, the closest competing claim) and Petie et al 2026
(wild-bird mortality, Netherlands). **The full texts of 6, 15, 16 and 17 were not read**; the
sentences describing them rest on search summaries. The subagent's query log was not kept.
Unresolved leads are in `self_review.md`.

**Still open.**
- Human validation analysis (`kappa.py analyse`) when the three workbooks return.
- The X rerun on Max's re-pull: every X number changes; carry-over rules are in the
  "Departures" section and in the manuscript's comment block.
- Bootstrap intervals for the risk-standardised ratios; the Platt-calibrated reliability
  table; compute cost; data-source references; LLM version identifiers; a logged literature
  search; the 292 and 419 in the timing description above against 290 and 418 elsewhere.
- Nick's items: IRB determination, Meta publication terms, course AI policy, author order
  and Muthu's role, deadline, Waree's sign-off on the re-dated baseline.

## Update, 2026-11-19: X rerun on Max's re-pull (`x_post_unique_ss`)

*This section is current for every X number. Where it conflicts with an earlier section, this
one holds. The earlier X outputs are kept in `data\journal\_x_v1_prior_cohort\`.*

**What changed in the data.** Max published `x_post_unique_ss` (287,777 posts) and its raw
table `x_raw_en_hist` (112,481 lines). The English posts of 2022-06-01 to 2024-12-31 now come
from twitterapi.io with the same two query groups (`phrases`, `subtypes`) as the 2025+ pull,
as collector `twitterapi_en_hist` (111,772 posts). The prior cohort's export (`x_csv_raw`,
364,220 posts) is no longer read. The other collectors are identical to before, post for
post (176,005 ids, same text, same dates).

**Seam check (shared database, read-only), 2024-12 against 2025-01.**

| Check | Result |
|---|---|
| Collector and raw schema | Same on both sides (30 JSON keys, same set) |
| Query groups | Same two; the same odd terms ("chicken flu", "duck flu", LPAI) appear on both sides; every `subtypes` post names a subtype somewhere in its raw record on both sides |
| Monthly English posts | 9,948 (2024-12), 11,947 (2025-01); daily counts continuous across the new year |
| Post mix | Original / reply / quote 45 / 44 / 11% in 2024-12 and 43 / 47 / 11% in 2025-01 |
| Text | Never truncated on either side (long posts up to about 25,000 characters) |
| Day assignment | Every row's UTC date equals its query day, both sides |
| **Completeness** | **Not equal.** The new pull has no rows for 102 of 1,890 query-days (5.4%); 2025+ has 7 of 1,276 (0.5%). On 66 more busy query-days (60 posts or more) it has nothing before 06:00 UTC; 2025+ has 4. About 4,500 posts are missing on the empty days by the neighbouring-day average, on 112,481 collected. The days are scattered through the whole period, not clustered at the seam |
| Cross-group duplicates | The new pull keeps a post found by both groups twice (709 rows); the 2025+ pull has none. Removed at step 1, no effect |

Reading: the collector switch at the seam is gone. The 2022-2024 pull is about 4 to 7% short
relative to 2025+, on scattered days. That is small against the earlier break (a different
scraper, different terms, 72.6k against 28.1k posts across the seam) and it lowers
development-period counts only. The gap list for Max is `docs\journal_rebuild\x_repull_gaps.csv`
(179 rows). Labels are keyed by post id, so a later top-up of those days would need readers
only for the posts it adds.

**The overlap with the prior-cohort corpus is small.** 99,183 of the 111,772 new posts were
in the prior-cohort corpus, but only those 99,183 of its 364,220 posts return (27%): the
prior cohort searched broader terms. Of its 31,998 reader-set posts, 9,747 are in the new
corpus and 8,843 are in the new reader set (867 of the rest are quote posts, which the old
export did not mark).

**Funnel, X (Meta is identical to the archived build on all 301,044 rows).**

| | X before | X now |
|---|---:|---:|
| Rows collected | 603,265 | 289,455 |
| Unique posts | 540,225 | 287,777 |
| 2 Not English or Spanish | 22,830 | 21,600 |
| 3 Outside the window | 26,691 | 26,691 |
| 4 Quote or reshare | 9,030 | 20,814 |
| 5 Not about avian influenza | 28,423 | 20,242 |
| 6 No US state named | 405,078 | 177,384 |
| 7 More than one state | 4,372 | 2,376 |
| 8 Copypasta repeat | 3,143 | 338 |
| **Reader set** | **40,658** | **18,332** |
| 9 Not about avian influenza (reader) | 2,794 | 1,329 |
| 10 Not about one US state (reader) | 4,560 | 2,385 |
| **Sentiment set** | **33,304** | **14,618** |
| Sighting posts | 3,024 | 1,260 |
| **Sightings** | **827** | **586** (171 in 2022-24, 415 in 2025+) |

X reader set by collector: `twitterapi_en_hist` 9,670; `twitterapi_en` 6,837; `twitterapi_es`
1,819; `twitterapi_fr` 6.

**Label carry-over (Nick's decision, 2026-11-19: "same words").** The prior cohort's export
had lost commas and line breaks, so only 1,673 of the 8,843 returning labelled posts are
character-identical. `labels.py carry` keeps a label when the wording is the same on letters
and digits, without links and leading mentions (`same_words_key`): 43,186 labels carried
(36,265 identical text, 6,921 same words). 1,078 posts went to `wave2`: 249 whose wording
changed (nearly all were cut short in the old export) and 829 never read (322 new ids, 210
that earlier steps used to remove, 297 that are now the first copy of a copypasta group).

- `wave2`: 4 batches, two Sonnet reads each (8 runs), all checker-valid, none rerun. Kappa:
  relevant 0.935, topic 0.868, content class 0.940, local place 0.950, illness 0.955, exclude
  0.863, place kind 0.903, place state 0.951, sighting candidate 0.966. 85 posts (7.9%)
  differed on an analysis field; one Opus run adjudicated them (checker-valid).
- `label_funnel.py merge` now takes `carried.parquet` plus `wave2`; `merged_v1.parquet` and
  `reader_text_v1.parquet` hold the earlier label table and the text it was read from.
- X author types: 183 accounts new to the reader set were read twice (`authors.py batches2`,
  `authors\round2`): agreement 0.978, kappa 0.946, 4 adjudicated by one Opus run. X now has
  4,900 reader-set authors: 2,128 organisation, 2,772 individual.
- Sentiment: 7,999 X posts whose text is new or differs in any character were rescored on
  primary's CPU (`sentiment.py`); the rest keep their 2026-11-17 scores. For the 7,170
  rescored posts that had an old score, restoring commas and line breaks moved the score by
  0.037 on average (correlation 0.985) and changed the label of 4.6%.

**Control.** Every Meta row in every result file reproduces the archived run to float
precision (largest difference 4e-14), so the rerun changed nothing it should not have.

**Results, X.** The 2025+ X posts are the same posts as before, so **every X test-period
Track B and mechanism number is unchanged** (primary 1.13 (0.97, 1.31), 161 of 418; all
exclusion windows, cells, news control, risk check, platform comparison). What changed:

| | X before | X now |
|---|---|---|
| Track A primary, k = 14, new onset (S4 − S3) | −0.002 (−0.010, +0.006); S3 0.378 | **−0.010 (−0.018, +0.001)**; S3 0.403, S4 0.393 |
| k = 7 | −0.001 (−0.011, +0.011) | +0.001 (−0.005, +0.011) |
| k = 28 | +0.003 (−0.005, +0.008) | −0.003 (−0.014, +0.007) |
| Post windows ending 5 days early | +0.002 (−0.003, +0.006) | −0.004 (−0.014, +0.003) |
| Volume only / sentiment only | −0.002 / −0.004 | −0.003 (−0.013, +0.012) / +0.002 (−0.007, +0.011) |
| Block permutation gain (p) | −0.003 (0.90) | −0.005 (0.95) |
| Offset model, share of posts, new onset | +0.21 (SE 0.08) | +0.23 (SE 0.08), p = 0.003 |
| Training rows (covered development state-weeks) | 1,975 | 1,108 |
| Shrinkage pseudocount | 4.66 | 4.14 |
| Track B, development period, k = 14 | 1.17 (1.02, 1.33); 134 of 346 | 1.27 (1.02, 1.53); 54 of 136 |
| Track B without top-1% authors | 1.10 (0.88, 1.35); 106 state-weeks; 72% of sighting posts removed | 1.14 (0.95, 1.36); 120 state-weeks; 59% removed (35 of 3,439 authors) |
| Three largest relay accounts' share of sighting posts | 62% (`account A` 1,279) | 48%: `account B` 389, `account C` 179, `account D` 34; `account A` 25 |

- Track A is still null on X. The estimate is now slightly negative and its upper bound is
  +0.001. In the sensitivity table two X intervals lie wholly below zero (profile state
  −0.008 (−0.019, −0.003); 30-day horizon −0.006 (−0.012, −0.000)); none lies above.
- The development-period X sightings fell from 346 to 136 sighted state-weeks on the grid,
  mostly because `account A`'s row-by-row posts of the APHIS wild-bird table are not in the
  new pull. The development-period first-hand and personal cells (18 and 12 state-weeks)
  show large ratios at long exclusions (3.7 and 3.8 at 10 days); they are tiny
  development-period cells and are not reported in the paper.
- County roll-up on X Track A rows: S4 − roll-up +0.051 (−0.024, +0.136) at k = 14 new onset.
- Supplement: English-only X Track A +0.005 (−0.005, +0.015); emoji-removed X −0.009
  (−0.016, +0.003); 17% of sentiment-set posts now contain an emoji.

**Documents updated:** `paper\manuscript.md` (and `.docx`), `paper\appendices.md`,
`supplement.md`, `paper\self_review.md` (M4), `funnel.md`, figures 1 to 4.

**Departure 5 from the protocol (2026-11-19).** The protocol (its X data section and the
"X collector switch" row of its sensitivity table) describes two English X collectors. There
is now one. The first-quarter and later-weeks rows are still reported. Labels for returning
posts were carried over under the same-words rule above instead of being read again.

**Human-validation sample.** 79 of the 204 sampled X posts are no longer in the reader set
(125 remain; all 196 Meta posts remain). `kappa.py analyse` has **not** been changed yet: it
must drop those 79 and recompute the X weights against the new strata, and the team decides
on a top-up. Never-read posts are 829 of 18,332 (4.5% of the X reader set), below the 10-15%
threshold agreed earlier, but the surviving X sample is 125, not 204.

**Not done.** No independent recomputation. The cross-check with Max's Meta tables was not
rerun (Meta did not change). The gaps in the new pull are not filled. The "Status at a
glance" table and the Track B, mechanism and sensitivity tables earlier in this file still
show the old X development-period and Track A numbers.

## Update, 2026-11-19, second part: human-validation analysis after the X re-pull

*Supersedes the "Human-validation sample" paragraph of the section above, which said
`kappa.py analyse` had not been changed.*

**What changed (`h5n1\journal\kappa.py`).** The sample and the three workbooks are untouched;
coders finish all 400 items. A new step, `kappa.py retained`, compares the drawn sample with
the reader set as it is now and writes `docs\journal_rebuild\coding\sample_design_current.csv`
(counts only). `analyse` now reports the coder majority against the reader labels in four
scopes:

1. **Primary: the current reader set, weighted.** Posts still in the reader set, with the
   labels the analysis uses now, weighted by current cell population over retained posts in
   the cell (post-stratification on stratum, platform and language).
2. All 400 coded posts against the label each had when the sample was drawn, unweighted. This
   speaks to the reading method only, not to the current corpus.
3. and 4. The same unweighted comparison split into posts still in the reader set and posts
   no longer in it.

Agreement among the three coders still uses all 400 items.

**What is left of the sample.** 321 of 400 posts: all 196 Facebook posts and 125 of 204 X
posts. Every one of the 79 lost posts is an English X post; all 52 Spanish X posts remain.

| English X stratum | Sampled | Retained | Reader-set posts now | Never eligible for the draw |
|---|---:|---:|---:|---:|
| Sighting | 35 | 14 | 1,230 | 46 |
| Disagreement | 21 | 9 | 1,413 | 83 |
| Not included | 14 | 7 | 2,807 | 280 |
| Domestic detection | 19 | 10 | 3,238 | 167 |
| Wildlife report | 19 | 7 | 1,143 | 73 |
| First-hand | 25 | 13 | 71 | 3 |
| Other | 19 | 13 | 6,616 | 426 |
| **All English X** | **152** | **73** | **16,518** | **1,078** |

- No cell that has posts now is left without a retained sample, so the weights cover the
  whole reader set. One cell has fewer than 5 retained posts (Spanish X first-hand: 1 of 1).
- 2 retained X posts had their wording changed in the re-pull and were read again. The coders
  saw the old text, so the primary scope leaves them out (319 posts). 1 retained post changed
  stratum. No retained post changed geocoded state.
- "Never eligible" are reader-set posts read after the draw (`wave2`): 1,078 of the 16,518
  English X posts (6.5%). The weights treat them like the other posts in their cell.
- X authors in the primary scope: 103 of the 156 sampled.

**Defaults chosen without asking.** Post-stratification on the current cells, not the
original design weights; the 2 re-read posts left out of the primary; no top-up drawn.

**Test.** `analyse` ran end to end on simulated workbooks (labels at the draw with 10% noise
per field): all four scopes report, and the "no longer in the reader set" scope has 79
posts. The simulated numbers mean nothing. Real agreement still waits for the three coded
files.

**Open, for the team:** whether to draw a top-up. English X sightings are the thin cell that
matters most: 14 coded posts stand for 1,230.

## Update, 2026-11-19, third part: X rerun after the repair of the missing query-days

*Current for every X number. Overrides the X numbers in the two sections above and in the
older tables of this file.*

**What happened.** Max re-ran the query-days listed in `x_repull_gaps.csv` and published the
result: a new raw table `x_raw_repair` (37,426 lines, each marked `_repair`) and a larger
`x_post_unique_ss` (287,777 to 310,202 posts). Nick asked for the repair to be confirmed and
the X analysis rerun.

**Is the repair real? Yes.**

| Check (English query-days, 2022-06 to 2026-09) | Before | After |
|---|---:|---:|
| No rows for the query group on the day, 2022-24 | 102 | 5 |
| Cut short (no posts before 06:00 UTC on a busy day), 2022-24 | 66 | 1 |
| No rows, 2025-26 | 7 | 1 |
| Cut short, 2025-26 | 4 | 0 |

- 173 of the 179 listed query-days now have posts. The 6 still empty are all `subtypes`
  days with 8 to 16 posts on neighbouring days. Each cut-short day now starts within the
  first hour except one (2024-11-23, first post 07:48).
- Repaired empty days hold a median of 1.2 times their neighbours' mean; repaired cut-short
  days hold a median of 1.9 times what they held before.
- Max went beyond the list: 233 English query-days were re-run in all (60 not on the list,
  many of them days that had stopped at 40 posts), plus Spanish (5,649 lines) and French (429)
  days, and some days before June 2022 that the analysis window drops.
- 37,386 repair posts: 14,961 were already in the corpus and 22,425 are new. The union of
  the three raw tables equals `x_post_unique_ss` exactly (310,202 ids, none missing either
  way), and `posts.py` reproduces that count.
- The 7 remaining days are in `x_repull_gaps.csv`; the earlier list is kept as
  `x_repull_gaps_before_repair.csv` (both git-ignored).

**Pipeline changes.**

- `posts.py` reads `x_raw_repair`; an English repair line belongs to the 2022-24 collector
  when its query day is before 2025-01-01. `copy_source.py` copies the table.
- `labels.py carry3`: a reader-set post with a label keeps it when its wording is unchanged
  (the same-words rule); the rest are cut into `wave3`. All 44,247 returning posts had
  identical text. 17 labelled X posts left the reader set (not traced; the likely cause is that a newly collected
  earlier copy of the same text now takes their place).
- `label_funnel.py` merges the kept labels (`labels/carried3.parquet`) with `wave3`.
- `authors.py`: third typing round (`authors/round3`); an account read earlier that now
  carries a Business or Government `verifiedType` is typed by the metadata rule (2 accounts).
- `sentiment.py`: scores are kept where the text is identical; 1,465 posts scored on CPU.
- `kappa.py`: posts read in `wave3` count as re-read, like `wave2`.
- Archive of the pre-repair run: `data/journal/_x_v2_before_repair/`.

**Reading (Nick authorised all 15 runs).** 1,465 posts in 5 batches, read twice by Sonnet;
kappa 0.92 relevance, 0.92 content class, 0.96 state, 0.94 sighting candidate; 118 posts
(8.1%) adjudicated by Opus. 63 new authors read twice; 1 adjudicated. All 15 runs passed
their checkers.

**Counts.**

| | Before repair | After |
|---|---:|---:|
| Unique X posts | 287,777 | 310,202 |
| X reader set | 18,332 | 19,780 |
| X sentiment set | 14,618 | 15,756 |
| X sighting posts | 1,260 | 1,317 |
| X sightings (state-weeks, all periods) | 586 | 601 |

Of the 1,465 new reader-set posts, 1,303 are English 2022-24, 130 English 2025-26 and 32
Spanish. Because 2025-26 gained posts, the X test-period numbers moved this time.

**Results, X (Facebook reproduced in every file, largest difference 4e-14).**

| | Before repair | After |
|---|---|---|
| Track B primary, test (14 days) | 1.13 (0.97, 1.31); 161 of 418 | 1.15 (0.99, 1.32); 164 of 424 |
| Track B, first 5 days excluded | 1.27 (1.06, 1.51) | 1.30 (1.08, 1.55) |
| Track B, 7 days / 28 days | 1.12 / 1.28 | 1.10 / 1.28 |
| Track B, development period | 1.27 (1.02, 1.53); 136 | 1.27 (1.04, 1.51); 145 |
| Track A primary (new onset, 14 days) | −0.010 (−0.018, +0.001) | +0.000 (−0.011, +0.011) |
| Track B without top-1% authors | 1.14 (0.95, 1.36); 120 state-weeks | 1.15 (0.97, 1.36); 122 state-weeks |
| Wildlife-report sightings | 1.15 (0.98, 1.32); 410 | 1.16 (1.00, 1.33); 416 |
| Within fifths of expected risk | 1.14 | 1.16 |
| Recalibrated odds ratio | 1.32 (0.99, 1.75) | 1.35 (1.02, 1.79) |
| Platform ratio of O/E ratios (Facebook over X) | 1.06 (0.89, 1.26) | 1.05 (0.88, 1.25) |
| Three largest accounts' share of sighting posts | 48% (602 of 1,260) | 47% (622 of 1,317) |

- No conclusion changes. X Track B is still just short of the reading rule (lower bound
  0.985). X Track A is still null; its estimate moved from −0.010 to 0.000 on almost the
  same rows (588 against 585), which shows how little the Track A point estimates mean at
  this size. The earlier interval had nearly excluded zero on the negative side; the new one
  is centred on zero.
- Track A sensitivity on X: 1 of 8 intervals is below zero (the alternative forest, −0.017),
  where before it was 2 (profile location and the 30-day horizon). These flip with small
  changes in the data and should not be interpreted.

**Human-validation sample.** `kappa.py retained` now finds 326 of 400 posts in the reader set
(196 Facebook, 130 X). The 5 posts that came back were read again in `wave3`, so the primary
scope is unchanged at 319 posts.

**Documents updated.** `paper/manuscript.md` and `.docx`, `paper/appendices.md`,
`paper/self_review.md` (M4), `supplement.md`, `funnel.md`, all four figures.

**Not done.** The older tables in this file still show earlier X numbers. `PROTOCOL.md` is
unchanged. The Spanish and French pulls were not gap-checked by query group. No result has
been independently recomputed.
