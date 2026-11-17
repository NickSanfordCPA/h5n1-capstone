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
| **Gate 1: `PROTOCOL.md`** | **Written. Waiting for Nick to approve and commit.** |
| 6.6–6.10, paper | Blocked by gate 1 |

Protocol commit hash: *(none yet; Nick commits it)*

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

*(None. The protocol is not yet committed.)*

## Next step

**Gate 1.** Nick reads `PROTOCOL.md`, changes what he wants, and commits it. Then:
1. Record the hash here.
2. Full labelling (option A or B from protocol 6.6), X author typing, adjudication.
3. `funnel.md`, then Tracks A and B, the baseline re-dating numbers, sensitivity and mechanism
   checks, the κ packets (gate 2), and the paper.
