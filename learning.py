"""
learning.py - what the system does with the corrections it has been given.

There is no fine-tuning here and there should not be: a handful of expert edits
is far too little to train on, and retraining would bury the reviewer's
reasoning inside weights where nobody can inspect or withdraw it. Instead the
corrections stay as rows, and the closest ones are handed back to the model as
evidence at the moment it reads a new song.

Corrections are attributed. Two experts can disagree about the same repertoire,
so a reading can be asked to follow one of them, and a ranking decides who is
heard first when several have something to say about the same song.

  exact      the same lyrics have been corrected before, so serve that
             correction instead of asking the model again
  similar    a nearby song has been corrected, so append the reviewer's
             changes and reasoning to the input as guidance
"""

import json
import math
import os
import re

try:                        # filenames differ in case between machines
    import database
except ImportError:         # pragma: no cover
    import database as database

# --- tuning knobs --------------------------------------------------------

THRESHOLD = float(os.getenv("LYRIQ_MATCH_THRESHOLD", "0.78"))
MAX_MATCHES = int(os.getenv("LYRIQ_MAX_MATCHES", "3"))

# Preference keys, shared with review.py and app.py.
FILTER_KEY = "expert_filter"     # whose corrections to hear, [] means everyone
RANK_KEY = "expert_rank"         # ordered list, best first, at most three

WATCHED = [
    "valence",
    "arousal",
    "emotion_indices",
    "quadrant",
    "primary_emotion",
    "canonical_emotion",
    "secondary_emotions",
    "mixed_emotion",
    "rasa",
    "parjaay",
    "tradition",
    "language",
    "confidence",
    "summary",
    "music_therapy",
    "recommendation_tags",
]

LONG_FIELDS = {"summary", "music_therapy"}

GUIDANCE_HEADER = (
    "\n\n=== Reviewer corrections on similar songs ===\n"
    "Human experts reviewed earlier readings of songs close to this one and "
    "changed them as listed below, each attributed to the reviewer who made it. "
    "Treat this as evidence about how these reviewers read this repertoire, not "
    "as facts about the present song. Apply the same reasoning where it fits the "
    "words in front of you, and ignore it where it does not. Where reviewers "
    "disagree, prefer the one listed first. Do not copy a previous verdict onto "
    "a different song, and do not mention these notes in your output.\n"
)


# --- embeddings ----------------------------------------------------------

_embedder = None


def _get_embedder():
    """Built on first use so importing this module never needs an API key."""
    global _embedder
    if _embedder is None:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        key = os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_API_KEY is not set; embeddings cannot run.")
        _embedder = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=key,
        )
    return _embedder


def embed(text):
    """Vector for one text, or None if the service is not reachable."""
    try:
        return _get_embedder().embed_query(text)
    except Exception:  # noqa: BLE001 - embeddings are an optimisation, not a requirement
        return None


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(y * y for y in b))
    if left == 0 or right == 0:
        return 0.0
    return dot / (left * right)


# --- stored choices ------------------------------------------------------

def stored_filter():
    """Names whose corrections are currently in play; empty list means all."""
    names = database.get_json_preference(FILTER_KEY, [])
    return [str(n) for n in names] if isinstance(names, list) else []


def stored_ranking():
    """Experts in order of standing, best first, at most three."""
    names = database.get_json_preference(RANK_KEY, [])
    return [str(n) for n in names][:3] if isinstance(names, list) else []


def _rank_of(editor, ranking):
    """Position in the ranking, or a large number for everyone unranked."""
    try:
        return ranking.index(editor)
    except ValueError:
        return len(ranking) + 99


# --- diffing -------------------------------------------------------------

def _comparable(value):
    if isinstance(value, dict):
        return ", ".join(f"{k} {float(v):.2f}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    if value is None:
        return ""
    return str(value).strip()


def _normalise(field, value):
    """Flatten differences of format so they are not mistaken for judgements."""
    text = _comparable(value)
    if field == "quadrant":
        return text[:2].upper()
    if field in ("secondary_emotions", "recommendation_tags"):
        return ", ".join(sorted(p.strip().lower() for p in text.split(",") if p.strip()))
    if field in ("primary_emotion", "canonical_emotion", "rasa",
                 "parjaay", "tradition", "language"):
        return text.strip().lower()
    return text


_EMPTY = {"", "no", "0.00", "none", "null"}


def changes(original, corrected):
    """Which watched fields the expert actually moved, and from what to what."""
    original = original or {}
    corrected = corrected or {}
    out = {}
    for field in WATCHED:
        before = _normalise(field, original.get(field))
        after = _normalise(field, corrected.get(field))
        if before == after:
            continue
        if field not in original and after.lower() in _EMPTY:
            continue
        out[field] = {
            "from": _comparable(original.get(field)),
            "to": _comparable(corrected.get(field)),
        }
    return out


# --- writing -------------------------------------------------------------

def remember(analysis_id, corrected, editor=None, note=None):
    """Store one expert edit against the reading it corrects."""
    analysis = database.get_analysis(analysis_id)
    if analysis is None:
        raise ValueError(f"No analysis with id {analysis_id}.")

    original = analysis["output"]
    diff = changes(original, corrected)

    correction_id = database.save_correction(
        analysis_id=analysis_id,
        original=original,
        corrected=corrected,
        changed=diff,
        editor=editor,
        note=note,
        embedding=embed(analysis["input_text"]),
    )
    return database.get_correction(correction_id)


# --- reading -------------------------------------------------------------

def exact_correction(text, editors=None):
    """A correction for byte-identical input, or None."""
    return database.correction_for(text, editors=editors)


def similar_corrections(text, limit=MAX_MATCHES, threshold=THRESHOLD,
                        editors=None, ranking=None):
    """The nearest corrections to this song.

    Ordered by the expert ranking first and similarity second, so a ranked
    reviewer's correction is heard before an unranked one that happens to
    score a little higher.
    """
    pool = database.active_corrections(with_vector=True, editors=editors)
    if not pool:
        return []

    vector = embed(text)
    if vector is None:
        return []

    ranking = ranking or []
    scored = []
    for correction in pool:
        score = _cosine(vector, correction.get("embedding"))
        if score >= threshold:
            correction = dict(correction)
            correction["similarity"] = round(score, 3)
            correction["rank"] = _rank_of(correction["editor"], ranking)
            correction.pop("embedding", None)
            scored.append(correction)

    scored.sort(key=lambda c: (c["rank"], -c["similarity"]))
    return scored[:limit]


def _teaching_lines(correction):
    lines = []
    for field, move in (correction.get("changed") or {}).items():
        before, after = move.get("from", ""), move.get("to", "")
        if field in LONG_FIELDS:
            lines.append(f"  {field}: the reviewer rewrote this. Theirs reads: {after[:240]}")
        else:
            lines.append(f"  {field}: you said {before or 'nothing'}, "
                         f"the reviewer set {after or 'nothing'}")
    if correction.get("note"):
        lines.append(f"  their reasoning: {correction['note']}")
    return lines

VOCAB_HEADER = (
    "\n\n=== Reviewer vocabulary ===\n"
    "Terms the experts reviewing this system have used for emotion labels, with "
    "how often each was chosen. Where one of these names the feeling in this song "
    "better than a standard English label, prefer it, and match their habit of "
    "joining two or three terms with a slash. Do not force a term that does not "
    "fit the song, and do not list these in your output.\n"
)


def vocabulary(editors=None, limit=30):
    """Every emotion term the experts have written, commonest first.

    Read from the corrected side of each correction, split on the separators
    the editor used, so a compound like masti/playfulness contributes both.
    """
    terms = {}
    for correction in database.active_corrections(with_vector=False, editors=editors):
        for field in ("primary_emotion", "canonical_emotion"):
            move = (correction.get("changed") or {}).get(field)
            if not move:
                continue
            for part in re.split(r"[/,]", move.get("to", "")):
                term = part.strip()
                if not term or len(term) > 40:
                    continue
                entry = terms.setdefault(term.lower(), {"term": term, "count": 0})
                entry["count"] += 1

    return sorted(terms.values(), key=lambda e: -e["count"])[:limit]


def vocabulary_block(editors=None):
    """The glossary as a block to append to the analyser's input."""
    terms = vocabulary(editors)
    if not terms:
        return ""
    listing = ", ".join(f"{t['term']} (used {t['count']}x)" for t in terms)
    return VOCAB_HEADER + listing + "\n=== end of reviewer vocabulary ===\n"


def guidance_for(text, limit=MAX_MATCHES, threshold=THRESHOLD,
                 editors=None, ranking=None):
    """Build the block appended to the analyser's input.

    Returns (block, matches). block is "" when there is nothing to teach, so a
    lyrics-only request reaches analyze() exactly as it does without any of this.
    """
    matches = similar_corrections(text, limit, threshold, editors, ranking)
    if not matches:
        return "", []

    blocks = []
    for index, correction in enumerate(matches, start=1):
        lines = _teaching_lines(correction)
        if not lines:
            continue
        head = (f"\nSimilar song {index}, reviewed by {correction['editor']}, "
                f"similarity {correction['similarity']}, "
                f"opening {correction['excerpt'][:70]}")
        blocks.append(head + "\n" + "\n".join(lines))

    if not blocks:
        return "", []

    return GUIDANCE_HEADER + "\n".join(blocks) + "\n=== end of reviewer corrections ===\n", matches


def summarise(matches):
    """Small shape for the browser, so the page can say what it leaned on."""
    return [
        {
            "correction_id": match["id"],
            "editor": match["editor"],
            "similarity": match["similarity"],
            "excerpt": match["excerpt"],
            "fields": list((match.get("changed") or {}).keys()),
            "note": match.get("note"),
        }
        for match in matches
    ]


def health():
    """Whether learned mode can do anything useful right now."""
    counts = database.stats()
    counts["embeddings_available"] = embed("test") is not None
    counts["threshold"] = THRESHOLD
    counts["max_matches"] = MAX_MATCHES
    counts["expert_filter"] = stored_filter()
    counts["expert_rank"] = stored_ranking()
    return counts