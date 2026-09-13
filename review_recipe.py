"""A producer's next take uses the studio's generation contract."""
import math
import re

import model_options
import run as engine
from studio_core import validate_recipe

FIELDS = ("title", "lyrics", "style", "abc", "score_source", "mode", "cot", "max_seconds",
          "steps", "solver", "cfg_scale", "temperature", "seed", "refinement", "performance_source", "render_mode", "acoustic_source", "decoder")


def generation_context(source):
    keys = (*FIELDS, "brief", "idea_engine", "writer_tokens", "lyrics_source", "energy", "texture", "theme",
            "performance", "performance_track_id", "writer_model", "writer_summary", "acoustic")
    return {key: source[key] for key in keys if key in source}


def available_scores(source):
    """Only studio-offered native scores can be selected by a provider."""
    offered = source.get("available_scores")
    if not isinstance(offered, list):
        return []
    fields = ("id", "title", "abc", "cot", "token_count", "truncated", "sha256", "bytes",
              "source_track_id", "source_job_id", "provenance", "compatible", "display_error")
    scores = {}
    for score in offered:
        if (isinstance(score, dict) and score.get("compatible") is not False and
                isinstance(score.get("id"), str) and re.fullmatch(r"riff-score-v1:[a-f0-9]{64}", score["id"])):
            scores[score["id"]] = {key: score[key] for key in fields if key in score}
    return list(scores.values())


def available_acoustics(source):
    offered = source.get("available_acoustics")
    if not isinstance(offered, list):
        return []
    fields = ("id", "title", "source_job_id", "compatible", "duration", "frames", "seed", "steps", "solver",
              "decode_core_frames", "decode_halo_frames", "vae_storage", "sample_rate", "channels", "sha256")
    result = []
    for item in offered:
        if (isinstance(item, dict) and item.get("compatible") is True and isinstance(item.get("id"), str) and
                re.fullmatch(r"riff-acoustic-v1:[a-f0-9]{64}", item["id"])):
            result.append({**{key: item[key] for key in fields if key in item},
                           "inputs": generation_context(item.get("inputs", {}))})
    return result


def _native_symbolic_context(source):
    plan = source.get("symbolic_plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    return {"supplied_abc": source.get("abc", ""),
            "score_source": source.get("score_source", ""),
            "available_scores": available_scores(source),
            "available_acoustics": available_acoustics(source),
            "generated_abc": plan.get("abc", ""),
            "generated_score_source": plan.get("artifact_id", ""),
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


def source_seed(source):
    """Keep native seed precision; legacy recordings may have no recorded seed."""
    seed = source.get("seed")
    return str(seed) if type(seed) in (str, int) and str(seed).strip() else None


def response_schema(source, keep_lyrics, *, preserve_seed=False):
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
        "abc": {"type": "string", "description": "Complete revised ABC score with score_source empty. Keep abc empty to retain an offered exact score; leave both empty for a new composition."},
        "score_source": {"type": "string", "enum": [""] + [score["id"] for score in available_scores(source)],
                         "description": "An offered exact native score ID, with abc empty and cot melody or full. Reuses the saved score tokens, not the prior audio or music codes. Leave empty for revised ABC or a new composition. Displayed ABC is an editing view and need not preserve every native token."},
        "mode": {"type": "string", "enum": ["free", "instrumental", "lyrics", "surprise"]},
        "cot": {"type": "string", "enum": ["off", "melody", "full"],
                "description": "YuE2 planning: off, melody, or melody and chords. Supplied ABC or an exact saved score needs melody or full."},
        "max_seconds": {"type": "number", "minimum": 1 / model_options.TOKEN_RATE,
                        "description": "Requested duration limit in seconds. Keep the artist's preview scope."},
        "steps": {"type": "integer", "minimum": 1, "description": "Acoustic solver steps."},
        "solver": {"type": "string", "enum": ["midpoint", "ab2"],
                   "description": "Acoustic integration method. Midpoint is the model default, with two network evaluations per step. AB2 is a second-order multistep method using steps+1 evaluations per chunk. It is faster at the same step count, with potentially different acoustic detail. Preserve the source method unless changing it serves the artist's request."},
        "cfg_scale": {"type": "number", "minimum": 0, "maximum": 20,
                      "description": "Semantic classifier-free guidance for music-token generation. A value of 1 uses a single conditioning branch."},
        "temperature": {"type": "number", "minimum": 0, "maximum": 5, "description": "Semantic sampling temperature."},
        "seed": {"type": "string", "pattern": "^[0-9]*$",
                 "description": "Integer seed from 0 through 9223372036854775807, or empty for a new seed."},
        "refinement": {"type": "object", "properties": refinement, "additionalProperties": False,
                       "description": "YuE2 sampling overrides. Omitted controls use runtime defaults."},
        "performance_source": {"type": "string", "enum": [""] + ([source["performance_track_id"]] if source.get("performance_track_id") else []),
                               "description": "Empty for a fresh performance. Use the supplied track ID to re-synthesize its saved performance codes with revised acoustic conditioning or solver steps. Retains phrasing and length; not suitable for new words or a new composition."},
        "render_mode": {"type": "string", "enum": ["music", "plan", "performance", "sound"],
                        "description": "music renders a take; performance saves music codes; sound saves completed sound synthesis for later decoding; plan composes a score and requires cot melody or full and empty performance_source. An acoustic_source needs music."},
        "acoustic_source": {"type": "string", "enum": [""] + [item["id"] for item in available_acoustics(source)],
                            "description": "An offered completed sound to decode again. Copy its listed musical inputs exactly; only title and decoder controls change. Skips composition, performance and acoustic synthesis. Leave empty when changing any of those stages."},
        "decoder": engine.decoder_schema(),
    }
    for item in available_acoustics(source):
        for key in ("score_source", "performance_source"):
            reference = item["inputs"].get(key)
            if reference and reference not in properties[key]["enum"]:
                properties[key]["enum"].append(reference)
    if keep_lyrics:
        properties["lyrics"]["description"] = "Riff preserves the source lyrics. This field may be empty when Keep lyrics is enabled."
    if preserve_seed and source_seed(source) is not None:
        properties["seed"].update(enum=[source_seed(source)],
                                  description="Retain this recording's original seed while refining its music. The artist can change it in the studio.")
    return {"type": "object", "additionalProperties": False,
            "required": ["notes", "summary", "generation"],
            "properties": {
                "notes": {"type": "string", "description": "Candid listening observations, with useful timestamps."},
                "summary": {"type": "string", "description": "Brief musical intent of this recommended take."},
                "generation": {"type": "object", "properties": properties,
                               "required": list(FIELDS), "additionalProperties": False}}}


def validate_generation(value, source, keep_lyrics=False):
    """Validate a complete composer or producer recipe at the common boundary."""
    if isinstance(value, dict):
        value = {"acoustic_source": "", "decoder": {}, **value}
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("The model did not return a complete generation recipe. Your current draft is kept.")
    for key in ("title", "lyrics", "style", "abc", "score_source", "mode", "cot", "seed", "performance_source", "render_mode", "solver", "acoustic_source"):
        if not isinstance(value[key], str):
            raise ValueError(f"The recommended {key} must be text.")
    for key in ("max_seconds", "cfg_scale", "temperature"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]):
            raise ValueError(f"The recommended {key} must be a finite number.")
    if type(value["steps"]) is not int:
        raise ValueError("The recommended solver steps must be a whole number.")
    lyrics = source.get("lyrics", "") if keep_lyrics else value["lyrics"]
    acoustic = next((item for item in available_acoustics(source) if item["id"] == value["acoustic_source"]), None)
    if value["acoustic_source"]:
        if acoustic is None:
            raise ValueError("The model selected sound synthesis that was not supplied for this request.")
        from acoustic_artifacts import UPSTREAM
        effective = {**value, "lyrics": lyrics}
        if any(effective[key] != acoustic["inputs"].get(key, "" if key.endswith("_source") else None) for key in UPSTREAM):
            raise ValueError("The recommended decoder take changed its saved musical inputs. Use a new performance for those changes.")
    if not acoustic and value["performance_source"] and value["performance_source"] != source.get("performance_track_id"):
        raise ValueError("The model selected a performance that was not supplied for this request.")
    if not acoustic and value["score_source"] and value["score_source"] not in {score["id"] for score in available_scores(source)}:
        raise ValueError("The model selected a score that was not supplied for this request.")
    # These are the same validation and defaults used by POST /api/generations.
    # Origin links come from the studio, never from the model.
    result = validate_recipe({**source, **value, "lyrics": lyrics, "title_auto": False,
                              "parent_track_id": "", "review_id": "",
                              "lyrics_source": "review" if lyrics else "none"})
    for key in ("parent_track_id", "review_id"):
        result.pop(key, None)
    return result


def recommended_generation(value, source, keep_lyrics):
    if isinstance(value, dict):
        # Recommendations made by earlier installed producers remain runnable.
        value = {"performance_source": "", "score_source": "", "render_mode": "music", "solver": "midpoint", **value}
        if source_seed(source) is not None:
            value["seed"] = source_seed(source)
    return validate_generation(value, source, keep_lyrics)
