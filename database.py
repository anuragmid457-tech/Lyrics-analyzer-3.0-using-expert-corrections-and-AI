"""
database.py - the only module that talks to PostgreSQL.

Nothing here knows about Flask, prompts or embeddings. It stores rows and
returns dicts. learning.py decides what the rows mean; review.py decides who
is allowed to write them. Because every query lives in this one file, moving
from SQLite to Postgres touched nothing else in the project.

The database lives with Supabase rather than on the machine running the app,
so corrections survive a redeploy, a restart, and the free tier wiping its
disk. Local runs and the hosted site point at the same one.

    DATABASE_URL must be set, e.g. in .env:
      DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@host.supabase.com:5432/postgres

    init()                              create the tables, safe every boot
    save_analysis(...)      -> id       record a reading
    get_analysis(id)        -> dict
    save_correction(...)    -> id       record what an expert changed
    active_corrections(...) -> list     rows still teaching, optionally by expert
    correction_for(fp, ...) -> dict     newest correction for identical input
    experts()               -> list     who has corrected, and how much
    retire_correction(id)               stop one teaching, keep the record
    get_preference / set_preference
    stats()                 -> dict     counts for the review panel
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).parent
SCHEMA_PATH = HERE / "schema.sql"

# Corrections saved before names were required show up under this.
UNATTRIBUTED = "Unattributed"


def _dsn():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Put your Supabase connection string in .env "
            "locally, and in the Environment tab on your host."
        )
    # Supabase requires TLS; say so explicitly rather than relying on defaults.
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


# --- plumbing ------------------------------------------------------------

def connect():
    """One connection per call. Simple, and fine at this traffic."""
    return psycopg.connect(_dsn(), row_factory=dict_row)


def init():
    """Create the tables if they are not there. Idempotent."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _one(sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def _all(sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _run(sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone() if cur.description else None
        conn.commit()
        return row


def _json(value, fallback=None):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def fingerprint(text: str) -> str:
    """Hash of the input with case and whitespace flattened."""
    flat = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return hashlib.sha256(flat.encode("utf-8")).hexdigest()


def excerpt(text: str, limit: int = 120) -> str:
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _editor_clause(editors):
    """SQL fragment restricting rows to a list of expert names."""
    if not editors:
        return "", []

    names = [str(name).strip() for name in editors if str(name).strip()]
    if not names:
        return "", []

    parts, params = [], []
    if UNATTRIBUTED in names:
        parts.append("(editor IS NULL OR TRIM(editor) = '')")
        names = [n for n in names if n != UNATTRIBUTED]
    if names:
        parts.append("TRIM(editor) IN (%s)" % ",".join(["%s"] * len(names)))
        params.extend(names)

    return " AND (" + " OR ".join(parts) + ")", params


# --- analyses ------------------------------------------------------------

def save_analysis(input_text, output, source="model", learned_from=None):
    """Record one reading and return its id."""
    row = _run(
        """INSERT INTO analyses
               (created_at, fingerprint, excerpt, input_text,
                output, source, learned_from)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            _now(),
            fingerprint(input_text),
            excerpt(input_text),
            input_text,
            json.dumps(output, ensure_ascii=False),
            source,
            json.dumps(learned_from, ensure_ascii=False) if learned_from else None,
        ),
    )
    return row["id"]


def get_analysis(analysis_id):
    record = _one("SELECT * FROM analyses WHERE id = %s", (analysis_id,))
    if record is None:
        return None
    record = dict(record)
    record["output"] = _json(record["output"], {})
    record["learned_from"] = _json(record["learned_from"], [])
    return record


def recent_analyses(limit=20):
    rows = _all(
        """SELECT a.id, a.created_at, a.excerpt, a.source,
                  (SELECT COUNT(*) FROM corrections c
                    WHERE c.analysis_id = a.id AND c.active = 1) AS corrections
             FROM analyses a
         ORDER BY a.created_at DESC, a.id DESC
            LIMIT %s""",
        (limit,),
    )
    return [dict(row) for row in rows]


# --- corrections ---------------------------------------------------------

def save_correction(analysis_id, original, corrected, changed,
                    editor=None, note=None, embedding=None):
    """Record an expert edit against the analysis it corrects."""
    analysis = get_analysis(analysis_id)
    if analysis is None:
        raise ValueError(f"No analysis with id {analysis_id}.")

    row = _run(
        """INSERT INTO corrections
               (created_at, analysis_id, fingerprint, excerpt,
                original, corrected, changed, editor, note, embedding, active)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
           RETURNING id""",
        (
            _now(),
            analysis_id,
            analysis["fingerprint"],
            analysis["excerpt"],
            json.dumps(original, ensure_ascii=False),
            json.dumps(corrected, ensure_ascii=False),
            json.dumps(changed, ensure_ascii=False),
            (editor or "").strip() or None,
            (note or "").strip() or None,
            json.dumps(embedding) if embedding else None,
        ),
    )
    return row["id"]


def _hydrate_correction(record, with_vector=False):
    record = dict(record)
    record["original"] = _json(record.get("original"), {})
    record["corrected"] = _json(record.get("corrected"), {})
    record["changed"] = _json(record.get("changed"), {})
    record["editor"] = (record.get("editor") or "").strip() or UNATTRIBUTED
    vector = _json(record.pop("embedding", None), None)
    if with_vector:
        record["embedding"] = vector
    record["has_embedding"] = bool(vector)
    return record


def active_corrections(with_vector=True, limit=400, editors=None):
    """Every correction still allowed to teach, newest first."""
    clause, params = _editor_clause(editors)
    rows = _all(
        f"""SELECT * FROM corrections
             WHERE active = 1{clause}
          ORDER BY created_at DESC, id DESC
             LIMIT %s""",
        params + [limit],
    )
    return [_hydrate_correction(row, with_vector) for row in rows]


def list_corrections(limit=50, include_retired=True, editors=None):
    """For the review log. Vectors stripped, they are noise on screen."""
    clause, params = _editor_clause(editors)
    where = "WHERE TRUE" if include_retired else "WHERE active = 1"
    rows = _all(
        f"""SELECT id, created_at, analysis_id, excerpt, changed,
                   editor, note, active
              FROM corrections {where}{clause}
          ORDER BY created_at DESC, id DESC
             LIMIT %s""",
        params + [limit],
    )
    out = []
    for row in rows:
        record = dict(row)
        record["changed"] = _json(record["changed"], {})
        record["editor"] = (record.get("editor") or "").strip() or UNATTRIBUTED
        out.append(record)
    return out


def get_correction(correction_id, with_vector=False):
    record = _one("SELECT * FROM corrections WHERE id = %s", (correction_id,))
    return _hydrate_correction(record, with_vector) if record else None


def correction_for(text, editors=None):
    """Newest active correction for an input identical to this one."""
    clause, params = _editor_clause(editors)
    record = _one(
        f"""SELECT * FROM corrections
             WHERE fingerprint = %s AND active = 1{clause}
          ORDER BY created_at DESC, id DESC
             LIMIT 1""",
        [fingerprint(text)] + params,
    )
    return _hydrate_correction(record) if record else None


def retire_correction(correction_id, active=False):
    """Stop a correction teaching, or put it back. The row is never deleted."""
    _run("UPDATE corrections SET active = %s WHERE id = %s",
         (1 if active else 0, correction_id))
    return get_correction(correction_id)


# --- experts -------------------------------------------------------------

def experts():
    """Who has corrected readings, and how much of it still teaches.

    The COALESCE runs in a subquery rather than the GROUP BY, because
    Postgres will not group by an expression containing a bind parameter.
    """
    rows = _all(
        """SELECT name,
                  COUNT(*)                 AS corrections,
                  COALESCE(SUM(active), 0) AS teaching,
                  MAX(created_at)          AS last_edit
             FROM (SELECT COALESCE(NULLIF(TRIM(editor), ''), %s) AS name,
                          active, created_at
                     FROM corrections) AS named
         GROUP BY name
         ORDER BY teaching DESC, corrections DESC, name ASC""",
        (UNATTRIBUTED,),
    )
    return [dict(row) for row in rows]

# --- preferences ---------------------------------------------------------

def get_preference(key, fallback=None):
    record = _one("SELECT value FROM preferences WHERE key = %s", (key,))
    return record["value"] if record else fallback


def set_preference(key, value):
    _run(
        """INSERT INTO preferences (key, value, updated_at)
           VALUES (%s, %s, %s)
           ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = EXCLUDED.updated_at""",
        (key, str(value), _now()),
    )
    return get_preference(key)


def get_json_preference(key, fallback=None):
    return _json(get_preference(key), fallback if fallback is not None else [])


def set_json_preference(key, value):
    return set_preference(key, json.dumps(value, ensure_ascii=False))


# --- summary -------------------------------------------------------------

def stats():
    row = _one(
        """SELECT
             (SELECT COUNT(*) FROM analyses)                     AS analyses,
             (SELECT COUNT(*) FROM corrections)                  AS corrections,
             (SELECT COUNT(*) FROM corrections WHERE active = 1) AS teaching,
             (SELECT COUNT(*) FROM corrections
               WHERE active = 1 AND embedding IS NOT NULL)       AS embedded,
             (SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(editor), ''), %s))
                FROM corrections)                                AS experts,
             (SELECT MAX(created_at) FROM corrections)           AS last_edit""",
        (UNATTRIBUTED,),
    )
    return dict(row)