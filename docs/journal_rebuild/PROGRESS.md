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
| 6.6–6.10, paper | Open. Labelling first |

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
| `labels.py` | Reader batches, CSV checker, agreement between reads |
| `xlmr_score.py` | Standalone XLM-R scorer |

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
| `wave1x` (prior-cohort X), batches 0-24 | 25 | 7,500 | A 25, B 25 | 7.1% (535 posts) |
| `wave1x` batches 25-61, not yet read | 37 | 10,908 | - | - |

`wave1f` holds 14,821 Meta posts (batches 0-49) and 4,861 twitterapi X posts (batches 49-65:
3,814 en, 1,044 es, 3 fr). It was cut by `labels.py focus`, which keeps the wave 1 random order
within each group and refuses to rebuild. Wave 1 batches 95-221 are superseded.

Agreement between reads on `wave1f` (19,682 posts; `labels agree wave1f`, disagreements in
`data\journal\labels\wave1f\disagreements.csv`): kappa relevant 0.941, topic 0.877, content
class 0.947, local place 0.946, illness 0.922, exclude 0.917, place kind 0.923, place state
0.967, sighting candidate 0.947. Sighting candidates: A 1,546, B 1,552, both 1,474. The largest
content-class confusion is domestic detection against other (206 and 131).

Agreement on `wave1x` batches 0-24 (7,500 posts, 2026-11-18; all 50 reads pass the checker):
kappa relevant 0.905, topic 0.844, content class 0.949, local place 0.974, illness 0.934,
exclude 0.960, place kind 0.880, place state 0.960, sighting candidate 0.975. Sighting
candidates: A 552, B 554, both 540. Not yet adjudicated.

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

**Cost and limits measured.** A reader run uses 125k-165k Sonnet tokens and 3-4 minutes, about
0.5% of a 5-hour usage window. The harness refuses more than 64 concurrent subagents.

**Not started.** X author typing (23 batches, guide `GUIDE_author.md`, unpiloted), the merged
label table, funnel steps 9-16 and `funnel.md`, the county baseline,
Tracks A and B, sensitivity and mechanism checks, WHISPers and GDELT state-week features, the
kappa packets (gate 2), the paper.

**Next step.** The merged label table (adjudicated row where one exists, otherwise the agreed
reads; read A's topic kept unless the adjudicated `relevant` makes it invalid), then funnel
steps 9-16 and `funnel.md`; alongside, the work that needs no readers (county baseline,
WHISPers and GDELT features). Labelling of Max's re-pulled X waits for the
data.
