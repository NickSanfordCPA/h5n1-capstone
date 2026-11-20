"""Connections for the journal rebuild.

Two databases, two roles:

* SOURCE  - the shared Cloud SQL database through the proxy on port 5433. Read-only.
            Every session is opened with default_transaction_read_only = on.
* LOCAL   - h5n1_journal on the local Postgres, port 5432. The only place we write.

`local_engine()` refuses to return an engine that is not on port 5432 / h5n1_journal,
so no script built on it can write to the shared database by accident.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine

REPO = Path(__file__).resolve().parents[2]
LOCAL_HOST = "localhost"          # pgpass.conf is keyed to "localhost", not 127.0.0.1
LOCAL_PORT = 5432
LOCAL_DB = "h5n1_journal"
LOCAL_USER = "postgres"
SOURCE_PORT = 5433
PG_BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")
DATA_DIR = REPO / "data" / "journal"


def source_settings() -> dict:
    """Shared-DB settings from .env (never from the process environment)."""
    env = dotenv_values(REPO / ".env")
    port = int(env.get("DB_PORT", 0))
    if port != SOURCE_PORT:
        raise RuntimeError(f".env DB_PORT is {port}, expected the proxy on {SOURCE_PORT}")
    return {"host": env.get("DB_HOST", "127.0.0.1"), "port": port, "db": env["DB_NAME"],
            "user": env["DB_USER"], "password": env["DB_PASSWORD"]}


@lru_cache(maxsize=1)
def source_engine() -> Engine:
    s = source_settings()
    url = URL.create("postgresql+psycopg", username=s["user"], password=s["password"],
                     host=s["host"], port=s["port"], database=s["db"])
    eng = create_engine(url, pool_pre_ping=True, future=True)

    @event.listens_for(eng, "connect")
    def _read_only(dbapi_conn, _):
        with dbapi_conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
        dbapi_conn.commit()

    return eng


def assert_local(engine: Engine) -> None:
    """Abort unless the engine points at the local journal database."""
    u = engine.url
    if u.port != LOCAL_PORT or u.database != LOCAL_DB or u.port == SOURCE_PORT:
        raise RuntimeError(f"refusing to write: {u.host}:{u.port}/{u.database} is not the local "
                           f"journal database ({LOCAL_PORT}/{LOCAL_DB})")


@lru_cache(maxsize=1)
def local_engine() -> Engine:
    """Engine for h5n1_journal on 5432. Password comes from pgpass.conf."""
    url = URL.create("postgresql+psycopg", username=LOCAL_USER, host=LOCAL_HOST,
                     port=LOCAL_PORT, database=LOCAL_DB)
    eng = create_engine(url, pool_pre_ping=True, future=True)
    assert_local(eng)
    return eng


def point_h5n1_db_at_local() -> None:
    """Make h5n1.db.get_engine() (used by the existing loaders) hit the local database.

    Call before importing anything that calls get_engine(). Sets the process environment
    only; .env is left unchanged (load_dotenv does not override existing variables).
    """
    import psycopg  # noqa: F401  (pgpass lookup needs an explicit password for h5n1.db's URL)

    pw = _pgpass_password()
    os.environ.update({"DB_HOST": LOCAL_HOST, "DB_PORT": str(LOCAL_PORT), "DB_NAME": LOCAL_DB,
                       "DB_USER": LOCAL_USER, "DB_PASSWORD": pw})
    from h5n1 import db
    db.get_engine.cache_clear()
    assert_local(db.get_engine())


def _pgpass_password() -> str:
    path = Path(os.environ["APPDATA"]) / "postgresql" / "pgpass.conf"
    for line in path.read_text().splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 5 and parts[0] == LOCAL_HOST and parts[1] in ("*", str(LOCAL_PORT)) \
                and parts[3] == LOCAL_USER:
            return ":".join(parts[4:])
    raise RuntimeError("no pgpass.conf entry for the local postgres user")
