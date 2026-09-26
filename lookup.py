"""
lookup.py - find a song by name, by film, or by the person who made it.

Three doors in:

    lookup(title=...)      one song: lyrics, plus context
    by_film(film=...)      everything in a film, to choose from
    by_person(person=...)  everything by a composer or singer, to choose from

and four repertoires, because "Ae mere humsafar" and "Khacar bhitor ochin
pakhi" are not looked up the same way:

    bollywood        Hindi film song, has a film
    bengali          Bengali film and modern song, may have a film
    rabindrasangeet  Tagore, has no film and does have a parjaay
    hollywood        English language popular and film song, may have a film

Two sources, kept apart on purpose
----------------------------------
LRCLIB supplies lyrics. It is free, needs no key, and every line it returns
was typed by a person rather than recalled by a model. Its catalogue is
crowdsourced from music players, so Hindi and English film song are reasonably
covered and Bengali repertoire is thin.

The model supplies lists and context: which songs a film contains, what a
composer wrote, what raga a song uses. Those are facts it may know and may
equally invent, so everything from it comes back marked unverified and is
never used as lyrics. The model is never asked for lyrics, and any lyric
shaped field it volunteers is stripped before the browser sees it.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

LRCLIB = os.getenv("LYRIQ_LYRICS_API", "https://lrclib.net/api/search")

USER_AGENT = os.getenv(
    "LYRIQ_USER_AGENT",
    "LYRIQ research prototype (music emotion recognition)",
)

TIMEOUT = 12
MAX_CANDIDATES = 5
MAX_TITLES = 25


# --- what kind of song we are looking for -------------------------------

REPERTOIRES = {
    "bollywood": {
        "label": "Bollywood",
        "tradition": "Bollywood film song",
        "language": "Hindi",
        "film_label": "Film",
        "has_film": True,
        "hint": "Hindi film song. Naming the film narrows the search a great deal.",
    },
    "bengali": {
        "label": "Bengali",
        "tradition": "Bengali song",
        "language": "Bengali",
        "film_label": "Film or album",
        "has_film": True,
        "hint": "Bengali film and modern song. Lyric coverage is thin, so "
                "expect to paste the words yourself more often than not.",
    },
    "rabindrasangeet": {
        "label": "Rabindrasangeet",
        "tradition": "Rabindrasangeet",
        "language": "Bengali",
        "film_label": "",
        "has_film": False,
        "hint": "Tagore. Lyrics are rarely in the catalogue, but the parjaay "
                "and raga the model suggests still save typing.",
    },
    "hollywood": {
        "label": "Hollywood",
        "tradition": "English language popular song",
        "language": "English",
        "film_label": "Film",
        "has_film": True,
        "hint": "English language popular and film song. Best covered of the four.",
    },
}


def repertoire_of(name):
    return REPERTOIRES.get(str(name or "").strip().lower())


def repertoires():
    """The list the browser draws its bar from."""
    return [
        {
            "id": key,
            "label": entry["label"],
            "film_label": entry["film_label"],
            "has_film": entry["has_film"],
            "hint": entry["hint"],
        }
        for key, entry in REPERTOIRES.items()
    ]


# --- prompts -------------------------------------------------------------

NEVER_LYRICS = """NEVER output lyrics. Not a line, not a phrase, not a remembered \
fragment, in any script or transliteration. The lyrics come from a separate \
source; yours would be a reconstruction from memory, which would corrupt the \
research this feeds."""

CONTEXT_PROMPT = """You supply factual context about a song so a researcher can \
fill in metadata fields. You handle Bengali and South Asian repertoire as first \
class cases: Rabindrasangeet, Baul sangeet, Lalan geeti, Nazrul geeti, Shyama \
sangeet, Hindi and Bengali film song, Sufi and qawwali, alongside English \
language popular music.

""" + NEVER_LYRICS + """

Give only what you actually know about the song named. For every field you are \
unsure of use null rather than a guess: a null costs the researcher one lookup, \
a wrong raga costs them a corrupted record. If you do not recognise the song, \
return every field null and say so in the note.

Fields:
  title       the song as usually written, in roman transliteration
  original    the title in its own script, if it has one, else null
  film        the film it comes from, if any, else null
  composer    composer or music director
  lyricist    lyricist, if different from the composer
  performers  up to three well known performers, as a list, else []
  tradition   e.g. Rabindrasangeet, Bollywood film song, Bengali modern song
  language    the language of the lyrics
  parjaay     for Rabindrasangeet only: Puja, Prem, Prakriti, Swadesh,
              Anushthanik, Bichitro or Nrityanatya. null otherwise
  raga        raga or scale, if the song is known to have one
  taal        taal, if known
  laya        tempo in words, if known
  year        year of release or composition, as a number, else null
  confidence  0.0 to 1.0, how sure you are this is a real song you know
  note        one plain sentence on what you are unsure about, or ""

Return ONLY a raw JSON object in that shape. No markdown fences, no commentary, \
no lyrics anywhere in it."""

LIST_PROMPT = """You list the songs of a film, or the songs written or sung by one \
person, so a researcher can choose one to study. You know Hindi and Bengali film \
music, Rabindrasangeet, and English language popular and film song.

""" + NEVER_LYRICS + """

Treat the name as case insensitive and possibly mis-spelled: the researcher may type it in capitals, in lower case, or with a word missing. Recognise the film or person they mean.

List only songs you are genuinely confident belong there. A short accurate list \
is worth far more than a long one padded with plausible titles: every invented \
title is a lookup the researcher wastes. If you do not recognise what you \
are given, return an empty list and say so in the note. If it may be newer than \
what you were trained on, say that in the note rather than guessing at titles.

For each song give:
  title       as usually written, in roman transliteration
  original    the title in its own script, if it has one, else null
  performers  up to two singers, as a list, else []
  composer    composer or music director, if you know it
  year        as a number, else null

And alongside the list:
  subject     the film or person as you understand it, spelled properly
  kind        "film" or "person"
  confidence  0.0 to 1.0, how sure you are of the list as a whole
  note        one plain sentence on what is uncertain, or ""

Return ONLY a raw JSON object of this shape, no markdown fences, no commentary:

{"subject": "", "kind": "", "confidence": 0.0, "note": "",
 "songs": [{"title": "", "original": null, "performers": [], "composer": null,
            "year": null}]}"""


# --- LRCLIB --------------------------------------------------------------

def search_tracks(track=None, artist=None, album=None, query=None):
    """One LRCLIB search. Returns only entries that actually carry lyrics."""
    import requests

    params = {}
    if (track or "").strip():
        params["track_name"] = track.strip()
    if (artist or "").strip():
        params["artist_name"] = artist.strip()
    if (album or "").strip():
        params["album_name"] = album.strip()
    if (query or "").strip():
        params["q"] = query.strip()

    if not params:
        return []

    try:
        response = requests.get(
            LRCLIB,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        found = response.json()
    except Exception:  # noqa: BLE001 - a miss is not an error worth raising
        return []

    if not isinstance(found, list):
        return []

    out = []
    for item in found:
        if not isinstance(item, dict):
            continue

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

    return out


# Words too common to tell one film from another.
STOP_TOKENS = {"the", "a", "an", "of", "and", "aur", "ki", "ka", "ke", "hai",
               "ho", "mein", "se", "original", "soundtrack", "motion", "picture",
               "ost", "songs", "music", "from", "film", "movie", "vol"}


def _tokens(text):
    bare = "".join(
        character if character.isalnum() else " "
        for character in str(text or "").lower()
    )
    return [word for word in bare.split() if word and word not in STOP_TOKENS]


def _close_enough(wanted, haystack):
    """Does this catalogue row plausibly belong to what was asked for?

    Whole string matching fails the moment a name is partial or spelled a
    little differently: "Rani Ki Prem Kahanis" appears nowhere, though the
    film does. So the test is how many of the asked-for words turn up, with
    a short query needing all of them and a long one needing most.
    """
    asked = _tokens(wanted)
    if not asked:
        return False

    found = set(_tokens(haystack))
    hits = sum(1 for word in asked if word in found)

    if len(asked) == 1:
        return hits == 1

    # Two words must both land; beyond that, three fifths is enough, which
    # lets a missing or mis-typed word through without letting in a film
    # that merely shares one common word.
    if len(asked) == 2:
        return hits == 2

    return hits / len(asked) >= 0.6


def find_lyrics(title, artist="", film="", limit=MAX_CANDIDATES):
    """Lyric candidates for one song, widening the search until something sticks."""
    title = normalise(title)
    artist = normalise(artist)
    film = normalise(film)
    if not title:
        return []

    attempts = [
        {"track": title, "artist": artist, "album": film},
        {"track": title, "artist": artist},
        {"track": title, "album": film},
        {"track": title},
        {"query": " ".join(part for part in [title, artist, film] if part)},
        {"query": title},
    ]

    seen, out = set(), []
    for attempt in attempts:
        rows = search_tracks(**attempt)

        # The free text passes return anything vaguely similar, so the title
        # has to actually be recognisable in what comes back.
        if "query" in attempt:
            rows = [row for row in rows
                    if _close_enough(title, row["title"])
                    or _close_enough(title, " ".join([row["title"], row["album"]]))]

        for row in rows:
            key = row["id"] or (row["title"], row["artist"])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= limit:
                return out

        if out:
            return out

    return out


# --- the model's half ----------------------------------------------------

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


# Words that stay lowercase inside a title, and ones that are always capitals.
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "is",
               "ka", "ke", "ki", "mein", "na", "ne", "o", "of", "on", "or",
               "par", "se", "the", "to", "aur", "hai", "ho", "je", "ar"}

KEEP_UPPER = {"dc", "mtv", "rrr", "kgf", "ddlj", "kkhh", "ii", "iii", "iv",
              "vi", "vii", "viii", "ix", "xi", "ok", "dj", "abcd"}


def normalise(text):
    """What the person typed, in one predictable shape.

    A model answers "COCKTAIL 2", "cocktail 2" and "Cocktail 2" differently,
    because case is part of the prompt. The catalogue does not care, but the
    model does, so every search is tidied to the same shape first and the same
    question comes back with the same songs however it was typed.
    """
    words = " ".join(str(text or "").split())
    if not words:
        return ""

    out = []
    for index, word in enumerate(words.lower().split(" ")):
        bare = word.strip(".,:;!?()[]'\"")

        # K3G, RRR, 3 Idiots: a short token carrying a digit, or a known
        # abbreviation, is a name rather than a word.
        if bare in KEEP_UPPER or (any(c.isdigit() for c in bare) and len(bare) <= 4):
            out.append(word.upper())
        elif index > 0 and bare in SMALL_WORDS:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])

    return " ".join(out)


def _clean_text(value, limit=120):
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _confidence(value):
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return 0.0


# Any field that could smuggle song text back in.
FORBIDDEN = {"lyrics", "lyric", "text", "words", "verse", "refrain", "chorus",
             "sthayi", "antara", "mukhda", "first_line", "opening", "excerpt"}


def _strip_lyrics(payload):
    if not isinstance(payload, dict):
        return payload
    for key in list(payload):
        if str(key).strip().lower() in FORBIDDEN:
            payload.pop(key)
    return payload


def _chat_or_default(chat):
    if chat is not None:
        return chat
    import models
    return models.get_model(models.default_id())


def describe_song(title, artist="", film="", repertoire=None, chat=None):
    """Context about one song. Never lyrics."""
    from langchain_core.messages import HumanMessage, SystemMessage

    title = normalise(title)
    artist = normalise(artist)
    film = normalise(film)
    if not title:
        return {}

    kind = repertoire_of(repertoire)

    asked = f"Song: {title}"
    if (artist or "").strip():
        asked += f"\nArtist or composer: {artist.strip()}"
    if (film or "").strip():
        asked += f"\nFilm: {film.strip()}"
    if kind:
        asked += f"\nRepertoire: {kind['label']} ({kind['tradition']})"

    raw = _chat_or_default(chat).invoke([
        SystemMessage(content=CONTEXT_PROMPT),
        HumanMessage(content=asked),
    ]).content

    try:
        parsed = _strip_lyrics(_loads(raw))
    except (json.JSONDecodeError, ValueError, AttributeError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    performers = parsed.get("performers")
    if not isinstance(performers, list):
        performers = []

    return {
        "title": _clean_text(parsed.get("title")) or title,
        "original": _clean_text(parsed.get("original")),
        "film": _clean_text(parsed.get("film")) or ((film or "").strip() or None),
        "composer": _clean_text(parsed.get("composer")),
        "lyricist": _clean_text(parsed.get("lyricist")),
        "performers": [_clean_text(p, 60) for p in performers[:3] if _clean_text(p, 60)],
        "tradition": _clean_text(parsed.get("tradition"), 60)
                     or (kind["tradition"] if kind else None),
        "language": _clean_text(parsed.get("language"), 40)
                    or (kind["language"] if kind else None),
        "parjaay": _clean_text(parsed.get("parjaay"), 40),
        "raga": _clean_text(parsed.get("raga"), 60),
        "taal": _clean_text(parsed.get("taal"), 40),
        "laya": _clean_text(parsed.get("laya"), 40),
        "year": _number(parsed.get("year")),
        "confidence": _confidence(parsed.get("confidence")),
        "note": _clean_text(parsed.get("note"), 300) or "",
        "verified": False,
    }


def _titles_from_model(subject, kind, repertoire=None, chat=None):
    """Songs of a film, or by a person, as titles only."""
    from langchain_core.messages import HumanMessage, SystemMessage

    subject = normalise(subject)
    entry = repertoire_of(repertoire)

    asked = ("Film: " if kind == "film" else "Composer or singer: ") + subject
    if entry:
        asked += f"\nRepertoire: {entry['label']} ({entry['tradition']})"

    raw = _chat_or_default(chat).invoke([
        SystemMessage(content=LIST_PROMPT),
        HumanMessage(content=asked),
    ]).content

    try:
        parsed = _loads(raw)
    except (json.JSONDecodeError, ValueError, AttributeError):
        return {"songs": [], "confidence": 0.0,
                "note": "The model did not return a usable list."}

    if not isinstance(parsed, dict):
        return {"songs": [], "confidence": 0.0, "note": ""}

    songs = []
    for item in (parsed.get("songs") or [])[:MAX_TITLES]:
        if not isinstance(item, dict):
            continue
        _strip_lyrics(item)

        title = _clean_text(item.get("title"))
        if not title:
            continue

        performers = item.get("performers")
        if not isinstance(performers, list):
            performers = []

        songs.append({
            "title": title,
            "original": _clean_text(item.get("original")),
            "performers": [_clean_text(p, 60) for p in performers[:2]
                           if _clean_text(p, 60)],
            "composer": _clean_text(item.get("composer")),
            "year": _number(item.get("year")),
            "album": None,
            "source": "model",
            "verified": False,
        })

    return {
        "subject": _clean_text(parsed.get("subject")) or subject,
        "songs": songs,
        "confidence": _confidence(parsed.get("confidence")),
        "note": _clean_text(parsed.get("note"), 300) or "",
    }


def _merge_titles(catalogue, suggested):
    """Catalogue entries first: those already have lyrics behind them."""
    out, seen = [], set()

    for row in catalogue:
        key = row["title"].strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "title": row["title"],
            "original": None,
            "performers": [row["artist"]] if row["artist"] else [],
            "composer": None,
            "year": None,
            "album": row["album"],
            "source": "LRCLIB",
            "verified": True,     # a real catalogue entry, with lyrics behind it
        })

    for row in suggested:
        key = (row.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)

    return out


# --- the three doors -----------------------------------------------------

def lookup(title, artist="", film="", repertoire=None, chat=None):
    """One song: real lyrics, plus unverified context."""
    title = normalise(title)
    artist = normalise(artist)
    film = normalise(film)

    candidates = find_lyrics(title, artist, film)

    try:
        context = describe_song(title, artist, film, repertoire, chat=chat)
    except Exception as exc:  # noqa: BLE001 - context is optional, lyrics are not
        context = {"error": f"{type(exc).__name__}: {exc}"}

    entry = repertoire_of(repertoire)

    if candidates:
        notice = (
            f"{len(candidates)} lyric source"
            f"{'' if len(candidates) == 1 else 's'} found. Check the words "
            "against a copy you trust before reading: the catalogue is "
            "crowdsourced, so a version can be partial or misattributed."
        )
    else:
        notice = "No lyrics found. " + (
            entry["hint"] if entry
            else "Paste the lyrics instead; the context below may still save typing."
        )

    return {
        "mode": "song",
        "query": {"title": title, "artist": artist, "film": film,
                  "repertoire": repertoire},
        "candidates": candidates,
        "context": context,
        "notice": notice,
        "lyrics_source": "LRCLIB",
    }


def _catalogue_for(subject, kind):
    """Several searches, because one exact match finds very little.

    An album search only matches when the soundtrack is filed under exactly
    that name, which for film music it often is not: it may carry a year, or
    "Original Motion Picture Soundtrack", or the songs may be filed under the
    singer with no album at all.
    """
    rows = []
    if kind == "film":
        rows += search_tracks(album=subject)
        rows += search_tracks(query=subject)
        rows += search_tracks(query=subject + " soundtrack")
    else:
        rows += search_tracks(artist=subject)
        rows += search_tracks(query=subject)

    keep, seen = [], set()
    for row in rows:
        key = row["id"] or (row["title"].lower(), row["artist"].lower())
        if key in seen:
            continue

        haystack = " ".join([row["album"], row["artist"], row["title"]])
        if not _close_enough(subject, haystack):
            continue

        seen.add(key)
        keep.append(row)

    return keep


def _listing(subject, kind, repertoire, chat, empty_notice):
    catalogue = _catalogue_for(subject, kind)

    try:
        suggested = _titles_from_model(subject, kind, repertoire, chat=chat)
    except Exception as exc:  # noqa: BLE001
        suggested = {"songs": [], "confidence": 0.0,
                     "note": f"{type(exc).__name__}: {exc}"}

    # The model usually knows the full title behind a partial or mis-spelled
    # one: "Rani Ki Prem Kahanis" comes back as "Rocky Aur Rani Kii Prem
    # Kahaani". The catalogue is worth asking again under that name, since
    # that is how the soundtrack will be filed.
    corrected = normalise(suggested.get("subject") or "")
    if corrected and _tokens(corrected) != _tokens(subject):
        extra = _catalogue_for(corrected, kind)
        known = {row["id"] or (row["title"].lower(), row["artist"].lower())
                 for row in catalogue}
        catalogue += [
            row for row in extra
            if (row["id"] or (row["title"].lower(), row["artist"].lower())) not in known
        ]

    songs = _merge_titles(catalogue, suggested.get("songs") or [])
    ready = len([song for song in songs if song["source"] == "LRCLIB"])

    return {
        "mode": kind,
        "query": {"subject": subject, "repertoire": repertoire},
        "subject": corrected or suggested.get("subject") or subject,
        "songs": songs,
        "confidence": suggested.get("confidence", 0.0),
        "note": suggested.get("note", ""),
        "notice": (
            f"{len(songs)} song{'' if len(songs) == 1 else 's'}, "
            f"{ready} with lyrics already in the catalogue. Titles marked "
            "unverified come from the model and may be wrong; choosing one "
            "searches for its lyrics."
            if songs else empty_notice
        ),
        "maybe_too_recent": not songs,
    }


def by_film(film, repertoire=None, chat=None):
    """Everything in a film, to choose from."""
    film = normalise(film)
    if not film:
        return {"mode": "film", "songs": [], "notice": "Name a film first."}

    return _listing(
        film, "film", repertoire, chat,
        "Nothing found for that film. Either the soundtrack is not in the "
        "catalogue, or the film is newer than the model's training data, "
        "which is common for anything released in the last year or two. "
        "Search by song name instead, or paste the lyrics.",
    )


def by_person(person, repertoire=None, chat=None):
    """Everything by a composer or singer, to choose from."""
    person = normalise(person)
    if not person:
        return {"mode": "person", "songs": [],
                "notice": "Name a composer or singer first."}

    return _listing(
        person, "person", repertoire, chat,
        "Nothing found for that name. A singer usually finds more than a "
        "composer, because the catalogue is built from recordings. Recent "
        "work may also be newer than the model's training data.",
    )