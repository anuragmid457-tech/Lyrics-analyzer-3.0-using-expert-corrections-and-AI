"""
lookup.py - find a song by name, and be honest about what was found.

Two separate jobs, deliberately kept apart:

    find_lyrics(title, artist)    fetches real lyrics from LRCLIB
    describe_song(title, artist)  asks the model for context only

The separation matters. A language model asked for lyrics will produce
something fluent and partly invented, and the analyser would then read words
the song does not contain. Every correction saved against that reading would
teach the system from a text nobody wrote. So the model is never asked for
lyrics here, and the prompt says so twice.

What the model is asked for is context: composer, tradition, language, raga,
taal, parjaay. Those are facts it may know and may equally get wrong, so each
comes back with the model's own confidence and is shown to the reviewer for
checking rather than fed straight into the analyser.

LRCLIB is free, needs no key, and is crowdsourced from music players. Coverage
of Bengali and South Asian repertoire is thin: Rabindrasangeet, Baul and Lalan
will often not be there at all, and the honest answer in that case is to say
so and let the reviewer paste the lyrics. Its lyrics are offered for
educational and personal use.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

LRCLIB = os.getenv("LYRIQ_LYRICS_API", "https://lrclib.net/api/search")

# LRCLIB asks that clients identify themselves.
USER_AGENT = os.getenv(
    "LYRIQ_USER_AGENT",
    "LYRIQ research prototype (music emotion recognition)",
)

TIMEOUT = 12
MAX_CANDIDATES = 5

# What to ask about, per repertoire. A Bollywood song is identified by its
# film, a Rabindrasangeet by its parjaay, and a Hollywood song by neither, so
# asking the same questions of all three wastes the model's attention and
# invites it to invent a parjaay for a film song.
REPERTOIRES = {
    "bollywood": {
        "label": "Bollywood",
        "lead": "This is a Hindi film song. Identify the film it belongs to, "
                "its music director and lyricist, and the playback singers. "
                "Parjaay does not apply: return null for it. Raga applies only "
                "where the song is genuinely raga based, which many are not.",
        "fields": ["film", "composer", "lyricist", "performers", "year",
                   "language", "raga", "taal", "laya"],
    },
    "rabindrasangeet": {
        "label": "Rabindrasangeet",
        "lead": "This is a Tagore song. The composer is Rabindranath Tagore "
                "unless you have reason to say otherwise. Give its parjaay "
                "from Puja, Prem, Prakriti, Swadesh, Anushthanik, Bichitro or "
                "Nrityanatya, and its raga and taal, which are usually "
                "documented in Swarabitan. There is no film: return null.",
        "fields": ["composer", "parjaay", "raga", "taal", "laya", "year",
                   "performers", "language"],
    },
    "hollywood": {
        "label": "Hollywood and Western popular",
        "lead": "This is a Western popular song. Identify the artist or band, "
                "the writers, the album, and the film if it was written for "
                "one. Parjaay, raga and taal do not apply: return null for "
                "each rather than inventing an equivalent.",
        "fields": ["composer", "lyricist", "performers", "album", "film",
                   "year", "language"],
    },
}


def repertoire_of(name):
    return REPERTOIRES.get(str(name or "").strip().lower())


CONTEXT_PROMPT = """You supply factual context about a song so a researcher can \
fill in metadata fields. You handle Bengali and South Asian repertoire as first \
class cases: Rabindrasangeet, Baul sangeet, Lalan geeti, Nazrul geeti, Shyama \
sangeet, Sufi and qawwali, kirtan and bhajan, film song, alongside general \
popular music.

NEVER output lyrics. Not a line, not a phrase, not a remembered fragment, in any \
script or transliteration. If asked for them, leave the field out. The lyrics are \
fetched from a separate source and yours would be a reconstruction from memory, \
which is worse than nothing for this purpose.

Give only what you actually know about the song named. For every field you are \
unsure of, use null rather than a guess: a null costs the researcher one lookup, \
a wrong raga costs them a corrupted record. If you do not recognise the song at \
all, return every field as null and say so in the note.

Fields:
  title       the song's name as usually written, in roman transliteration
  original    the title in its own script, if it has one, else null
  film        the film the song belongs to, else null
  album       the album it appears on, else null
  composer    composer or music director
  lyricist    lyricist or songwriter, else null
  performers  up to three well known performers, as a list, else []
  tradition   e.g. Rabindrasangeet, Baul sangeet, Bengali film song
  language    the language of the lyrics
  parjaay     for Rabindrasangeet only: Puja, Prem, Prakriti, Swadesh,
              Anushthanik, Bichitro or Nrityanatya. null otherwise
  raga        raga or scale, if the song is known to have one
  taal        taal, if known
  laya        tempo in words, if known
  year        year of composition, as a number, else null
  confidence  0.0 to 1.0, how sure you are this is a real song you know
  note        one plain sentence on what you are unsure about, or "" if nothing

Return ONLY a raw JSON object in that shape, no markdown fences, no commentary, \
and no lyrics anywhere in it."""


# --- lyrics, from a real source -----------------------------------------

def find_lyrics(title, artist="", film=""):
    """Search LRCLIB. Returns a list of candidates, possibly empty.

    A Hindi film song is catalogued under its soundtrack, so the film name is
    sent as the album: it is usually the difference between finding the song
    and finding nothing.
    """
    import requests

    title = (title or "").strip()
    if not title:
        return []

    params = {"track_name": title}
    if (artist or "").strip():
        params["artist_name"] = artist.strip()
    if (film or "").strip():
        params["album_name"] = film.strip()

    try:
        response = requests.get(
            LRCLIB,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        found = response.json()
    except Exception:  # noqa: BLE001 - a missing song is not an error worth raising
        return []

    if not isinstance(found, list):
        return []

    out = []
    for item in found[:MAX_CANDIDATES * 3]:
        if not isinstance(item, dict):
            continue

        # An entry can exist with no lyrics at all, which is no use here.
        lyrics = (item.get("plainLyrics") or "").strip()
        if not lyrics or item.get("instrumental"):
            continue

        out.append({
            "id": item.get("id"),
            "title": (item.get("trackName") or "").strip(),
            "artist": (item.get("artistName") or "").strip(),
            "album": (item.get("albumName") or "").strip(),
            "duration": item.get("duration"),
            "lyrics": lyrics,
            "lines": len([line for line in lyrics.splitlines() if line.strip()]),
            "source": "LRCLIB",
        })

        if len(out) >= MAX_CANDIDATES:
            break

    return out


# --- context, from the model, clearly marked ----------------------------

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


def _clean_text(value, limit=120):
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


# Anything resembling a lyric field is dropped even if the model produces one.
FORBIDDEN = {"lyrics", "lyric", "text", "words", "verse", "refrain",
             "sthayi", "antara", "first_line", "opening"}


def describe_song(title, artist="", chat=None, repertoire="", film=""):
    """Context about the song from the model. Never lyrics."""
    from langchain_core.messages import HumanMessage, SystemMessage

    title = (title or "").strip()
    if not title:
        return {}

    if chat is None:
        import models
        chat = models.get_model(models.default_id())

    prompt = CONTEXT_PROMPT
    kind = repertoire_of(repertoire)
    if kind:
        prompt += "\n\nThis song comes from a known repertoire. " + kind["lead"]

    asked = f"Song: {title}"
    if (film or "").strip():
        asked += f"\nFilm: {film.strip()}"
    if (artist or "").strip():
        asked += f"\nArtist, composer or singer: {artist.strip()}"

    raw = chat.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content=asked),
    ]).content

    try:
        parsed = _loads(raw)
    except (json.JSONDecodeError, ValueError, AttributeError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    # Belt and braces: strip any field that could carry song text.
    for key in list(parsed):
        if str(key).strip().lower() in FORBIDDEN:
            parsed.pop(key)

    performers = parsed.get("performers")
    if not isinstance(performers, list):
        performers = []

    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0

    try:
        year = int(parsed.get("year"))
    except (TypeError, ValueError):
        year = None

    return {
        "title": _clean_text(parsed.get("title")) or title,
        "original": _clean_text(parsed.get("original")),
        "film": _clean_text(parsed.get("film")) or (_clean_text(film) or None),
        "album": _clean_text(parsed.get("album")),
        "composer": _clean_text(parsed.get("composer")),
        "lyricist": _clean_text(parsed.get("lyricist")),
        "performers": [_clean_text(p, 60) for p in performers[:3] if _clean_text(p, 60)],
        "tradition": _clean_text(parsed.get("tradition"), 60),
        "language": _clean_text(parsed.get("language"), 40),
        "parjaay": _clean_text(parsed.get("parjaay"), 40),
        "raga": _clean_text(parsed.get("raga"), 60),
        "taal": _clean_text(parsed.get("taal"), 40),
        "laya": _clean_text(parsed.get("laya"), 40),
        "year": year,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "note": _clean_text(parsed.get("note"), 300) or "",
        "repertoire": (kind or {}).get("label", ""),
        "verified": False,   # nothing here has been checked against a source
    }


# --- what the browser asks for ------------------------------------------

def lookup(title, artist="", chat=None, repertoire="", film=""):
    """Both halves, plus a plain account of what was and was not found."""
    candidates = find_lyrics(title, artist, film)

    # A film name that does not match the catalogue's spelling excludes
    # everything, so a fruitless search is tried again without it.
    if not candidates and (film or "").strip():
        candidates = find_lyrics(title, artist)

    try:
        context = describe_song(
            title, artist, chat=chat, repertoire=repertoire, film=film
        )
    except Exception as exc:  # noqa: BLE001 - context is optional, lyrics are not
        context = {"error": f"{type(exc).__name__}: {exc}"}

    if candidates:
        notice = (
            f"{len(candidates)} lyric source"
            f"{'' if len(candidates) == 1 else 's'} found on LRCLIB. "
            "Check the words against a copy you trust before reading the "
            "expression: the catalogue is crowdsourced and a version can be "
            "partial or misattributed."
        )
    elif repertoire_of(repertoire) and repertoire.lower() == "rabindrasangeet":
        notice = (
            "No lyrics found, which is the usual outcome for Rabindrasangeet: "
            "LRCLIB is crowdsourced from music players and carries very little "
            "Bengali repertoire. Paste the lyrics instead; the context below "
            "may still save you typing the parjaay, raga and taal."
        )
    elif repertoire_of(repertoire) and repertoire.lower() == "bollywood":
        notice = (
            "No lyrics found. Try the song's exact catalogue spelling, or drop "
            "the film name, which has to match the soundtrack title in the "
            "catalogue rather than the film as commonly written."
        )
    else:
        notice = (
            "No lyrics found. Check the spelling, or paste the lyrics instead; "
            "the context below may still save you some typing."
        )

    kind = repertoire_of(repertoire)

    return {
        "query": {
            "title": title,
            "artist": artist,
            "film": film,
            "repertoire": (kind or {}).get("label", ""),
        },
        "candidates": candidates,
        "context": context,
        "notice": notice,
        "lyrics_source": "LRCLIB",
    }