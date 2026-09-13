"""Discover the same musical operations used by the studio and producer."""
from review_recipe import response_schema
from model_options import CONTEXT, TOKEN_RATE


def describe():
    recipe = response_schema({}, False)["properties"]["generation"]
    recipe["required"] = []
    recipe["properties"]["performance_source"] = {
        "type": "string", "pattern": "^(?:[a-f0-9]{32})?$",
        "description": "Empty for new music, or a library track or completed-stage job ID with performance_available=true. Reuses that take's saved performance codes and duration."}
    recipe["properties"]["score_source"] = {
        "type": "string", "pattern": "^(?:riff-score-v1:[a-f0-9]{64})?$",
        "description": "Empty for revised ABC or new composition, or an owned exact native score ID offered by the studio. Keep abc empty and cot melody or full when retaining a saved score. The score's tokens are reused; the audio performance may be newly generated."}
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
            "score": {"method": "GET", "path": "/api/scores/{score_source}",
                      "description": "Inspect an owned riff-score-v1 reference: id, artifact_id, title, source_job_id, sha256, bytes, token_count, truncated, abc, cot, provenance and compatible; display_error may explain unavailable readable notation. Historical scores remain inspectable when compatible=false, but exact reuse requires compatibility with the selected engine."},
            "generate": {"method": "POST", "path": "/api/generations", "body": "recipe_schema"},
            "compose": {"method": "POST", "path": "/api/plans", "body": "recipe_schema; cot=melody or full"},
            "revise_score": {"method": "POST", "path": "/api/composition/revise", "body": "abc, cot, brief and current recipe inputs"},
            "write": {"method": "POST", "path": "/api/inspiration", "body": "recipe_schema with brief and idea_engine; optional write_scope=all|words|sound, hold_words and hold_sound. Library parent/performance references supply prior inputs, symbolic plans, owned available_scores and listening notes. Returns summary and a complete editable generation recipe, retaining score_source or revising abc as appropriate."},
            "review": {"method": "POST", "path": "/api/tracks/{track_id}/reviews", "body": "focus (text), keep_lyrics (boolean)"},
            "review_result": {"method": "GET", "path": "/api/reviews/{review_id}"},
            "job": {"method": "GET", "path": "/api/jobs/{job_id}"},
            "cancel": {"method": "POST", "path": "/api/jobs/{job_id}/cancel"},
            "move": {"method": "POST", "path": "/api/jobs/{job_id}/move", "body": "direction: up, down or first"},
            "visualization": {"method": "GET", "path": "/api/tracks/{track_id}/visualization"},
            "export_video": {"method": "POST", "path": "/api/tracks/{track_id}/video-exports",
                             "body": "Optional width, height, fps, start_seconds and end_seconds. Defaults to the complete recording; returns sample-aligned source_start, source_end, duration and frame count."},
            "export_status": {"method": "GET", "path": "/api/video-exports/{export_id}"},
            "export_frame": {"method": "POST", "path": "/api/video-exports/{export_id}/frames",
                             "body": "PNG bytes; Content-Type: image/png and X-Riff-Frame: zero-based frame index. Draw shared artwork at source_start + index / fps using the recording's motion data."},
            "export_finish": {"method": "POST", "path": "/api/video-exports/{export_id}/finish"},
            "export_cancel": {"method": "POST", "path": "/api/video-exports/{export_id}/cancel"},
            "export_download": {"method": "GET", "path": "/api/video-exports/{export_id}/download"},
        },
        "performance": {
            "inputs": ["performance_source", "style", "abc", "score_source", "cot", "lyrics", "steps", "solver", "seed"],
            "behavior": "Retains saved music codes, phrasing and duration. Acoustic conditioning, solver steps and seed may be revised. Music sampling settings are skipped. Use a fresh performance to change words or composition.",
        },
        "score": {
            "inputs": ["score_source", "abc", "cot"],
            "behavior": "Retain an offered exact native score with score_source and empty abc; revise notation with abc and empty score_source; leave both empty for a new composition. Saved and edited scores require melody or full planning. Arrangements retain their individual source score references.",
        },
    }
