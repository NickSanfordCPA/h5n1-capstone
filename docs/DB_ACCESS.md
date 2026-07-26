# Connecting to the H5N1 Postgres database

The warehouse is **Cloud SQL Postgres** (`h5n1-pg`), reachable only through an
IAM-authenticated tunnel — the public IP has **no authorized networks**, so there is
no "just point psql at the host" path, by design. See
[ARCHITECTURE_SKETCH.md](../ARCHITECTURE_SKETCH.md) for the why.

There is a runnable companion to this doc: [`notebooks/connect_example.ipynb`](../notebooks/connect_example.ipynb).

## Two auth layers (read this first)

Getting in is **two separate gates**, not one:

1. **The tunnel — GCP identity.** The Cloud SQL Auth Proxy (or the Python Connector)
   only opens if your Google identity has `cloudsql.client`. Max and Waree already
   have it (`cloudsql.client` + `viewer` + `editor` at project level). Nothing to do.
2. **The database login.** Once the tunnel is open, Postgres still wants its own
   credentials. Two options:
   - **Shared password** (current default): user `h5n1_app` + its password.
   - **IAM database authentication** (preferred, no shared secret): you log in as your
     own `@g.harvard.edu`, password is a short-lived OAuth token. See
     [IAM database authentication](#iam-database-authentication-no-shared-password)
     below. Requires one-time instance setup.

   IAM authorizes the *pipe* either way; this second gate authenticates the *session*.

Get the `h5n1_app` password from the **team password manager** — never git or Slack.

## Facts you'll need

| | |
|---|---|
| Instance connection name | `harvard-capstone-499102:us-central1:h5n1-pg` |
| Project | `harvard-capstone-499102` |
| Database | `h5n1` |
| DB user | `h5n1_app` |
| Region | `us-central1` |

---

## One-time setup (local machine)

```bash
# 1. Install the Google Cloud SDK, then:
gcloud auth login                          # your @g.harvard.edu account
gcloud config set project harvard-capstone-499102

# 2. Application Default Credentials — SEPARATE from the line above.
#    A working `gcloud` CLI does NOT mean a working proxy; the proxy needs THIS.
gcloud auth application-default login

# 3. Download the Cloud SQL Auth Proxy v2.x binary for your OS:
#    https://cloud.google.com/sql/docs/postgres/sql-proxy#install
#    (gitignored — do not commit it)
```

Then copy `.env.example` to `.env` and fill in the `DB_*` and `GCP_*`/`CLOUD_SQL_INSTANCE`
values from the password manager.

---

## Local: proxy + notebook / script

**Terminal A — start the proxy and leave it running:**

```bash
cloud-sql-proxy harvard-capstone-499102:us-central1:h5n1-pg --port 5432
```

⚠️ **Port clash.** If you already run Postgres locally it owns 5432, and the proxy
fails on Windows with `socket ... forbidden by its access permissions` (not a clear
"address in use"). Pick a free port — e.g. `--port 5433` — and set `DB_PORT` in `.env`
to **the same number**. (This is why Nick uses 5433.)

**Terminal B — connect through the library:**

```python
from h5n1.db import get_engine, check_connection
print(check_connection())                 # -> "PostgreSQL 18.4 ..." smoke test

import pandas as pd
pd.read_sql("SELECT COUNT(*) FROM fact_h5n1_outbreak", get_engine())
```

Always go through `h5n1.db.get_engine()` — it reads `.env`, so your port choice lives
in one place and no code hardcodes it. Launch Jupyter **from the project environment**
(`uv run --extra dev jupyter lab`, or the Docker image) so the kernel has `h5n1` and the
pinned deps. Running a system-wide `jupyter` gives a kernel that can't `import h5n1`.

---

## Visual browsing (pgAdmin / DBeaver / TablePlus)

To click through tables instead of writing SQL, point a **local** GUI client at the
proxy — **not** at the instance's public IP. Start the proxy exactly as above, then in
the client create a Postgres connection to:

| Field | Value |
|---|---|
| Host | `127.0.0.1` |
| Port | `5432` (or `5433` if you moved it) |
| Database | `h5n1` |
| User / password | `h5n1_app` + password (or your IAM login — see below) |

The GUI talks plain Postgres to localhost; the proxy carries the IAM-authenticated
tunnel. This needs **no** change to authorized networks. pgAdmin renders in a browser
tab but is still a local app tunneling through the proxy — that's fine. **Do not** add
your IP to the instance's authorized networks to reach it from the browser directly;
that empty list is the main control protecting the database.

## Browsing in the Google Cloud Console

In Chrome at [console.cloud.google.com/sql](https://console.cloud.google.com/sql/instances/h5n1-pg/overview?project=harvard-capstone-499102)
you can view the instance config, metrics, backups, and users. The in-console
**Cloud SQL Studio** query editor, however, connects over the public IP — which has no
authorized networks — so it won't connect without loosening that control. Use the proxy
path above for querying; use the console only for admin/monitoring.

---

## Google Colab

Colab is a VM in Google's cloud — `localhost` and a locally-run proxy don't exist there.
Use the **Cloud SQL Python Connector**: no proxy binary, authenticates via ADC, and it
goes through the same IAM tunnel, so **you do NOT open authorized networks**.

```python
!pip install "cloud-sql-python-connector[pg8000]" sqlalchemy pandas

from google.colab import auth
auth.authenticate_user()   # authenticates the Google account SIGNED INTO COLAB.
                           # It must be your @g.harvard.edu (the one with cloudsql.client),
                           # not a personal Gmail — otherwise connect fails with a
                           # permission error, not an auth prompt.

import sqlalchemy
from google.cloud.sql.connector import Connector

connector = Connector()

def _getconn():
    return connector.connect(
        "harvard-capstone-499102:us-central1:h5n1-pg",
        "pg8000",
        user="h5n1_app",
        password="...",        # h5n1_app password (paste at runtime; don't commit)
        db="h5n1",
    )

engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=_getconn)

import pandas as pd
pd.read_sql("SELECT COUNT(*) FROM fact_weather", engine)
```

**Do not** upload a service-account key JSON into Colab. We deliberately have not
minted the `h5n1-jobs` key, and a key pasted into a notebook is the most leakable
artifact in the project. `auth.authenticate_user()` needs no key. Likewise, don't add
Colab's IP to authorized networks — those IPs are ephemeral and it would punch a hole
in the one control protecting the database.

---

## IAM database authentication (no shared password)

The alternative to distributing the `h5n1_app` password: log in as your own Google
identity, with a short-lived OAuth token in place of a password. Kills the shared
secret entirely.

**One-time instance setup (Nick):**

```bash
# 1. Enable the feature flag (restarts the instance — brief blip).
#    --database-flags REPLACES the whole set; list existing flags first and include
#    them all, or they're dropped:
#    gcloud sql instances describe h5n1-pg --format="value(settings.databaseFlags)"
gcloud sql instances patch h5n1-pg --database-flags=cloudsql.iam_authentication=on

# 2. Grant each teammate the login role (they already have cloudsql.client).
for u in sht310@g.harvard.edu wap185@g.harvard.edu; do
  gcloud projects add-iam-policy-binding harvard-capstone-499102 \
    --member="user:$u" --role="roles/cloudsql.instanceUser"
done

# 3. Register each as an IAM DB user.
gcloud sql users create sht310@g.harvard.edu --instance=h5n1-pg --type=cloud_iam_user
gcloud sql users create wap185@g.harvard.edu --instance=h5n1-pg --type=cloud_iam_user
```

**Then GRANT Postgres privileges** (connected as `postgres` — IAM handles *authn*, not
*authz*; a new IAM user has no table rights):

```sql
GRANT USAGE ON SCHEMA public TO "sht310@g.harvard.edu";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "sht310@g.harvard.edu";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO "sht310@g.harvard.edu";   -- future tables too
-- repeat for wap185@g.harvard.edu; add INSERT/UPDATE where write access is needed
```

**Connecting as an IAM user:**

- Username is your **full email** (`sht310@g.harvard.edu`) — hence the quotes above.
- No password. The Cloud SQL Python Connector handles the token: pass
  `enable_iam_auth=True` and drop `password=` entirely.

```python
connector.connect(
    "harvard-capstone-499102:us-central1:h5n1-pg",
    "pg8000",
    user="sht310@g.harvard.edu",
    db="h5n1",
    enable_iam_auth=True,          # token instead of a password
)
```

Through the **proxy** (local GUI or `h5n1.db`), add `--auto-iam-authn` to the proxy
command; connect with your email as the user and any/empty password.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `socket ... forbidden by its access permissions` (Windows) | Port clash — local Postgres owns 5432. Use `--port 5433` and match `DB_PORT`. |
| Proxy: `failed to refresh token` / permission denied | ADC missing or stale. Run `gcloud auth application-default login`. |
| Colab: `403 ... cloudsql.instances.connect` | Colab signed into the wrong Google account. Use the `@g.harvard.edu` with `cloudsql.client`. |
| `password authentication failed for user "h5n1_app"` | Tunnel is fine; wrong DB password. Re-check the password manager. |
| `KeyError: 'DB_USER'` (or similar) | `.env` not filled in / not loaded. Copy from `.env.example`. |
