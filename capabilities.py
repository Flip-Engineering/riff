"""Discover the same musical operations used by the studio and producer."""
from review_recipe import response_schema
from model_options import CONTEXT, TOKEN_RATE


def describe():
    recipe = response_schema({}, False)["properties"]["generation"]
    recipe["required"] = []
    recipe["properties"]["performance_source"] = {
        "type": "string", "pattern": "^(?:[a-f0-9]{32})?$",
        "description": "Empty for new music, or a library track ID with performance_available=true. Reuses that take's saved performance codes and duration."}
    for name in ("parent_track_id", "review_id"):
        recipe["properties"][name] = {"type": "string", "pattern": "^(?:[a-f0-9]{32})?$",
                                      "description": "Optional source reference to retain the iteration's ancestry."}
    recipe["properties"]["idea_engine"] = {"type": "string", "enum": ["ai", "phrases", "openrouter"]}
    recipe["properties"]["brief"] = {"type": "string", "description": "The writer's musical intention for a Surprise song."}
    return {
        "model": "YuE2", "context_tokens": CONTEXT, "music_tokens_per_second": TOKEN_RATE,
        "recipe_schema": recipe,
        "request_header": {"X-Riff-Request": "1"},
        "operations": {
            "library": {"method": "GET", "path": "/api/state"},
            "recording": {"method": "GET", "path": "/api/tracks/{track_id}"},
            "generate": {"method": "POST", "path": "/api/generations", "body": "recipe_schema"},
            "compose": {"method": "POST", "path": "/api/plans", "body": "recipe_schema; cot=melody or full"},
            "revise_score": {"method": "POST", "path": "/api/composition/revise", "body": "abc, cot, brief and current recipe inputs"},
            "write": {"method": "POST", "path": "/api/inspiration", "body": "brief, mode, style, lyrics, idea_engine=openrouter and optional seed"},
            "review": {"method": "POST", "path": "/api/tracks/{track_id}/reviews", "body": "focus (text), keep_lyrics (boolean)"},
            "review_result": {"method": "GET", "path": "/api/reviews/{review_id}"},
            "job": {"method": "GET", "path": "/api/jobs/{job_id}"},
            "cancel": {"method": "POST", "path": "/api/jobs/{job_id}/cancel"},
            "move": {"method": "POST", "path": "/api/jobs/{job_id}/move", "body": "direction: up, down or first"},
        },
        "performance": {
            "inputs": ["performance_source", "style", "abc", "cot", "lyrics", "steps", "seed"],
            "behavior": "Retains saved music codes, phrasing and duration. Acoustic conditioning, solver steps and seed may be revised. Music sampling settings are skipped. Use a fresh performance to change words or composition.",
        },
    }
