"""Database access — one way in for the whole codebase.

Everything connects to Postgres through a SQLAlchemy engine built from environment
variables. In development and in Modal jobs you reach Cloud SQL through the Cloud
SQL Auth Proxy, which exposes the instance on 127.0.0.1:5432 — so DB_HOST/DB_PORT
point at the proxy and no code needs GCP credentials beyond starting it.

    from h5n1.db import get_engine, check_connection
    print(check_connection())          # smoke test
    df = pd.read_sql("SELECT * FROM dim_county LIMIT 5", get_engine())
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()


def _url() -> str:
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    name = os.environ["DB_NAME"]
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a cached SQLAlchemy engine. pool_pre_ping survives idle drops."""
    return create_engine(_url(), pool_pre_ping=True, future=True)


def check_connection() -> str:
    """Connect and return the server version string. Raises if it can't connect."""
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT version()")).scalar_one()
