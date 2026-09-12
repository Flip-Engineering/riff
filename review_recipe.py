"""A producer's next take uses the studio's generation contract."""
import math

import model_options
from studio_core import validate_recipe

FIELDS = ("title", "lyrics", "style", "abc", "mode", "cot", "max_seconds",
          "steps", "cfg_scale", "temperature", "seed", "refinement", "performance_source", "render_mode")


def generation_context(source):
    keys = (*FIELDS, "brief", "idea_engine", "writer_tokens", "lyrics_source", "energy", "texture", "theme",
            "performance", "performance_track_id")
    return {key: source[key] for key in keys if key in source}


def _native_symbolic_context(source):
    plan = source.get("symbolic_plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    return {"supplied_abc": source.get("abc", ""),
            "generated_abc": plan.get("abc", ""),
            "generated_plan_truncated": bool(plan.get("truncated", False)),
            "generated_plan_token_count": plan.get("token_count")}


def symbolic_context(source):
    context = _native_symbolic_context(source)
    arrangement = source.get("arrangement")
    if not isinstance(arrangement, dict):
        return context
    recipes = arrangement.get("source_recipes") or {}
    if isinstance(recipes, list):
        recipes = dict(zip(arrangement.get("sources") or [], recipes))
    if not isinstance(recipes, dict):
        recipes = {}
    scores = arrangement.get("source_scores") or {}
    if not isinstance(scores, dict):
        scores = {}
    sources = []
    for track_id in dict.fromkeys([*recipes, *scores]):
        recipe = recipes.get(track_id)
        recipe = recipe if isinstance(recipe, dict) else {}
        symbolic = _native_symbolic_context(recipe)
        if not (symbolic["supplied_abc"] or symbolic["generated_abc"]) and isinstance(scores.get(track_id), str):
            symbolic["source_abc"] = scores[track_id]
        sources.append({"track_id": track_id, "inputs": generation_context(recipe), "symbolic": symbolic})
    # Musical edits provide listening context; internal file paths and raw
    # processing commands are not part of the producer's generation contract.
    clip_fields = ("track_id", "timeline_start", "start", "end", "duration", "speed", "kind",
                   "strength", "oud_windows", "double_snare")
    clips = arrangement.get("clips") or []
    context["arrangement"] = {
        "sources": sources,
        "clips": [{key: clip[key] for key in clip_fields if key in clip}
                  for clip in clips if isinstance(clip, dict)] if isinstance(clips, list) else []}
    return context


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
        "performance_source": {"type": "string", "enum": [""] + ([source["performance_track_id"]] if source.get("performance_track_id") else []),
                               "description": "Empty for a fresh performance. Use the supplied track ID to re-synthesize its saved performance codes with revised acoustic conditioning, solver steps or seed. Retains phrasing and length; not suitable for new words or a new composition."},
        "render_mode": {"type": "string", "enum": ["music", "plan"],
                        "description": "music renders a take; plan composes an editable score without audio and requires cot melody or full and empty performance_source."},
    }
    if keep_lyrics:
        properties["lyrics"]["description"] = "Riff preserves the source lyrics. This field may be empty when Keep lyrics is enabled."
    return {"type": "object", "additionalProperties": False,
            "required": ["notes", "summary", "generation"],
            "properties": {
                "notes": {"type": "string", "description": "Candid listening observations, with useful timestamps."},
                "summary": {"type": "string", "description": "Brief musical intent of this recommended take."},
                "generation": {"type": "object", "properties": properties,
                               "required": list(FIELDS), "additionalProperties": False}}}


def recommended_generation(value, source, keep_lyrics):
    if isinstance(value, dict):
        # Recommendations made by earlier installed producers remain runnable.
        value = {"performance_source": "", "render_mode": "music", **value}
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("The review did not return a complete generation recipe. Review again to retry.")
    for key in ("title", "lyrics", "style", "abc", "mode", "cot", "seed", "performance_source", "render_mode"):
        if not isinstance(value[key], str):
            raise ValueError(f"The recommended {key} must be text.")
    for key in ("max_seconds", "cfg_scale", "temperature"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]):
            raise ValueError(f"The recommended {key} must be a finite number.")
    if type(value["steps"]) is not int:
        raise ValueError("The recommended solver steps must be a whole number.")
    lyrics = source.get("lyrics", "") if keep_lyrics else value["lyrics"]
    if value["performance_source"] and value["performance_source"] != source.get("performance_track_id"):
        raise ValueError("The producer selected a performance that was not supplied for this review.")
    # These are the same validation and defaults used by POST /api/generations.
    # Origin links come from the studio, never from the model.
    result = validate_recipe({**source, **value, "lyrics": lyrics, "title_auto": False,
                              "parent_track_id": "", "review_id": "",
                              "lyrics_source": "review" if lyrics else "none"})
    for key in ("parent_track_id", "review_id"):
        result.pop(key, None)
    return result
