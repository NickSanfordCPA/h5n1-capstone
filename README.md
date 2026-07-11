# H5N1 Social-Media Early-Warning — Harvard MDS Capstone

Modeling social-media signals as early-warning indicators for USDA-confirmed H5N1
poultry outbreaks in the U.S. Team: Nick Sanford, Waree Protprommart, Max Tang.

See **[ARCHITECTURE_SKETCH.md](ARCHITECTURE_SKETCH.md)** for the full design (data
flow, county×day schema, notebook→Modal handoff).

## Stack

| Concern | Choice |
|---|---|
| Warehouse | Cloud SQL **Postgres** (`dim_`/`fact_` star, county×day grain) |
| Raw storage | **GCS** buckets (immutable, versioned) |
| Environment | **Docker** + `uv` (pinned deps, one shared image) |
| Collaboration | **GitHub** + required PR review on `main` |
| Scaled GPU | **Modal** (sentiment phase) |
| Experiment tracking | **W&B** (modeling phase) |

## First-time setup

1. **Provision GCP** (Nick, once): follow `infra/gcp_bootstrap.sh` block by block.
2. **Clone + configure:**
   ```bash
   git clone <repo-url> && cd Capstone
   cp .env.example .env        # fill in from the team password manager
   ```
3. **Environment** (pick one):
   ```bash
   # Local, with uv:
   uv sync                     # add --extra ml for torch/transformers
   # or Docker:
   docker build -t h5n1 .
   ```
4. **Connect to Postgres** — start the Cloud SQL Auth Proxy (exposes it on
   127.0.0.1:5432):
   ```bash
   cloud-sql-proxy "$CLOUD_SQL_INSTANCE" --port 5432
   ```
5. **Create the schema:**
   ```bash
   python sql/run_migrations.py
   python -c "from h5n1.db import check_connection; print(check_connection())"
   ```

**Definition of done for infra:** all three of us, from inside the container, can
connect to shared Postgres, query it, and open a PR that `main` requires to merge.

## Layout

```
h5n1/         shared library (imported by notebooks AND Modal jobs)
  sources/    raw pulls, one module per source
  clean/      raw -> conformed fact transforms
  sentiment/  pure translate + score functions
  features/   builds feature_county_day
  models/     model wrappers + permutation test
  db.py       the one way to connect to Postgres
ingest/       thin CLI entrypoints per source
modal_jobs/   Modal app definitions (sentiment phase)
notebooks/    exploration only — not the source of truth
sql/          versioned DDL migrations + runner
manifests/    frozen-snapshot ledgers (url, checksum, capture date)
infra/        gcp_bootstrap.sh
tests/        smoke tests (run in CI on every PR)
```

## Working agreement

- **Never commit** secrets (`.env`, `*-key.json`) or data. Both are gitignored.
- **Branch + PR** for every change; `main` is protected. PR review is how we learn.
- Anything that must run reproducibly lives in `h5n1/`, not in a notebook.
