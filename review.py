"""
review.py - the expert-facing HTTP surface, as a blueprint.

app.py stays the thin layer it was: it registers this and carries on. Every
route here is mounted under /api/review.

    GET    /api/review/analysis/<id>            the reading being edited
    POST   /api/review/unlock                   check the editor password
    POST   /api/review/correction               save an expert edit        (password)
    GET    /api/review/corrections              the review log
    POST   /api/review/correction/<id>/retire   stop one teaching, or restore  (password)
    GET    /api/review/experts                  who has corrected, and standings
    POST   /api/review/experts/ranking          set the top three          (password)
    GET    /api/review/preferences              mode and expert filter
    POST   /api/review/preferences              change them                (password)
    GET    /api/review/stats                    counts for the panel

Reading is open to anyone. Every route that changes something needs the
editor password, sent by the page in an X-Editor-Key header. The password
itself lives only in the EDITOR_PASSWORD environment variable on the server;
it is never written into the page, so inspecting the site cannot reveal it.

A correction must also carry the name of the person who made it. An
unattributed edit teaches the model something without anyone being
answerable for it, and it cannot be filtered or ranked afterwards.
"""

import hmac
import os
import time
from functools import wraps

from flask import Blueprint, jsonify, request

try:                        # filenames differ in case between machines
    import database
    import learning
except ImportError:         # pragma: no cover
    import Database as database
    import Learning as learning

review = Blueprint("review", __name__, url_prefix="/api/review")

EDITABLE = {
    "primary_emotion", "canonical_emotion", "secondary_emotions", "mixed_emotion",
    "valence", "arousal", "quadrant", "emotion_indices", "sections",
    "rasa", "parjaay", "tradition", "language",
    "summary", "confidence",
    "music_therapy", "music_therapy_context", "recommendation_tags",
    "evidence",
}

QUADRANTS = {"Q1", "Q2", "Q3", "Q4"}

# A compound label rarely runs past three or four terms; the cap is only
# there so a malformed submission cannot fill the column.
MAX_TERMS = 8

# A song split into more than this many sections is not a shape anyone can
# read, let alone drag.
MAX_SECTIONS = 40

# Seconds to wait after a wrong password. Harmless to a person who mistyped,
# and it turns a script guessing thousands of passwords into a slow one.
WRONG_PASSWORD_DELAY = 0.8


# --- the editor password -------------------------------------------------

def _editor_password():
    return os.getenv("EDITOR_PASSWORD", "")


def require_editor(view):
    """Allow the request only if it carries the editor password.

    Fails closed: if EDITOR_PASSWORD is not set on the server, nobody can
    edit, rather than everybody. Forgetting the variable should lock the
    site, not open it.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = _editor_password()
        if not expected:
            return jsonify({
                "error": "Editing is switched off: no EDITOR_PASSWORD is set on the server.",
                "locked": True,
            }), 503

        supplied = request.headers.get("X-Editor-Key", "")
        # compare_digest takes the same time whether the first character or
        # the last one is wrong, so response timing leaks nothing.
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            time.sleep(WRONG_PASSWORD_DELAY)
            return jsonify({"error": "Editor password required.", "locked": True}), 401

        return view(*args, **kwargs)
    return wrapped


# --- validation ----------------------------------------------------------

def _clamp(value, low, high, fallback=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:
        return fallback
    return round(max(low, min(high, number)), 2)


def _as_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _names(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _quadrant_from(valence, arousal):
    if valence >= 0:
        return "Q1" if arousal >= 0 else "Q4"
    return "Q2" if arousal >= 0 else "Q3"


def _sections(value):
    """The section-by-section shape, tidied.

    A list of {label, valence, arousal}, one per section of the song, as the
    reviewer dragged it. Stored on the correction rather than in a column of
    its own, so no migration is needed and an older correction simply has no
    shape attached.
    """
    if not isinstance(value, list):
        return []

    out = []
    for item in value[:MAX_SECTIONS]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:60]
        if not label:
            continue
        row = {"label": label}
        for key in ("valence", "arousal"):
            if item.get(key) is None:
                continue
            try:
                number = float(item[key])
            except (TypeError, ValueError):
                continue
            if number != number:
                continue
            row[key] = round(max(-1.0, min(1.0, number)), 2)
        if len(row) > 1:
            out.append(row)
    return out


def _indices(value):
    """Per-term indices as {term: 0.0-1.0}, tidied.

    The browser sends one entry per term of the primary emotion. Anything
    unparseable is dropped rather than stored, so a bad submission cannot
    leave odd values behind for the model to learn from.
    """
    if not isinstance(value, dict):
        return {}
    out = {}
    for term, score in list(value.items())[:MAX_TERMS]:
        name = str(term).strip()
        if not name:
            continue
        try:
            number = float(score)
        except (TypeError, ValueError):
            continue
        if number != number:
            continue
        out[name] = round(max(0.0, min(1.0, number)), 2)
    return out


def clean(submitted, original):
    """Merge the reviewer's edits onto the original verdict, tidied."""
    out = dict(original or {})

    for key, value in (submitted or {}).items():
        if key in EDITABLE:
            out[key] = value

    out["valence"] = _clamp(out.get("valence"), -1.0, 1.0)
    out["arousal"] = _clamp(out.get("arousal"), -1.0, 1.0)
    out["confidence"] = _clamp(out.get("confidence"), 0.0, 1.0)

    sections = _sections(out.get("sections"))
    if sections:
        out["sections"] = sections
    else:
        out.pop("sections", None)

    indices = _indices(out.get("emotion_indices"))
    if indices:
        out["emotion_indices"] = indices
    else:
        out.pop("emotion_indices", None)

    quadrant = str(out.get("quadrant") or "")[:2].upper()
    out["quadrant"] = quadrant if quadrant in QUADRANTS else _quadrant_from(
        out["valence"], out["arousal"]
    )

    out["secondary_emotions"] = _as_list(out.get("secondary_emotions"))
    out["recommendation_tags"] = _as_list(out.get("recommendation_tags"))
    out["mixed_emotion"] = bool(out.get("mixed_emotion"))

    for key in ("primary_emotion", "canonical_emotion", "rasa",
                "parjaay", "tradition", "language"):
        value = out.get(key)
        out[key] = value.strip() or None if isinstance(value, str) else value

    out["edited"] = True
    out.pop("status", None)
    return out


# --- routes --------------------------------------------------------------

@review.post("/unlock")
@require_editor
def unlock():
    """Lets the page check a password before anyone starts editing."""
    return jsonify({"ok": True})


@review.get("/analysis/<int:analysis_id>")
def read_analysis(analysis_id):
    record = database.get_analysis(analysis_id)
    if record is None:
        return jsonify({"error": "That reading is no longer on file."}), 404
    return jsonify({
        "id": record["id"],
        "created_at": record["created_at"],
        "excerpt": record["excerpt"],
        "source": record["source"],
        "output": record["output"],
    })


@review.post("/correction")
@require_editor
def write_correction():
    payload = request.get_json(silent=True) or {}

    try:
        analysis_id = int(payload.get("analysis_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Which reading is being corrected? No analysis id."}), 400

    editor = (payload.get("editor") or "").strip()
    if not editor:
        return jsonify({
            "error": "Put your name to this correction. It decides whose judgement "
                     "the model can be asked to follow later."
        }), 400

    analysis = database.get_analysis(analysis_id)
    if analysis is None:
        return jsonify({"error": "That reading is no longer on file."}), 404

    corrected = clean(payload.get("corrected"), analysis["output"])
    diff = learning.changes(analysis["output"], corrected)

    if not diff:
        return jsonify({"error": "Nothing changed, so there is nothing to learn."}), 400

    try:
        record = learning.remember(
            analysis_id=analysis_id,
            corrected=corrected,
            editor=editor,
            note=payload.get("note"),
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    return jsonify({
        "correction_id": record["id"],
        "editor": record["editor"],
        "changed": record["changed"],
        "corrected": corrected,
        "embedded": record["has_embedding"],
    }), 201


@review.post("/score-terms")
@require_editor
def score_terms():
    """Score expressions the reviewer added, against the song being edited.

    The analyser only returns indices for terms it chose itself, so a term
    the reviewer types has never been scored by anything. This asks the model
    about those terms rather than leaving the editor to guess a number.
    """
    payload = request.get_json(silent=True) or {}

    try:
        analysis_id = int(payload.get("analysis_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Which reading are these terms for?"}), 400

    terms = _names(payload.get("terms"))
    if not terms:
        return jsonify({"scores": {}})

    analysis = database.get_analysis(analysis_id)
    if analysis is None:
        return jsonify({"error": "That reading is no longer on file."}), 404

    try:
        import scoring          # imported here so a missing key cannot break boot
        scores = scoring.score_terms(analysis["input_text"], terms)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    return jsonify({"scores": scores})


@review.get("/corrections")
def read_corrections():
    limit = request.args.get("limit", default=50, type=int)
    include_retired = request.args.get("all", default="1") != "0"
    editor = request.args.get("editor")
    editors = [editor] if editor else None
    return jsonify({
        "corrections": database.list_corrections(limit, include_retired, editors),
        "experts": database.experts(),
        "ranking": learning.stored_ranking(),
        "stats": database.stats(),
    })


@review.post("/correction/<int:correction_id>/retire")
@require_editor
def retire(correction_id):
    payload = request.get_json(silent=True) or {}
    restore = bool(payload.get("restore"))
    record = database.retire_correction(correction_id, active=restore)
    if record is None:
        return jsonify({"error": "No such correction."}), 404
    return jsonify({"correction_id": correction_id, "active": bool(record["active"])})


@review.get("/experts")
def read_experts():
    return jsonify({
        "experts": database.experts(),
        "ranking": learning.stored_ranking(),
        "filter": learning.stored_filter(),
        "stats": database.stats(),
    })


@review.post("/experts/ranking")
@require_editor
def write_ranking():
    """Order the experts by standing. First listed is heard first."""
    payload = request.get_json(silent=True) or {}
    ranking = []
    for name in _names(payload.get("ranking")):
        if name not in ranking:
            ranking.append(name)
    database.set_json_preference(learning.RANK_KEY, ranking[:3])
    return jsonify({"ranking": learning.stored_ranking()})


@review.get("/preferences")
def read_preferences():
    return jsonify({
        "use_learned": database.get_preference("use_learned", "1") == "1",
        "filter": learning.stored_filter(),
        "ranking": learning.stored_ranking(),
        "experts": database.experts(),
        "stats": database.stats(),
    })


@review.post("/preferences")
@require_editor
def write_preferences():
    payload = request.get_json(silent=True) or {}

    if "use_learned" in payload:
        database.set_preference("use_learned", "1" if payload["use_learned"] else "0")

    # An empty list means every expert, which is the default.
    if "filter" in payload:
        database.set_json_preference(learning.FILTER_KEY, _names(payload.get("filter")))

    return jsonify({
        "use_learned": database.get_preference("use_learned", "1") == "1",
        "filter": learning.stored_filter(),
        "ranking": learning.stored_ranking(),
    })


@review.get("/stats")
def read_stats():
    return jsonify(learning.health())