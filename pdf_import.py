"""
import_pdf.py - seed the system from a PDF of songs that already carry labels.

    python import_pdf.py songs.pdf --editor "Sen 2019 corpus"
    python import_pdf.py songs.pdf --editor "Sen 2019 corpus" --dry-run

This does not fine-tune anything. Gemini's weights are not yours to change,
and a few hundred examples would be far too few to train on anyway. What it
does is faster and more useful here: every labelled song in the PDF becomes a
row in the same corrections table your experts write to, embedded the same
way, so it guides new readings through the machinery that already exists.

The rows are attributed to whatever --editor you give, so the corpus appears
in the Follow menu beside your human reviewers. You can follow it alone,
exclude it, rank it below a person, or retire the whole batch from the review
log if it turns out to be poor. That is the point of importing it this way
rather than pasting it into the prompt: it stays inspectable and reversible.

Nothing is deleted. Existing corrections are untouched.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

try:                        # filenames differ in case between machines
    import database
    import learning
except ImportError:         # pragma: no cover
    import database as database
    import learning as learning

# How much of the PDF to hand the model at once. Small enough that a song is
# rarely split across two requests, large enough to keep the call count down.
CHUNK = 6000

SYSTEM_PROMPT = """You are reading a document that contains song lyrics together \
with emotion labels someone has already assigned to them, and sometimes notes \
explaining the labelling.

Find every song in the text you are given and return it. For each one:

  title            the song's title if the document gives one, else ""
  lyrics           the lyrics as printed, as fully as the text provides
  primary_emotion  the emotion label the document assigns. Keep the document's
                   own wording, including Bengali, Hindi or Urdu terms. If it
                   gives several, join them with "/"
  valence          minus one to one if the document states or clearly implies it,
                   otherwise null
  arousal          minus one to one on the same terms, otherwise null
  note             the document's own reasoning for the label, in one or two
                   sentences, quoting its wording where it explains itself. ""
                   when it offers no reasoning

Take the labels from the document. Do not correct them, do not substitute your
own reading, and do not invent a label for a song the document leaves unlabelled
-- skip that song instead. Ignore headers, page numbers, references and any
other matter that is not a song.

Return ONLY a raw JSON array, no markdown fences and no commentary:

[{"title": "", "lyrics": "", "primary_emotion": "", "valence": null,
  "arousal": null, "note": ""}]

Return [] when the text you are given holds no labelled song."""


# --- reading -------------------------------------------------------------

def read_pdf(path):
    from langchain_community.document_loaders import PyPDFLoader
    pages = PyPDFLoader(path).load()
    return "\n\n".join(page.page_content for page in pages)


def chunks(text, size=CHUNK):
    """Split on blank lines so a song is rarely cut in half."""
    out, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) > size and current:
            out.append(current)
            current = block
        else:
            current = current + "\n\n" + block if current else block
    if current.strip():
        out.append(current)
    return out


def _loads(raw):
    if isinstance(raw, list):
        raw = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in raw
        )
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) > 1:
            cleaned = parts[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    return json.loads(cleaned.strip("` \n"))


def extract(text):
    """Ask the model to pull the labelled songs out of one chunk."""
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    model = init_chat_model(
        "gemini-3.1-flash-lite-preview",
        model_provider="google_genai",
        temperature=0.0,
    )
    raw = model.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=text),
    ]).content

    try:
        found = _loads(raw)
    except (json.JSONDecodeError, ValueError, AttributeError):
        return []
    return found if isinstance(found, list) else []


# --- writing -------------------------------------------------------------

def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else max(-1.0, min(1.0, round(out, 2)))


def _quadrant(valence, arousal):
    if valence >= 0:
        return "Q1" if arousal >= 0 else "Q4"
    return "Q2" if arousal >= 0 else "Q3"


def compose(song):
    """Same shape app.py sends the analyser, so fingerprints line up."""
    lyrics = (song.get("lyrics") or "").strip()
    title = (song.get("title") or "").strip()
    if not title:
        return lyrics
    return lyrics + "\n\n---\nSupplied alongside the lyrics:\nTitle: " + title


def store(song, editor, source):
    """One labelled song becomes an analysis plus the correction of it."""
    text = compose(song)
    label = (song.get("primary_emotion") or "").strip()
    if not text or not label:
        return None

    valence = _number(song.get("valence"))
    arousal = _number(song.get("arousal"))

    corrected = {"primary_emotion": label, "edited": True}
    if valence is not None:
        corrected["valence"] = valence
    if arousal is not None:
        corrected["arousal"] = arousal
    if valence is not None and arousal is not None:
        corrected["quadrant"] = _quadrant(valence, arousal)

    note = (song.get("note") or "").strip()
    note = (note + " " if note else "") + "Imported from " + source + "."

    # The analysis carries an empty verdict, so the correction reads as
    # "nothing was said, the source says this" rather than pretending the
    # model had produced a reading that was then corrected.
    analysis_id = database.save_analysis(text, {}, source="import")

    database.save_correction(
        analysis_id=analysis_id,
        original={},
        corrected=corrected,
        changed=learning.changes({}, corrected),
        editor=editor,
        note=note,
        embedding=learning.embed(text),
    )
    return corrected


# --- entry point ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="the PDF to read")
    parser.add_argument("--editor", required=True,
                        help="name these rows are attributed to, e.g. 'Sen 2019 corpus'")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be imported without writing anything")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many songs")
    args = parser.parse_args()

    print(f"Reading {args.pdf}")
    text = read_pdf(args.pdf)
    pieces = chunks(text)
    print(f"  {len(text):,} characters in {len(pieces)} chunk(s)\n")

    database.init()

    written, skipped = 0, 0
    for number, piece in enumerate(pieces, start=1):
        songs = extract(piece)
        print(f"chunk {number}/{len(pieces)}: {len(songs)} labelled song(s)")

        for song in songs:
            if args.limit and written >= args.limit:
                break

            title = (song.get("title") or "untitled").strip()
            label = (song.get("primary_emotion") or "").strip()

            if not label or not (song.get("lyrics") or "").strip():
                skipped += 1
                print(f"    skipped {title}: no label or no lyrics")
                continue

            if args.dry_run:
                print(f"    would import {title} -> {label}")
                written += 1
                continue

            store(song, args.editor, args.pdf)
            written += 1
            print(f"    imported {title} -> {label}")

        if args.limit and written >= args.limit:
            break

    print(f"\n{'would import' if args.dry_run else 'imported'} {written} song(s), "
          f"skipped {skipped}")
    if not args.dry_run:
        print("now on file:", database.stats())
        print(f"\nThese rows appear in the Follow menu as '{args.editor}'. "
              f"Retire them from the review log if they turn out to be poor.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nstopped")