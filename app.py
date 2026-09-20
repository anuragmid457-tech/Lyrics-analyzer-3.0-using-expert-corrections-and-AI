"""
app.py - HTTP layer only.

No prompt, no model, no label logic lives here. Both live in your two
analyser files:
  lyrics_senti_analysis.py  -> analyze(lyrics: str) -> dict
  lyrics_text.py            -> detect_emotion(path) -> str   (reads a PDF)

Four modules sit beside them:
  graph.py      section by section scores, shaped as Plotly figures
  learning.py   what past expert corrections should tell the analyser
  review.py     the expert-facing routes, registered as a blueprint
  database.py   storage, and the only file that touches SQLite
"""

import os
import tempfile

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

try:                        # filenames differ in case between machines
    import database
    import learning
    from review import review
except ImportError:         # pragma: no cover
    import Database as database
    import Learning as learning
    from Review import review

import graph
import lyrics_senti_analysis
import lyrics_text

app = Flask(__name__)
app.register_blueprint(review)

database.init()

# --- optional inputs -----------------------------------------------------
OPTIONAL = [
    ("title", "Title"),
    ("composer", "Composer or lyricist"),
    ("tradition", "Tradition"),
    ("language", "Language"),
    ("parjaay", "Parjaay"),
    ("raga", "Raga or scale"),
    ("taal", "Taal"),
    ("laya", "Laya or tempo"),
    ("notation", "Notation"),
    ("notes", "Notes"),
]
MULTILINE = {"notation", "notes"}


def compose(payload):
    """Fold whatever optional fields were filled in into one text block."""
    lyrics = payload["lyrics"].strip()
    extras = []
    for key, label in OPTIONAL:
        value = (payload.get(key) or "").strip()
        if not value:
            continue
        extras.append(f"{label}:\n{value}" if key in MULTILINE else f"{label}: {value}")
    if not extras:
        return lyrics
    return lyrics + "\n\n---\nSupplied alongside the lyrics:\n" + "\n".join(extras)


def learned_mode(payload):
    """Per-request choice if the browser sent one, otherwise the stored default."""
    if isinstance(payload.get("use_learned"), bool):
        return payload["use_learned"]
    return database.get_preference("use_learned", "1") == "1"


def chosen_experts(payload):
    """Whose corrections to hear. None means everyone."""
    if isinstance(payload.get("experts"), list):
        names = [str(n).strip() for n in payload["experts"] if str(n).strip()]
        return names or None
    return learning.stored_filter() or None


# --- response shaping ----------------------------------------------------

QUADRANTS = {
    "Q1": "Q1 · happy, excited",
    "Q2": "Q2 · tense, agitated",
    "Q3": "Q3 · sad, subdued",
    "Q4": "Q4 · calm, serene",
}


def for_browser(result):
    """Light touch-up of your JSON so the page can render it."""
    out = dict(result)

    code = str(out.get("quadrant") or "")[:2].upper()
    if code not in QUADRANTS:
        valence, arousal = out.get("valence") or 0, out.get("arousal") or 0
        code = ("Q1" if arousal >= 0 else "Q4") if valence >= 0 else ("Q2" if arousal >= 0 else "Q3")
    out["quadrant"] = code
    out["quadrant_label"] = QUADRANTS[code]

    if "music_therapy_context" not in out and "music_therapy" in out:
        out["music_therapy_context"] = out["music_therapy"]

    out.setdefault("status", "ok")
    return out


# --- routes --------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True) or {}
    if not (payload.get("lyrics") or "").strip():
        return jsonify({"error": "Paste some lyrics to analyse."}), 400

    text = compose(payload)
    use_learned = learned_mode(payload)
    experts = chosen_experts(payload) if use_learned else None
    ranking = learning.stored_ranking() if use_learned else []

    # An identical song corrected before is served from that correction rather
    # than asked again. Turn learned mode off to see what the model says alone.
    if use_learned:
        hit = learning.exact_correction(text, editors=experts)
        if hit:
            out = for_browser(hit["corrected"])
            out["analysis_id"] = database.save_analysis(
                text, hit["corrected"], source="correction",
                learned_from=[hit["id"]],
            )
            out["learning"] = {
                "mode": "learned",
                "source": "correction",
                "correction_id": hit["id"],
                "edited_at": hit["created_at"],
                "editor": hit["editor"],
                "experts": experts or [],
                "ranking": ranking,
                "matches": [],
            }
            return jsonify(out)

    guidance, matches = (
        learning.guidance_for(text, editors=experts, ranking=ranking)
        if use_learned else ("", [])
    )

    try:
        vocab = learning.vocabulary_block(editors=experts) if use_learned else ""
        result = lyrics_text.analyze(text + vocab + guidance)
    except Exception as exc:  # noqa: BLE001 - show the real cause in the UI
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    if not isinstance(result, dict):
        return jsonify({"error": "analyze() returned something other than a dict."}), 500
    if result.get("parse_error"):
        return jsonify({
            "error": "The model did not return valid JSON.",
            "raw": result.get("raw_output", ""),
        }), 502

    out = for_browser(result)
    out["analysis_id"] = database.save_analysis(
        text, result, source="model",
        learned_from=[match["id"] for match in matches] or None,
    )
    out["learning"] = {
        "mode": "learned" if use_learned else "default",
        "source": "guided" if matches else "model",
        "experts": experts or [],
        "ranking": ranking,
        "matches": learning.summarise(matches),
    }
    return jsonify(out)


@app.post("/api/graph")
def api_graph():
    """Section-by-section scores for the same lyrics, shaped as Plotly figures."""
    payload = request.get_json(silent=True) or {}
    if not (payload.get("lyrics") or "").strip():
        return jsonify({"error": "Paste some lyrics to plot."}), 400

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        analysis = None

    try:
        return jsonify(graph.build_graph(compose(payload), analysis))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/analyze-pdf")
def api_analyze_pdf():
    """Runs detect_emotion() on an uploaded PDF, returning its raw string."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Attach a PDF first."}), 400
    if not upload.filename.lower().endswith(".pdf"):
        return jsonify({"error": "detect_emotion() expects a PDF file."}), 400

    # tempfile.gettempdir() rather than "/tmp", which does not exist on Windows,
    # and secure_filename so an uploaded name cannot walk out of that directory.
    safe_name = secure_filename(upload.filename) or "upload.pdf"
    tmp_path = os.path.join(tempfile.gettempdir(), safe_name)
    upload.save(tmp_path)
    try:
        text = lyrics_senti_analysis.detect_emotion(tmp_path)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        os.remove(tmp_path)

    return jsonify({"raw_text": text})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))