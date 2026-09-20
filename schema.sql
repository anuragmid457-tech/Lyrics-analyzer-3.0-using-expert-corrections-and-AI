-- schema.sql  (PostgreSQL / Supabase)
--
--   analyses     every reading the system has produced, model or corrected
--   corrections  what an expert changed, why, and who they are
--   preferences  key/value store: learned mode, expert filter, standings
--
-- A correction points at the analysis it corrected, so the pair (what the
-- model said, what the expert said instead) is always recoverable. That pair
-- is the training signal; the corrected output alone is not.

CREATE TABLE IF NOT EXISTS analyses (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    excerpt      TEXT NOT NULL,
    input_text   TEXT NOT NULL,
    output       TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'model',
    learned_from TEXT
);

CREATE INDEX IF NOT EXISTS analyses_fingerprint ON analyses (fingerprint);
CREATE INDEX IF NOT EXISTS analyses_created     ON analyses (created_at DESC);


CREATE TABLE IF NOT EXISTS corrections (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TEXT   NOT NULL,
    analysis_id   BIGINT NOT NULL
                  REFERENCES analyses (id) ON DELETE CASCADE,
    fingerprint   TEXT   NOT NULL,
    excerpt       TEXT   NOT NULL,
    original      TEXT   NOT NULL,
    corrected     TEXT   NOT NULL,
    changed       TEXT   NOT NULL,

    -- who made this call. Rows saved before names were required are NULL and
    -- are reported as 'Unattributed'.
    editor        TEXT,

    -- the reviewer's reasoning, the most valuable column in the database:
    -- a changed number teaches far less than the sentence saying why.
    note          TEXT,

    -- embedding of the corrected song's input text, JSON array of floats
    embedding     TEXT,

    active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS corrections_active      ON corrections (active, created_at DESC);
CREATE INDEX IF NOT EXISTS corrections_fingerprint ON corrections (fingerprint, active);
CREATE INDEX IF NOT EXISTS corrections_editor      ON corrections (editor, active);


CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);