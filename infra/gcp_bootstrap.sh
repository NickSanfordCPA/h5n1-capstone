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
# NOTE: PROJECT_ID is the immutable project id, NOT the display name. The display
# name is "h5n1-capstone"; the id was set when the project was created and cannot
# be changed. Always use the id below in commands and connection strings.
PROJECT_ID="harvard-capstone-499102"
BILLING_ACCOUNT="016681-D7C2E5-526A9D"        # "Billing Account for Education"
REGION="us-central1"
DB_INSTANCE="h5n1-pg"
DB_NAME="h5n1"
DB_APP_USER="h5n1_app"
# Bucket names are global and deliberately NOT derived from PROJECT_ID.
# The raw/social split is a security boundary: social data is PII-restricted and
# gets narrower IAM than raw source files. Do not collapse these into one bucket.
RAW_BUCKET="gs://h5n1-raw"
SOCIAL_BUCKET="gs://h5n1-social-restricted"
SA_NAME="h5n1-jobs"                            # service account for non-human access
TEAM_EMAILS=("sht310@g.harvard.edu" "wap185@g.harvard.edu")   # Max, Waree

# ---------------------------------------------------------------------------
# 2. Project, billing, budget, APIs
# ---------------------------------------------------------------------------
# ALREADY DONE — the project exists and billing is linked. Kept for the record:
#   gcloud projects create "$PROJECT_ID"
#   gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
gcloud config set project "$PROJECT_ID"

gcloud services enable \
  compute.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  billingbudgets.googleapis.com

# Budget alerts at 50/90/100% of $40. Sized under the ~$49 of education credits so
# the alert fires BEFORE they run out, not after. A budget only NOTIFIES — it does
# not cap spend. Credits expire 2026-09-02 regardless; relink billing before then:
#   gcloud billing projects link "$PROJECT_ID" --billing-account=<new-id>
# --credit-types-treatment=exclude-all-credits IS LOAD-BEARING. The default is
# INCLUDE_ALL_CREDITS, which measures spend NET of credits — on a fully-credited
# account that reads $0 forever and the thresholds NEVER fire. Excluding credits
# makes the budget track gross spend, which is what "how fast are we burning the
# grant?" actually means.
# Also needs --billing-project, else the call is attributed to a shared Google SDK
# project and fails SERVICE_DISABLED even when the API is enabled on ours.
gcloud billing budgets create \
  --billing-account="$BILLING_ACCOUNT" \
  --display-name="h5n1-monthly" \
  --budget-amount=40USD \
  --credit-types-treatment=exclude-all-credits \
  --billing-project="$PROJECT_ID" \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0

# ---------------------------------------------------------------------------
# 3. IAM — teammates + a service account for jobs
# ---------------------------------------------------------------------------
# Least privilege: teammates get exactly what they need — connect to Cloud SQL and
# read/write buckets. Deliberately NOT roles/editor, which would let them delete the
# SQL instance and the buckets. Credits here are finite and non-replaceable, so an
# accidental delete is expensive. Add roles later if someone is actually blocked.
for EMAIL in "${TEAM_EMAILS[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="user:${EMAIL}" --role="roles/cloudsql.client"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="user:${EMAIL}" --role="roles/storage.objectAdmin"
  # Lets them see the instance in the console / run `gcloud sql instances list`.
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="user:${EMAIL}" --role="roles/cloudsql.viewer"
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
# --edition=ENTERPRISE IS REQUIRED AND MUST NOT BE DROPPED.
# Cloud SQL defaults new Postgres instances to ENTERPRISE_PLUS, which rejects all
# shared-core tiers and only accepts db-perf-optimized-N-* — hundreds of dollars a
# month. Without this flag the create fails; worse, "fixing" it by switching to the
# tier the error message suggests silently buys the expensive edition.
# Note `gcloud sql tiers list` lists db-g1-small regardless of edition, so it is NOT
# a reliable check for whether a tier is usable here.
#
# STOP the instance when idle to cut cost:
#   gcloud sql instances patch $DB_INSTANCE --activation-policy=NEVER
# and start it again with --activation-policy=ALWAYS
gcloud sql instances create "$DB_INSTANCE" \
  --database-version=POSTGRES_16 \
  --region="$REGION" \
  --edition=ENTERPRISE \
  --tier=db-g1-small \
  --storage-size=10GB \
  --storage-auto-increase

gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE"

# App user. NOTE: there is NO --prompt-for-password flag on `gcloud sql users
# create` — only --password=PASSWORD. Typing it inline puts the secret in shell
# history, so read it into a variable first. Store it in the team password manager;
# roles/cloudsql.client gets teammates a connection, NOT credentials.
#   bash:       read -rs -p "password: " PW && echo
#   PowerShell: $sec = Read-Host "password" -AsSecureString
#               $plain = [System.Net.NetworkCredential]::new("", $sec).Password
gcloud sql users create "$DB_APP_USER" --instance="$DB_INSTANCE" --password="$PW"
unset PW

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
