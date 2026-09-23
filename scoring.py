"""
scoring.py - how strongly does this song express this word?

When an expert adds an expression of their own, such as bakchodi or
independent girl, nothing has ever scored it: the analyser only returns
indices for the terms it chose itself. Rather than defaulting those boxes to
some placeholder number, the term is sent back to the model with the lyrics
and scored on its own.

    score_terms(text, ["bakchodi", "longing"]) -> {"bakchodi": 0.78, ...}

One call covers every term, so adding four expressions costs one request.
The model sees the terms together, which also keeps the scores relative to
each other rather than each judged in isolation.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

MAX_TERMS = 8

SYSTEM_PROMPT = """You score how strongly a song expresses particular words, \
given its lyrics.

You will be given the lyrics of one song and a list of terms. The terms come \
from a human reviewer, so they may be ordinary emotion words, or words from \
Bengali, Hindi or Urdu such as masti, bakchodi, biraha or ishq, or short \
descriptive phrases such as independent girl or main character energy. Judge \
each one on its own terms, including slang and cultural register.

For every term, give a number from 0.0 to 1.0 for how strongly this song \
expresses that term. 0.0 means the song does not express it at all. 0.5 means \
it is present but not central. 1.0 means the song is overwhelmingly about it.

Score each term independently. They need not sum to anything, and several \
terms may legitimately score high at once. Judge only from the supplied text, \
never from what you recall of the song. Where a term does not apply to this \
song at all, say so with a low number rather than being polite about it.

Return ONLY a raw JSON object mapping each term exactly as it was given to its \
number, with no markdown fences and no commentary:

{"term one": 0.0, "term two": 0.0}
"""

_model = None


def _get_model():
    """Built on first use so importing this module never needs an API key."""
    global _model
    if _model is None:
        from langchain.chat_models import init_chat_model
        _model = init_chat_model(
            "gemini-3.1-flash-lite-preview",
            model_provider="google_genai",
            temperature=0.2,
        )
    return _model


def _text_of(raw):
    if isinstance(raw, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw
        )
    return raw or ""


def _loads(raw):
    cleaned = _text_of(raw).strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) > 1:
            cleaned = parts[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    return json.loads(cleaned.strip("` \n"))


def score_terms(text, terms):
    """Return {term: 0.0-1.0} for every term the model could score.

    Terms it leaves out are simply absent from the result, so the caller can
    tell the difference between "scored zero" and "not scored".
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    wanted = []
    for term in (terms or []):
        name = str(term).strip()
        if name and name not in wanted:
            wanted.append(name)
    wanted = wanted[:MAX_TERMS]

    if not wanted or not (text or "").strip():
        return {}

    raw = _get_model().invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content="Lyrics:\n\n" + text
                     + "\n\nTerms to score:\n" + "\n".join(wanted)),
    ]).content

    try:
        parsed = _loads(raw)
    except (json.JSONDecodeError, ValueError, AttributeError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    out = {}
    for term in wanted:
        if term not in parsed:
            continue
        try:
            value = float(parsed[term])
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        out[term] = round(max(0.0, min(1.0, value)), 2)
    return out