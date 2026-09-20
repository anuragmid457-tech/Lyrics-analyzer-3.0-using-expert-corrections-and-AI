"""
review.py - the expert-facing HTTP surface, as a blueprint.

app.py stays the thin layer it was: it registers this and carries on. Every
route here is mounted under /api/review.

    GET    /api/review/analysis/<id>            the reading being edited
    POST   /api/review/correction               save an expert edit
    GET    /api/review/corrections              the review log
    POST   /api/review/correction/<id>/retire   stop one teaching, or restore it
    GET    /api/review/experts                  who has corrected, and standings
    POST   /api/review/experts/ranking          set the top three
    GET    /api/review/preferences              mode and expert filter
    POST   /api/review/preferences              change them
    GET    /api/review/stats                    counts for the panel

A correction must carry the name of the person who made it. An unattributed
edit teaches the model something without anyone being answerable for it, and
it cannot be filtered or ranked afterwards.
"""

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
    "valence", "arousal", "quadrant",
    "rasa", "parjaay", "tradition", "language",
    "summary", "confidence",
    "music_therapy", "music_therapy_context", "recommendation_tags",
    "evidence",
}

QUADRANTS = {"Q1", "Q2", "Q3", "Q4"}


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


def clean(submitted, original):
    """Merge the reviewer's edits onto the original verdict, tidied."""
    out = dict(original or {})

    for key, value in (submitted or {}).items():
        if key in EDITABLE:
            out[key] = value

    out["valence"] = _clamp(out.get("valence"), -1.0, 1.0)
    out["arousal"] = _clamp(out.get("arousal"), -1.0, 1.0)
    out["confidence"] = _clamp(out.get("confidence"), 0.0, 1.0)

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