#!/usr/bin/env bash
# GCP bootstrap for the H5N1 capstone — items 2-6 of the infra plan.
#
# RUN INTERACTIVELY, block by block — do NOT pipe it blindly. gcloud flags change
# over time and a couple of steps prompt (billing link, DB password). Set the
# variables, run a block, read the output, then run the next.
#
# Prereqs: install the gcloud CLI and run `gcloud auth login` first.
set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Variables — EDIT THESE
# ---------------------------------------------------------------------------
PROJECT_ID="h5n1-capstone"                    # must be globally unique; add a suffix if taken
BILLING_ACCOUNT="XXXXXX-XXXXXX-XXXXXX"        # see: gcloud billing accounts list
REGION="us-central1"
DB_INSTANCE="h5n1-pg"
DB_NAME="h5n1"
DB_APP_USER="h5n1_app"
RAW_BUCKET="gs://${PROJECT_ID}-raw"
SOCIAL_BUCKET="gs://${PROJECT_ID}-social-restricted"
SA_NAME="h5n1-jobs"                            # service account for non-human access
TEAM_EMAILS=("waree@example.com" "max@example.com")   # their Google accounts

# ---------------------------------------------------------------------------
# 2. Project, billing, budget, APIs
# ---------------------------------------------------------------------------
gcloud projects create "$PROJECT_ID"
gcloud config set project "$PROJECT_ID"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"

gcloud services enable \
  compute.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  billingbudgets.googleapis.com

# Budget with alert emails at 50/90/100% of $100/month. Adjust the amount.
gcloud billing budgets create \
  --billing-account="$BILLING_ACCOUNT" \
  --display-name="h5n1-monthly" \
  --budget-amount=100USD \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0

# ---------------------------------------------------------------------------
# 3. IAM — teammates + a service account for jobs
# ---------------------------------------------------------------------------
for EMAIL in "${TEAM_EMAILS[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="user:${EMAIL}" --role="roles/editor"
done

gcloud iam service-accounts create "$SA_NAME" \
  --display-name="H5N1 batch jobs (Modal, migrations)"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/cloudsql.client"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/storage.objectAdmin"

# ---------------------------------------------------------------------------
# 4. GCS buckets — raw (versioned) + social (restricted)
# ---------------------------------------------------------------------------
gcloud storage buckets create "$RAW_BUCKET"    --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets create "$SOCIAL_BUCKET" --location="$REGION" --uniform-bucket-level-access
# Object versioning on raw = recover any overwrite/delete (immutability safety net).
gcloud storage buckets update "$RAW_BUCKET" --versioning

# ---------------------------------------------------------------------------
# 5. Cloud SQL Postgres
# ---------------------------------------------------------------------------
# NOTE: verify the tier + price for your region before creating. db-g1-small is a
# low-cost shared-core option; if it's rejected, use a custom tier instead:
#   --tier=db-custom-1-3840   (1 vCPU, 3.75 GB)
# You can STOP the instance when idle to cut cost: gcloud sql instances patch $DB_INSTANCE --activation-policy=NEVER
gcloud sql instances create "$DB_INSTANCE" \
  --database-version=POSTGRES_16 \
  --region="$REGION" \
  --tier=db-g1-small \
  --storage-size=10GB \
  --storage-auto-increase

gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE"

# App user — prompts for a password. Store it in the team password manager.
gcloud sql users create "$DB_APP_USER" --instance="$DB_INSTANCE" --prompt-for-password

# The instance connection name (PROJECT:REGION:INSTANCE) — put this in .env as CLOUD_SQL_INSTANCE:
gcloud sql instances describe "$DB_INSTANCE" --format="value(connectionName)"

# ---------------------------------------------------------------------------
# 6. Secrets + connectivity
# ---------------------------------------------------------------------------
# Service-account key for non-human jobs. TREAT AS SECRET — it is gitignored.
# Prefer `gcloud auth application-default login` for your own laptop and only use
# this key for Modal/CI.
gcloud iam service-accounts keys create ./service-account-key.json \
  --iam-account="$SA_EMAIL"

# Then start the Cloud SQL Auth Proxy locally so Postgres is on 127.0.0.1:5432.
# Download: https://cloud.google.com/sql/docs/postgres/sql-proxy  (Windows: cloud-sql-proxy.exe)
#   cloud-sql-proxy "$(gcloud sql instances describe $DB_INSTANCE --format='value(connectionName)')" --port 5432
#
# With the proxy running and .env filled in, verify end to end:
#   python sql/run_migrations.py
#   python -c "from h5n1.db import check_connection; print(check_connection())"
