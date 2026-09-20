"""
migrate_to_postgres.py - move an existing lyriq.db into Supabase. Run once.

    python migrate_to_postgres.py

Reads the SQLite file (default lyriq.db beside this script) and copies every
analysis, correction and preference into the Postgres database named by
DATABASE_URL. Ids are preserved, so each correction still points at the
reading it corrected, and the sequences are reset afterwards so new rows do
not collide.

Safe to re-run: rows whose id is already present are skipped rather than
duplicated. Nothing is written to the SQLite file, and nothing is deleted
from it, so the original stays as a backup.
"""

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import database  # noqa: E402  - needs DATABASE_URL loaded first

SQLITE_PATH = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "lyriq.db")

TABLES = {
    "analyses": [
        "id", "created_at", "fingerprint", "excerpt",
        "input_text", "output", "source", "learned_from",
    ],
    "corrections": [
        "id", "created_at", "analysis_id", "fingerprint", "excerpt",
        "original", "corrected", "changed", "editor", "note",
        "embedding", "active",
    ],
    "preferences": ["key", "value", "updated_at"],
}


def read_sqlite(path):
    if not Path(path).exists():
        raise SystemExit(f"No SQLite file at {path}. Nothing to migrate.")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {}
    for table, columns in TABLES.items():
        try:
            rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            rows = []          # table absent in an older file
        out[table] = [dict(row) for row in rows]
    conn.close()
    return out


def copy(table, columns, rows, conn):
    """Insert rows, skipping any whose primary key is already there."""
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    conflict = "key" if table == "preferences" else "id"
    written = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                f"""INSERT INTO {table} ({', '.join(columns)})
                    VALUES ({placeholders})
                    ON CONFLICT ({conflict}) DO NOTHING""",
                [row[column] for column in columns],
            )
            written += cur.rowcount
    return written


def resync_sequences(conn):
    """Point each BIGSERIAL at the highest id present, so inserts continue cleanly."""
    with conn.cursor() as cur:
        for table in ("analyses", "corrections"):
            cur.execute(
                f"""SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                                  COALESCE((SELECT MAX(id) FROM {table}), 1))"""
            )


def main():
    print(f"Reading  {SQLITE_PATH}")
    data = read_sqlite(SQLITE_PATH)
    for table, rows in data.items():
        print(f"  {table:12} {len(rows)} row(s)")

    print("\nCreating tables in Postgres if needed…")
    database.init()

    with database.connect() as conn:
        # Analyses first: corrections reference them.
        for table in ("analyses", "corrections", "preferences"):
            written = copy(table, TABLES[table], data[table], conn)
            print(f"  {table:12} {written} written, "
                  f"{len(data[table]) - written} already present")
        resync_sequences(conn)
        conn.commit()

    print("\nNow in Postgres:", database.stats())


if __name__ == "__main__":
    main()