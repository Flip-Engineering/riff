"""A producer's next take uses the studio's generation contract."""
import math

import model_options
from studio_core import validate_recipe

FIELDS = ("title", "lyrics", "style", "abc", "mode", "cot", "max_seconds",
          "steps", "cfg_scale", "temperature", "seed", "refinement")


def symbolic_context(source):
    plan = source.get("symbolic_plan") or {}
    return {"supplied_abc": source.get("abc", ""),
            "generated_abc": plan.get("abc", ""),
            "generated_plan_truncated": bool(plan.get("truncated", False)),
            "generated_plan_token_count": plan.get("token_count")}


def response_schema(source, keep_lyrics):
    refinement = {}
    for option in model_options.schema():
        field = {"type": "integer" if option["integer"] else "number",
                 "description": f"{option['label']}. Runtime default: {option['default']}."}
        field["exclusiveMinimum" if option["exclusive_min"] else "minimum"] = option["min"]
        if option["max"] is not None:
            field["maximum"] = option["max"]
        refinement[option["key"]] = field
    properties = {
        "title": {"type": "string", "description": "Title of the recommended take."},
        "lyrics": {"type": "string", "description": "The complete lyric input, or empty for a wordless take."},
        "style": {"type": "string", "description": "The actual YuE2 musical direction, ready to generate."},
        "abc": {"type": "string", "description": "Complete ABC score to condition the next take, or empty to let YuE2 compose."},
        "mode": {"type": "string", "enum": ["free", "instrumental", "lyrics", "surprise"]},
        "cot": {"type": "string", "enum": ["off", "melody", "full"],
                "description": "YuE2 planning: off, melody, or melody and chords. A supplied ABC score needs melody or full."},
        "max_seconds": {"type": "number", "minimum": 1 / model_options.TOKEN_RATE,
                        "description": "Requested duration limit in seconds. Keep the artist's preview scope."},
        "steps": {"type": "integer", "minimum": 1, "description": "Acoustic solver steps."},
        "cfg_scale": {"type": "number", "minimum": 0, "maximum": 20,
                      "description": "Semantic classifier-free guidance for music-token generation. A value of 1 uses a single conditioning branch."},
        "temperature": {"type": "number", "minimum": 0, "maximum": 5, "description": "Semantic sampling temperature."},
        "seed": {"type": "string", "pattern": "^[0-9]*$",
                 "description": "Integer seed from 0 through 9223372036854775807, or empty for a new seed."},
        "refinement": {"type": "object", "properties": refinement, "additionalProperties": False,
                       "description": "YuE2 sampling overrides. Omitted controls use runtime defaults."},
    }
    if keep_lyrics:
        properties["lyrics"]["const"] = source.get("lyrics", "")
    return {"type": "object", "additionalProperties": False,
            "required": ["notes", "summary", "generation"],
            "properties": {
                "notes": {"type": "string", "description": "Candid listening observations, with useful timestamps."},
                "summary": {"type": "string", "description": "Brief musical intent of this recommended take."},
                "generation": {"type": "object", "properties": properties,
                               "required": list(FIELDS), "additionalProperties": False}}}


def recommended_generation(value, source, keep_lyrics):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("The review did not return a complete generation recipe. Review again to retry.")
    for key in ("title", "lyrics", "style", "abc", "mode", "cot", "seed"):
        if not isinstance(value[key], str):
            raise ValueError(f"The recommended {key} must be text.")
    for key in ("max_seconds", "cfg_scale", "temperature"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]):
            raise ValueError(f"The recommended {key} must be a finite number.")
    if type(value["steps"]) is not int:
        raise ValueError("The recommended solver steps must be a whole number.")
    if keep_lyrics and value["lyrics"] != source.get("lyrics", ""):
        raise ValueError("The recommendation changed lyrics you chose to keep. Review again to retry.")
    # These are the same validation and defaults used by POST /api/generations.
    # Origin links come from the studio, never from the model.
    result = validate_recipe({**source, **value, "title_auto": False,
                              "parent_track_id": "", "review_id": "", "render_mode": "music",
                              "lyrics_source": "review" if value["lyrics"] else "none"})
    for key in ("parent_track_id", "review_id"):
        result.pop(key, None)
    return result
