"""
graph.py - the model draws the song, not just labels it.

analyze() gives one verdict for a whole song. That flattens a text like a Baul
song or a Tagore Prem song whose refrain reverses its verses. So this module
asks the model to walk the lyrics in order, split them into the sections the
song itself suggests, and score each one on the circumplex.

Expert mode:
- Arc points can be edited through the frontend expert controls.
- Circumplex / Path points can be edited through the frontend expert controls.
- Valence/arousal values remain the authoritative coordinates.
- Emotion distribution / weight is NEVER recalculated from expert edits.
- Expert edits are returned as `expert_edits`.
- The original model output remains available as `model_valence` /
  `model_arousal`.
- Normal rendering remains unchanged when expert mode is disabled.
- Every segment has a stable ID so frontend edits remain attached to the
  correct section.

Everything here returns plain JSON-serialisable dicts shaped as Plotly figures.
"""

import json

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()


EMOTIONS = [
    "joy",
    "love",
    "serenity",
    "devotion",
    "longing",
    "sadness",
    "fear",
    "anger",
]


SYSTEM_PROMPT = """You segment a song and score each segment for plotting on Russell's \
circumplex. You handle Bengali and South Asian repertoire as first class cases: \
Rabindrasangeet, Baul sangeet, Lalan geeti, Sufi and qawwali, Shyama sangeet, Nazrul geeti, \
kirtan and bhajan, alongside general popular song. Lyrics may be in Bengali, Devanagari or \
Perso Arabic script, in roman transliteration, or code mixed with English, and may arrive with \
notation, raga, taal, laya or parjaay metadata appended after a line of three dashes.

Split the lyrics into the sections the song itself suggests: sthayi, antara, sanchari, abhog, \
or verse, refrain, bridge, or simply consecutive stanzas. Between three and eight segments. \
Keep them in the order they appear in the text. Never reorder, never merge distant parts, and \
never invent a section that is not in the supplied text.

For every segment give:
  label     a short name for that section, two or three words, taken from its function or its
            opening image, not a number on its own
  snippet   at most six words quoted from that segment, enough to locate it
  valence   minus one to one, two decimals, minus one maximally unpleasant
  arousal   minus one to one, two decimals, minus one maximally calm
  emotion   one label from: joy, love, serenity, devotion, longing, sadness, fear, anger
  note      one short sentence, plain text, naming the word or image that set those numbers

Then give distribution, the emotional weight of the song as a whole across all eight labels, \
each between zero and one with two decimals, summing to 1.00.

Then give caption, two or three plain sentences describing the shape of the arc: where it \
moves and what moves it. Name any point where the surface words and the underlying meaning \
diverge, which is common where separation from the divine is written in the vocabulary of \
loss. Read metaphor as metaphor and do not score images literally. Where notation, raga or \
taal is supplied, let it inform arousal but let the words lead, and say so if they conflict.

You are scoring perceived emotion, the emotion the song expresses, not the emotion a listener \
would feel. Judge only from the supplied text. Do not recall the rest of a song you think you \
recognise. If the input is a fragment, score the fragment and say so in the caption.

Write plain text in label, snippet, note and caption. No markdown, no asterisks, no bullets.

Return ONLY a raw JSON object, no markdown fences and no commentary, in exactly this shape:

{
  "segments": [
    {"label": "", "snippet": "", "valence": 0.0, "arousal": 0.0, "emotion": "", "note": ""}
  ],
  "distribution": {"joy": 0.0, "love": 0.0, "serenity": 0.0, "devotion": 0.0,
                   "longing": 0.0, "sadness": 0.0, "fear": 0.0, "anger": 0.0},
  "caption": ""
}
"""


model = init_chat_model(
    "gemini-3.1-flash-lite-preview",
    model_provider="google_genai",
    temperature=0.2,
)


# -------------------------------------------------------------------------
# Palette
# -------------------------------------------------------------------------

TEXT = "#eeeae2"
SOFT = "#c6cad1"
MUTED = "#8992a1"
GRID = "rgba(137,146,161,0.14)"
AXIS = "rgba(137,146,161,0.32)"
SURFACE = "#151a22"
LINE = "#272e39"

GOLD = "#d8ad55"
TEAL = "#69a99e"
VIOLET = "#8c81c8"

SANS = '"DM Sans","Noto Sans Bengali",system-ui,sans-serif'
SERIF = "Newsreader,Georgia,serif"


EMOTION_COLOR = {
    "love": "#c98293",
    "joy": "#d8ad55",
    "devotion": "#8c81c8",
    "longing": "#7e8fd6",
    "sadness": "#668fc0",
    "serenity": "#69a99e",
    "anger": "#c86c69",
    "fear": "#c07a3f",
}


QUADRANT_TINT = {
    "Q1": "rgba(216,173,85,.055)",
    "Q2": "rgba(200,108,105,.055)",
    "Q3": "rgba(102,143,192,.055)",
    "Q4": "rgba(105,169,158,.055)",
}


QUADRANT_NAME = {
    "Q1": "bright, rising",
    "Q2": "tense, agitated",
    "Q3": "subdued, heavy",
    "Q4": "calm, settled",
}


# -------------------------------------------------------------------------
# Parsing
# -------------------------------------------------------------------------

def _text_of(raw):
    if isinstance(raw, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw
        )

    return raw or ""


def _loads(raw):
    """Strip markdown fences and parse model JSON."""

    cleaned = _text_of(raw).strip()

    if cleaned.startswith("```"):
        parts = cleaned.split("```")

        if len(parts) > 1:
            cleaned = parts[1]

        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]

    cleaned = cleaned.strip("` \n")

    return json.loads(cleaned)


def _num(value, low=-1.0, high=1.0, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default

    if out != out:
        return default

    return max(low, min(high, out))


def _quadrant(valence, arousal):
    if valence >= 0:
        return "Q1" if arousal >= 0 else "Q4"

    return "Q2" if arousal >= 0 else "Q3"


def _clean_segments(raw_segments):
    out = []

    for i, seg in enumerate(raw_segments or []):
        if not isinstance(seg, dict):
            continue

        valence = _num(seg.get("valence"))
        arousal = _num(seg.get("arousal"))

        emotion = (
            str(seg.get("emotion") or "")
            .strip()
            .lower()
            .replace(" ", "_")
        )

        if emotion not in EMOTION_COLOR:
            emotion = ""

        label = (
            str(seg.get("label") or "").strip()
            or f"Part {i + 1}"
        )

        snippet = str(seg.get("snippet") or "").strip()
        note = str(seg.get("note") or "").strip()

        out.append({
            # Stable frontend/backend identity.
            "id": f"segment-{i + 1}",

            "label": label,
            "snippet": snippet,

            # ---------------------------------------------------------
            # Original model coordinates.
            # These NEVER change after the model has spoken.
            # ---------------------------------------------------------
            "model_valence": round(valence, 2),
            "model_arousal": round(arousal, 2),

            # ---------------------------------------------------------
            # Active coordinates.
            #
            # These are the coordinates used by Arc + Path.
            # They initially equal the model coordinates and may later
            # be changed by an expert.
            # ---------------------------------------------------------
            "valence": round(valence, 2),
            "arousal": round(arousal, 2),

            "emotion": emotion,
            "note": note,

            "quadrant": _quadrant(
                valence,
                arousal,
            ),

            "expert_edited": False,
        })

    return out


def _clean_distribution(raw_distribution):
    """
    Clean the model-generated emotional weight distribution.

    IMPORTANT:
    This distribution is independent from expert Valence/Arousal edits.
    """

    values = {
        key: _num(
            (raw_distribution or {}).get(key),
            0.0,
            1.0,
        )
        for key in EMOTIONS
    }

    total = sum(values.values())

    if total <= 0:
        return {
            key: round(
                1.0 / len(EMOTIONS),
                2,
            )
            for key in EMOTIONS
        }

    return {
        key: round(
            value / total,
            3,
        )
        for key, value in values.items()
    }


def _from_analysis(analysis):
    """Last resort: one point and a single-label profile."""

    analysis = analysis if isinstance(analysis, dict) else {}

    valence = _num(
        analysis.get("valence")
    )

    arousal = _num(
        analysis.get("arousal")
    )

    primary = (
        str(
            analysis.get("primary_emotion") or ""
        )
        .strip()
        .lower()
        .replace(" ", "_")
    )

    distribution = {
        key: 0.0
        for key in EMOTIONS
    }

    if primary in distribution:
        distribution[primary] = 1.0

    return {
        "segments": [{
            "id": "segment-1",

            "label": "Whole song",
            "snippet": "",

            "model_valence": round(
                valence,
                2,
            ),

            "model_arousal": round(
                arousal,
                2,
            ),

            "valence": round(
                valence,
                2,
            ),

            "arousal": round(
                arousal,
                2,
            ),

            "emotion": (
                primary
                if primary in EMOTION_COLOR
                else ""
            ),

            "note": str(
                analysis.get("summary") or ""
            ).strip(),

            "quadrant": _quadrant(
                valence,
                arousal,
            ),

            "expert_edited": False,
        }],

        "distribution": distribution,

        "caption": (
            "The section by section reading was unavailable, so this shows "
            "the single whole song verdict only."
        ),
    }


def _colors_for(segments):
    return [
        EMOTION_COLOR.get(
            segment["emotion"],
            VIOLET,
        )
        for segment in segments
    ]


# -------------------------------------------------------------------------
# Expert editing
# -------------------------------------------------------------------------

def _apply_expert_edits(
    segments,
    expert_edits=None,
):
    """
    Apply expert Valence/Arousal corrections.

    Supported dictionary format:

        {
            "segment-1": {
                "valence": 0.40,
                "arousal": -0.20
            }
        }

    Supported list format:

        [
            {
                "id": "segment-1",
                "valence": 0.40,
                "arousal": -0.20
            }
        ]

    ONLY Valence and Arousal are editable.

    The following are deliberately NOT changed:

        emotion
        distribution
        model_valence
        model_arousal
    """

    if not expert_edits:
        return segments

    if isinstance(
        expert_edits,
        list,
    ):
        edits = {}

        for item in expert_edits:
            if not isinstance(
                item,
                dict,
            ):
                continue

            segment_id = item.get("id")

            if segment_id:
                edits[str(segment_id)] = item

    elif isinstance(
        expert_edits,
        dict,
    ):
        edits = expert_edits

    else:
        return segments

    for segment in segments:
        segment_id = segment["id"]

        edit = edits.get(
            segment_id
        )

        if not isinstance(
            edit,
            dict,
        ):
            continue

        original_valence = segment["valence"]
        original_arousal = segment["arousal"]

        # -------------------------------------------------------------
        # Only coordinates may be changed.
        # -------------------------------------------------------------

        if "valence" in edit:
            segment["valence"] = round(
                _num(
                    edit["valence"],
                    -1.0,
                    1.0,
                ),
                2,
            )

        if "arousal" in edit:
            segment["arousal"] = round(
                _num(
                    edit["arousal"],
                    -1.0,
                    1.0,
                ),
                2,
            )

        if (
            segment["valence"] != original_valence
            or
            segment["arousal"] != original_arousal
        ):
            segment["expert_edited"] = True

        segment["quadrant"] = _quadrant(
            segment["valence"],
            segment["arousal"],
        )

    return segments


def _expert_edit_payload(segments):
    """
    Return the frontend/backend representation of expert corrections.

    Distribution / emotional weight is intentionally absent.
    """

    return [
        {
            "id": segment["id"],

            "label": segment["label"],

            "valence": segment["valence"],
            "arousal": segment["arousal"],

            "model_valence": segment["model_valence"],
            "model_arousal": segment["model_arousal"],

            "quadrant": segment["quadrant"],

            "expert_edited": segment["expert_edited"],
        }
        for segment in segments
    ]


def _customdata_for_segment(segment):
    """
    Metadata consumed by the Plotly frontend.

    Index positions are deliberately stable:

        0 = snippet
        1 = emotion
        2 = note
        3 = segment ID
        4 = original model valence
        5 = original model arousal
        6 = expert edited
        7 = active valence
        8 = active arousal
        9 = label
        10 = quadrant
    """

    return [
        segment["snippet"] or "—",
        segment["emotion"] or "unnamed",
        segment["note"],
        segment["id"],
        segment["model_valence"],
        segment["model_arousal"],
        segment["expert_edited"],
        segment["valence"],
        segment["arousal"],
        segment["label"],
        segment["quadrant"],
    ]


def _customdata_for_segments(segments):
    return [
        _customdata_for_segment(
            segment
        )
        for segment in segments
    ]


# -------------------------------------------------------------------------
# Base layout
# -------------------------------------------------------------------------

def _base_layout(
    title,
    **extra,
):
    layout = {
        "title": {
            "text": title,

            "font": {
                "family": SERIF,
                "size": 21,
                "color": TEXT,
            },

            "x": 0,
            "xanchor": "left",
            "y": 0.97,
        },

        "margin": {
            "l": 58,
            "r": 26,
            "t": 56,
            "b": 52,
        },

        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",

        "font": {
            "family": SANS,
            "color": MUTED,
            "size": 12,
        },

        "hoverlabel": {
            "align": "left",

            "bgcolor": SURFACE,
            "bordercolor": LINE,

            "font": {
                "family": SANS,
                "color": TEXT,
                "size": 12,
            },
        },

        "dragmode": "pan",

        # Keeps the figure state stable when frontend updates occur.
        "uirevision": "lyriq-expert-graph",
    }

    layout.update(extra)

    return layout


# -------------------------------------------------------------------------
# Arc
# -------------------------------------------------------------------------

def _arc_figure(
    segments,
    expert_mode=False,
):
    labels = [
        segment["label"]
        for segment in segments
    ]

    custom = _customdata_for_segments(
        segments
    )

    ids = [
        segment["id"]
        for segment in segments
    ]

    hover = (
        "<b>%{x}</b>"
        "<br>%{fullData.name} %{y:.2f}"
        "<br>reads as %{customdata[1]}"
        "<br><i>%{customdata[0]}</i>"
        "<br>%{customdata[2]}"
        "<br><span style='opacity:.7'>"
        "model: %{customdata[4]:.2f}"
        "</span>"
        "<extra></extra>"
    )

    valence_trace = {
        "type": "scatter",

        "name": "Valence",

        "mode": "lines+markers",

        "x": labels,

        "y": [
            segment["valence"]
            for segment in segments
        ],

        # Stable IDs allow the frontend to identify the exact
        # segment even if labels happen to be identical.
        "ids": ids,

        "line": {
            "color": GOLD,
            "width": 2,
            "shape": "spline",
            "smoothing": 0.7,
        },

        "marker": {
            "size": 10,
            "symbol": "circle",
            "color": GOLD,
        },

        "customdata": custom,

        "hovertemplate": hover,

        "meta": {
            "graph": "arc",
            "axis": "valence",
            "editable": bool(
                expert_mode
            ),
        },
    }

    arousal_trace = {
        "type": "scatter",

        "name": "Arousal",

        "mode": "lines+markers",

        "x": labels,

        "y": [
            segment["arousal"]
            for segment in segments
        ],

        "ids": ids,

        "line": {
            "color": TEAL,
            "width": 2,
            "shape": "spline",
            "smoothing": 0.7,
            "dash": "dot",
        },

        "marker": {
            "size": 10,
            "symbol": "diamond",
            "color": TEAL,
        },

        "customdata": custom,

        "hovertemplate": hover,

        "meta": {
            "graph": "arc",
            "axis": "arousal",
            "editable": bool(
                expert_mode
            ),
        },
    }

    return {
        "data": [
            valence_trace,
            arousal_trace,
        ],

        "layout": _base_layout(
            "How the song moves",

            xaxis={
                "showgrid": False,

                "linecolor": LINE,

                "tickfont": {
                    "size": 11,
                    "color": SOFT,
                },
            },

            yaxis={
                "range": [
                    -1.08,
                    1.08,
                ],

                "zeroline": True,
                "zerolinecolor": AXIS,
                "zerolinewidth": 1,

                "gridcolor": GRID,

                "dtick": 0.5,

                "tickformat": ".1f",
            },

            hovermode="closest",

            legend={
                "orientation": "h",
                "y": -0.2,
                "x": 0,

                "font": {
                    "color": SOFT,
                    "size": 12,
                },
            },
        ),
    }


# -------------------------------------------------------------------------
# Circumplex / Path
# -------------------------------------------------------------------------

def _circumplex_figure(
    segments,
    analysis=None,
    expert_mode=False,
):
    shapes = []
    annotations = []

    for code, (
        x0,
        x1,
        y0,
        y1,
    ) in {
        "Q1": (0, 1.1, 0, 1.1),
        "Q2": (-1.1, 0, 0, 1.1),
        "Q3": (-1.1, 0, -1.1, 0),
        "Q4": (0, 1.1, -1.1, 0),
    }.items():

        shapes.append({
            "type": "rect",

            "x0": x0,
            "x1": x1,

            "y0": y0,
            "y1": y1,

            "fillcolor": QUADRANT_TINT[code],

            "line": {
                "width": 0,
            },

            "layer": "below",
        })

        annotations.append({
            "x": (x0 + x1) / 2,

            "y": (
                y1 - 0.07
                if y1 > 0
                else y0 + 0.07
            ),

            "text": QUADRANT_NAME[code],

            "showarrow": False,

            "font": {
                "family": SANS,
                "size": 10,
                "color": MUTED,
            },
        })

    path_trace = {
        "type": "scatter",

        "name": "Path",

        "mode": "lines+markers+text",

        # -------------------------------------------------------------
        # Active expert coordinates.
        # -------------------------------------------------------------
        "x": [
            segment["valence"]
            for segment in segments
        ],

        "y": [
            segment["arousal"]
            for segment in segments
        ],

        "ids": [
            segment["id"]
            for segment in segments
        ],

        "text": [
            f"{i + 1} · {segment['label']}"
            for i, segment in enumerate(segments)
        ],

        "textposition": "top center",

        "textfont": {
            "family": SANS,
            "size": 10,
            "color": SOFT,
        },

        "line": {
            "color": "rgba(140,129,200,.55)",
            "width": 1.4,
            "shape": "spline",
            "smoothing": 0.5,
        },

        "marker": {
            "size": 14,

            "color": _colors_for(
                segments
            ),

            "line": {
                "color": "#080a0f",
                "width": 2,
            },
        },

        "customdata": _customdata_for_segments(
            segments
        ),

        "hovertemplate": (
            "<b>%{text}</b>"
            "<br>valence %{x:.2f}, arousal %{y:.2f}"
            "<br>reads as %{customdata[1]}"
            "<br><i>%{customdata[0]}</i>"
            "<br>%{customdata[2]}"
            "<br><span style='opacity:.7'>"
            "model: %{customdata[4]:.2f}, "
            "%{customdata[5]:.2f}"
            "</span>"
            "<extra></extra>"
        ),

        "meta": {
            "graph": "circumplex",
            "axis": "valence_arousal",
            "editable": bool(
                expert_mode
            ),
        },
    }

    data = [
        path_trace
    ]

    # -------------------------------------------------------------
    # Whole-song model verdict.
    #
    # This is deliberately NOT affected by expert segment edits.
    # -------------------------------------------------------------

    if (
        isinstance(
            analysis,
            dict,
        )
        and analysis.get("valence") is not None
    ):
        data.append({
            "type": "scatter",

            "name": "Whole song",

            "mode": "markers",

            "x": [
                _num(
                    analysis.get(
                        "valence"
                    )
                )
            ],

            "y": [
                _num(
                    analysis.get(
                        "arousal"
                    )
                )
            ],

            "marker": {
                "size": 17,
                "symbol": "star",
                "color": GOLD,

                "line": {
                    "color": "#080a0f",
                    "width": 1.5,
                },
            },

            "hovertemplate": (
                "<b>Whole song verdict</b>"
                "<br>valence %{x:.2f}, arousal %{y:.2f}"
                "<extra></extra>"
            ),

            "meta": {
                "graph": "circumplex",
                "editable": False,
                "whole_song": True,
            },
        })

    return {
        "data": data,

        "layout": _base_layout(
            "The path it takes",

            xaxis={
                "title": {
                    "text": "valence",

                    "font": {
                        "size": 11,
                    },
                },

                "range": [
                    -1.12,
                    1.12,
                ],

                "zeroline": True,
                "zerolinecolor": AXIS,

                "gridcolor": "rgba(0,0,0,0)",

                "dtick": 0.5,

                "tickfont": {
                    "size": 10,
                },
            },

            yaxis={
                "title": {
                    "text": "arousal",

                    "font": {
                        "size": 11,
                    },
                },

                "range": [
                    -1.12,
                    1.12,
                ],

                "zeroline": True,
                "zerolinecolor": AXIS,

                "gridcolor": "rgba(0,0,0,0)",

                "dtick": 0.5,

                "tickfont": {
                    "size": 10,
                },

                "scaleanchor": "x",
                "scaleratio": 1,
            },

            shapes=shapes,

            annotations=annotations,

            showlegend=False,

            hovermode="closest",
        ),
    }


# -------------------------------------------------------------------------
# Profile / emotional weight
# -------------------------------------------------------------------------

def _profile_figure(distribution):
    """
    IMPORTANT:
    This figure uses ONLY the original model distribution.

    Expert Valence/Arousal corrections cannot modify this figure.
    """

    ordered = sorted(
        distribution.items(),
        key=lambda kv: kv[1],
    )

    return {
        "data": [{
            "type": "bar",

            "orientation": "h",

            "x": [
                round(
                    v,
                    3,
                )
                for _, v in ordered
            ],

            "y": [
                k
                for k, _ in ordered
            ],

            "marker": {
                "color": [
                    EMOTION_COLOR.get(
                        k,
                        VIOLET,
                    )
                    for k, _ in ordered
                ],

                "opacity": 0.88,

                "line": {
                    "width": 0,
                },
            },

            "hovertemplate": (
                "%{y} · %{x:.0%} of the weight"
                "<extra></extra>"
            ),

            "meta": {
                "graph": "profile",
                "editable": False,
                "weight_source": "model_distribution",
            },
        }],

        "layout": _base_layout(
            "Where the weight sits",

            xaxis={
                "range": [
                    0,

                    max(
                        list(
                            distribution.values()
                        )
                        + [0.1]
                    ) * 1.15,
                ],

                "tickformat": ".0%",

                "gridcolor": GRID,

                "zeroline": False,

                "tickfont": {
                    "size": 10,
                },
            },

            yaxis={
                "showgrid": False,

                "tickfont": {
                    "size": 12,
                    "color": SOFT,
                },
            },

            margin={
                "l": 92,
                "r": 26,
                "t": 56,
                "b": 44,
            },

            showlegend=False,

            bargap=0.42,
        ),
    }


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------

def build_graph(
    text: str,
    analysis: dict | None = None,
    expert_edits: dict | list | None = None,
    expert_mode: bool = False,
) -> dict:
    """
    Score a song section by section and return three ready-to-draw figures.

    Parameters
    ----------
    text:
        Lyrics / song text.

    analysis:
        Existing whole-song analysis used as fallback and for the
        whole-song circumplex marker.

    expert_edits:
        Optional expert corrections.

    expert_mode:
        Enables expert metadata and frontend editing support.

    IMPORTANT:
        Expert edits change ONLY Valence/Arousal coordinates.

        The following remain untouched:

            distribution
            emotion labels
            model_valence
            model_arousal
    """

    text = (text or "").strip()

    if not text:
        raise ValueError(
            "No lyrics to plot."
        )

    # ------------------------------------------------------------------
    # Model scoring
    # ------------------------------------------------------------------

    raw = model.invoke([
        SystemMessage(
            content=SYSTEM_PROMPT
        ),

        HumanMessage(
            content=(
                "Segment and score this song:\n\n"
                f"{text}"
            )
        ),
    ]).content

    try:
        payload = _loads(raw)

        segments = _clean_segments(
            payload.get("segments")
        )

        if not segments:
            raise ValueError(
                "no usable segments"
            )

        distribution = _clean_distribution(
            payload.get("distribution")
        )

        caption = str(
            payload.get("caption") or ""
        ).strip()

    except (
        json.JSONDecodeError,
        ValueError,
        AttributeError,
    ):
        fallback = _from_analysis(
            analysis
        )

        segments = fallback["segments"]

        distribution = fallback[
            "distribution"
        ]

        caption = fallback[
            "caption"
        ]

    # ------------------------------------------------------------------
    # Expert corrections.
    #
    # This operates AFTER model scoring and therefore does not modify
    # the model's emotional distribution.
    # ------------------------------------------------------------------

    segments = _apply_expert_edits(
        segments,
        expert_edits,
    )

    # ------------------------------------------------------------------
    # Build figures from ACTIVE coordinates.
    #
    # Arc and Path both use:
    #
    #     segment["valence"]
    #     segment["arousal"]
    #
    # Profile uses ONLY:
    #
    #     distribution
    #
    # Therefore expert coordinate edits cannot alter Weight.
    # ------------------------------------------------------------------

    figures = {
        "arc": _arc_figure(
            segments,
            expert_mode=expert_mode,
        ),

        "circumplex": _circumplex_figure(
            segments,
            analysis,
            expert_mode=expert_mode,
        ),

        "profile": _profile_figure(
            distribution
        ),
    }

    return {
        "segments": segments,

        # --------------------------------------------------------------
        # ORIGINAL MODEL DISTRIBUTION.
        #
        # NEVER recalculated from expert coordinates.
        # --------------------------------------------------------------

        "distribution": distribution,

        "caption": caption,

        "figures": figures,

        # --------------------------------------------------------------
        # Expert state.
        # --------------------------------------------------------------

        "expert_edits": _expert_edit_payload(
            segments
        ),

        "expert_mode": bool(
            expert_mode
        ),

        # --------------------------------------------------------------
        # Frontend configuration.
        #
        # IMPORTANT:
        # Plotly's generic `editable` setting is NOT being relied upon
        # to magically drag scatter points. The frontend should use
        # click + expert controls / sliders to change coordinates.
        #
        # The metadata above provides the segment identity required
        # for those controls.
        # --------------------------------------------------------------

        "config": {
            "responsive": True,

            "displaylogo": False,

            "scrollZoom": False,

            "modeBarButtonsToRemove": [
                "select2d",
                "lasso2d",
                "autoScale2d",
                "toggleSpikelines",
                "hoverClosestCartesian",
                "hoverCompareCartesian",
            ],

            "editable": False,

            "edits": {
                "x": False,
                "y": False,

                "annotationPosition": False,
                "legendPosition": False,
                "titleText": False,
                "shapePosition": False,
            },
        },
    }