"""Apply SQL migrations in sql/ in filename order, once each, idempotently.

Applied files are tracked in a schema_migrations table, so re-running is safe:
already-applied files are skipped. This is what makes the schema reproducible —
DDL is versioned code, not console typing.

Usage (Cloud SQL Auth Proxy running, or against a local Postgres):
    python sql/run_migrations.py
"""
from __future__ import annotations

import pathlib

from sqlalchemy import text

from h5n1.db import get_engine

SQL_DIR = pathlib.Path(__file__).parent


def _ensure_tracking(conn) -> set[str]:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    return set(conn.execute(text("SELECT filename FROM schema_migrations")).scalars())


def main() -> None:
    engine = get_engine()
    files = sorted(SQL_DIR.glob("*.sql"))
    with engine.begin() as conn:
        done = _ensure_tracking(conn)
        for path in files:
            if path.name in done:
                print(f"skip  {path.name}")
                continue
            print(f"apply {path.name}")
            conn.exec_driver_sql(path.read_text())  # psycopg runs multi-statement files
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                {"f": path.name},
            )
    print("migrations complete")


if __name__ == "__main__":
    main()
