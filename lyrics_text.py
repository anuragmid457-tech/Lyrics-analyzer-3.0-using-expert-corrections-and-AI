"""
Lyrics-based Music Emotion Recognition (MER)
Supports English + Bengali/Bangla traditions: Rabindrasangeet, Baul, Lalan Geeti, Sufi.
"""

import json
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

SYSTEM_PROMPT = """You are an expert system for Music Emotion Recognition (MER) from lyrics and \
symbolic music representations. You analyse the emotional content of a song from its text alone.
Do not act too much  dissent while judging your emotion. If the content is explicitly sexual, then say that without any hesitation. 
If the song emotion is like "you do not sugarcoat anything, just say the truth about the actual song emotion" 

INPUT you may receive:
- Lyrics in English, Bengali (Bangla script), or romanised Bengali transliteration.
- Traditional repertoire: Rabindrasangeet, Baul Sangeet, Lalan Geeti, Sufi/qawwali poetry, Nazrul Geeti.
- Optionally swaralipi / notation text (sa re ga ma, raga name, taal, tempo markings) alongside the lyrics.
  If notation or raga/taal information is present, use it as a secondary cue for arousal and mood,
  but the lyrics remain the primary evidence.

ANNOTATION FRAMEWORK (follow all three layers):

1. Dimensional — Russell's circumplex model.
   valence: float in [-1.0, 1.0]  (negative = unpleasant, positive = pleasant)
   arousal: float in [-1.0, 1.0]  (negative = calm/low energy, positive = excited/high energy)
   quadrant: one of
     "Q1 - happy / excited/passionate"   (high valence, high arousal)
     "Q2 - angry / tense"     (low valence, high arousal)
     "Q3 - sad / depressed"   (low valence, low arousal)
     "Q4 - calm / relaxed"    (high valence, low arousal)

2. Categorical — give TWO labels:
   primary_emotion: your own words for the exact vibe. One to three terms joined
     with "/" when a single word will not do. 
        Where a reviewer vocabulary is supplied below the lyrics, prefer its terms
   when one of them names this song's feeling more exactly than an English label.
      emotion_indices: an object with one entry per term in primary_emotion,
     each a float in [0.0, 1.0] for how strongly the song expresses that term
     on its own. For "masti/romance/sensual" give all three, e.g.
     {"masti": 0.85, "romance": 0.60, "sensual": 0.35}. Judge each separately:
     a song can be heavy on masti and light on sensual.
   canonical_emotion: the same reading mapped onto this closed set —
     joy, love, longing, sadness, grief, nostalgia, anger, fear, anxiety, peace,
     devotion, spiritual_yearning, hope, patriotism, playfulness, loneliness,
     acceptance, wonder. One to three of these joined with "/", strongest first,
     and nothing outside the set.
   Then list 1-3 secondary_emotions from the closed set.

3. Cultural — where the text belongs to a South Asian or Sufi tradition, add the closest
   classical rasa (shringara, karuna, shanta, bhakti, veera, adbhuta, hasya, raudra, bhayanaka,
   bibhatsa) and, for Rabindrasangeet, the likely parjaay (Puja, Prem, Prakriti, Swadesh,
   Anushthanik, Bichitro). Use null when it does not apply.

RULES:
- Judge ONLY from the supplied text. Never guess from the song's fame, artist, or your own memory of it.
- Quote 2-4 short evidence phrases from the lyrics that drove your decision, with a literal gloss if not English.
- Mystical/Baul/Sufi texts often combine sorrow with devotional serenity. Say so via mixed_emotion
  rather than flattening it to a single label.
- Metaphor matters more than surface words. "Fire", "burning", "death" in Sufi verse are usually
  spiritual longing, not anger or fear.
- Give a calibrated confidence in [0.0, 1.0]. Short, fragmentary, or ambiguous input must score low.
- Do not moralise, do not add advice, do not comment on the song's quality.

APPLICATION TAGS:
- music_therapy: suggest a use such as "mood induction - calming", "grief processing",
  "energising / activation", "reminiscence therapy", or "not recommended for low-mood clients".
- recommendation_tags: 3-6 lowercase retrieval tags for a recommender system (e.g. "rainy-day",
  "late-night", "devotional", "heartbreak", "festive").

OUTPUT:
Return ONLY a raw JSON object, no markdown fences, no commentary, in exactly this shape:

{
  "language": "",
  "tradition": "",
  "valence": 0.0,
  "arousal": 0.0,
  "quadrant": "",
  "primary_emotion": "",
  "emotion_indices": {},
  "secondary_emotions": [],
  "mixed_emotion": false,
  "rasa": null,
  "parjaay": null,
  "evidence": [{"phrase": "", "gloss": "", "why": ""}],
  "summary": "",
  "confidence": 0.0,
  "music_therapy": "",
  "recommendation_tags": []
}
"""

model = init_chat_model(
    "gemini-3.1-flash-lite-preview",
    model_provider="google_genai",
    temperature=0.2,
)


def analyze(lyrics: str) :
    """Send one song to the model and parse the JSON verdict."""
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Analyse the emotion of this song:\n\n{lyrics}"),
    ]
    raw = model.invoke(messages).content
    if isinstance(raw, list):
        raw = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw
            )
    

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip("` \n")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw_output": raw}


def report(r: dict) -> None:
    if r.get("parse_error"):
        print("\n[!] Model did not return valid JSON:\n")
        print(r["raw_output"])
        return

    print("\n" + "=" * 58)
    print(f"  Language   : {r.get('language')}   |  Tradition: {r.get('tradition')}")
    print(f"  Emotion    : {r.get('primary_emotion')}  "
          f"(also: {', '.join(r.get('secondary_emotions', [])) or '-'})")
    print(f"  Valence    : {r.get('valence')}   Arousal: {r.get('arousal')}")
    print(f"  Quadrant   : {r.get('quadrant')}")
    print(f"  Rasa       : {r.get('rasa')}   |  Parjaay: {r.get('parjaay')}")
    print(f"  Mixed      : {r.get('mixed_emotion')}   Confidence: {r.get('confidence')}")
    print("-" * 58)
    print(f"  {r.get('summary')}")
    print("-" * 58)
    for e in r.get("evidence", []):
        gloss = f" ({e.get('gloss')})" if e.get("gloss") else ""
        print(f"  \u2022 \"{e.get('phrase')}\"{gloss} -> {e.get('why')}")
    print("-" * 58)
    print(f"  Therapy use: {r.get('music_therapy')}")
    print(f"  Tags       : {', '.join(r.get('recommendation_tags', []))}")
    print("=" * 58)


def read_lyrics() -> str | None:
    print("\nPaste the lyrics / swaralipi.")
    print("Type END on a new line to analyse, or QUIT to exit.")
    lines = []
    while True:
        line = input()
        cmd = line.strip().upper()
        if cmd == "END":
            return "\n".join(lines).strip()
        if cmd in ("QUIT", "EXIT"):
            return None
        lines.append(line)


if __name__ == "__main__":
    print("Music Emotion Recognition from Lyrics")
    while True:
        lyrics = read_lyrics()
        if lyrics is None:
            print("Bye.")
            break
        if not lyrics:
            print("Empty input, try again.")
            continue
        report(analyze(lyrics))