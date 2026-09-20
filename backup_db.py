"""
backup_db.py - pull the hosted database down to a file on this machine.

    python backup_db.py

Writes a dated SQLite copy beside this script, e.g. lyriq-backup-2026-09-20.db.
That file opens in any SQLite browser and can be read by the migration script,
so a backup is also a way back if you ever need one.

The point is separation: Supabase holds the live database so corrections
survive redeploys, and this gives you your own copy that no service can wipe.
Run it after a correction session.
"""

import sqlite3
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import database  # noqa: E402  - needs DATABASE_URL loaded first

HERE = Path(__file__).parent

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY, created_at TEXT, fingerprint TEXT, excerpt TEXT,
    input_text TEXT, output TEXT, source TEXT, learned_from TEXT);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY, created_at TEXT, analysis_id INTEGER,
    fingerprint TEXT, excerpt TEXT, original TEXT, corrected TEXT,
    changed TEXT, editor TEXT, note TEXT, embedding TEXT, active INTEGER);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
"""

TABLES = {
    "analyses": ["id", "created_at", "fingerprint", "excerpt",
                 "input_text", "output", "source", "learned_from"],
    "corrections": ["id", "created_at", "analysis_id", "fingerprint", "excerpt",
                    "original", "corrected", "changed", "editor", "note",
                    "embedding", "active"],
    "preferences": ["key", "value", "updated_at"],
}


def main():
    target = HERE / f"lyriq-backup-{date.today().isoformat()}.db"
    if target.exists():
        target.unlink()

    out = sqlite3.connect(target)
    out.executescript(SQLITE_SCHEMA)

    counts = {}
    with database.connect() as conn:
        for table, columns in TABLES.items():
            with conn.cursor() as cur:
                cur.execute(f"SELECT {', '.join(columns)} FROM {table}")
                rows = cur.fetchall()
            placeholders = ", ".join("?" * len(columns))
            out.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                [[row[column] for column in columns] for row in rows],
            )
            counts[table] = len(rows)

    out.commit()
    out.close()

    print(f"Saved {target.name}")
    for table, count in counts.items():
        print(f"  {table:12} {count} row(s)")


if __name__ == "__main__":
    main()