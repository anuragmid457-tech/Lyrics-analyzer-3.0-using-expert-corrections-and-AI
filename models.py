"""
models.py - which analyser reads the song.

One place listing every model the app can call, so adding a provider is an
entry here and a key in the environment rather than an edit in four files.
A model appears in the picker only when its key is set, so a deployment with
just GOOGLE_API_KEY shows one option and nothing breaks.

    available()        -> [{"id": "gemini", "label": ..., "model": ...}, ...]
    default_id()       -> the id used when the browser does not name one
    get_model(id)      -> a LangChain chat model, built once and reused

The model strings can be overridden from the environment, e.g. GROQ_MODEL,
because free catalogues change without notice: a provider can retire the
model you hardcoded, and recovering should mean changing one variable rather
than pushing code.
"""

import os

CATALOGUE = [
    {
        "id": "gemini",
        "label": "Gemini Flash Lite",
        "provider": "google_genai",
        "key": "GOOGLE_API_KEY",
        "env": "GEMINI_MODEL",
        "model": "gemini-3.1-flash-lite-preview",
        "note": "Google. Strongest on Bengali and Devanagari script here.",
    },
    {
        "id": "groq",
        "label": "GPT-OSS 120B on Groq",
        "provider": "groq",
        "key": "GROQ_API_KEY",
        "env": "GROQ_MODEL",
        "model": "llama-3.3-70b-versatile",
        "note": "Free tier, no card, very fast.",
    },
    {
        "id": "mistral",
        "label": "Mistral Small",
        "provider": "mistralai",
        "key": "MISTRAL_API_KEY",
        "env": "MISTRAL_MODEL",
        "model": "mistral-small-latest",
        "note": "Free Experiment tier.",
    },
]

_built = {}


def _entry(model_id):
    for entry in CATALOGUE:
        if entry["id"] == model_id:
            return entry
    return None


def _model_name(entry):
    return os.getenv(entry["env"], "").strip() or entry["model"]


def available():
    """Every model whose API key is present, in catalogue order."""
    out = []
    for entry in CATALOGUE:
        if not os.getenv(entry["key"], "").strip():
            continue
        out.append({
            "id": entry["id"],
            "label": entry["label"],
            "model": _model_name(entry),
            "note": entry["note"],
        })
    return out


def default_id():
    """LYRIQ_DEFAULT_MODEL if it is usable, else the first model with a key."""
    wanted = os.getenv("LYRIQ_DEFAULT_MODEL", "").strip()
    ready = [entry["id"] for entry in available()]
    if wanted in ready:
        return wanted
    return ready[0] if ready else ""


def label_of(model_id):
    entry = _entry(model_id)
    return entry["label"] if entry else model_id


def get_model(model_id, temperature=0.2):
    """A chat model, built on first use and kept for later calls."""
    entry = _entry(model_id)
    if entry is None:
        raise ValueError(f"No model called {model_id!r}.")
    if not os.getenv(entry["key"], "").strip():
        raise RuntimeError(
            f"{entry['label']} needs {entry['key']} set on the server."
        )

    cached = _built.get(model_id)
    if cached is not None:
        return cached

    from langchain.chat_models import init_chat_model
    built = init_chat_model(
        _model_name(entry),
        model_provider=entry["provider"],
        temperature=temperature,
    )
    _built[model_id] = built
    return built