"""Discover the same musical operations used by the studio and producer."""
from review_recipe import response_schema
from model_options import CONTEXT, TOKEN_RATE
from video import VIDEO_PROFILES


def describe():
    recipe = response_schema({}, False)["properties"]["generation"]
    recipe["required"] = []
    recipe["properties"]["performance_source"] = {
        "type": "string", "pattern": "^(?:[a-f0-9]{32})?$",
        "description": "Empty for new music, or a library track or completed-stage job ID with performance_available=true. Reuses that take's saved performance codes and duration."}
    recipe["properties"]["score_source"] = {
        "type": "string", "pattern": "^(?:riff-score-v1:[a-f0-9]{64})?$",
        "description": "Empty for revised ABC or new composition, or an owned exact native score ID offered by the studio. Keep abc empty and cot melody or full when retaining a saved score. The score's tokens are reused; the audio performance may be newly generated."}
    recipe["properties"]["acoustic_source"] = {
        "type": "string", "pattern": "^(?:riff-acoustic-v1:[a-f0-9]{64})?$",
        "description": "Owned saved sound. POST this reference plus optional decoder controls and title to finish audio; omitted musical inputs are restored from its recorded generation. Supplied musical inputs must match that sound."}
    for name in ("parent_track_id", "review_id"):
        recipe["properties"][name] = {"type": "string", "pattern": "^(?:[a-f0-9]{32})?$",
                                      "description": "Optional source reference to retain the iteration's ancestry."}
    recipe["properties"]["idea_engine"] = {"type": "string", "enum": ["ai", "phrases", "openrouter"]}
    recipe["properties"]["brief"] = {"type": "string", "description": "The writer's musical intention for a Surprise song."}
    return {
        "model": "YuE2", "context_tokens": CONTEXT, "music_tokens_per_second": TOKEN_RATE,
        "recipe_schema": recipe,
        "video_profiles": VIDEO_PROFILES,
        "request_header": {"X-Riff-Request": "1"},
        "operations": {
            "library": {"method": "GET", "path": "/api/state"},
            "lineage": {"method": "GET", "path": "/api/tracks/{track_id}/lineage",
                        "version": 1, "body": "Read-only connected graph of recorded parent/performance references, including archived takes and unavailable source placeholders. Edges expose recorded-input differences and distinguish missing values from explicit values. No ancestry is inferred from seed, title or musical similarity; cycles are reported without rewriting recipes."},
            "recording": {"method": "GET", "path": "/api/tracks/{track_id}"},
            "score": {"method": "GET", "path": "/api/scores/{score_source}",
                      "description": "Inspect an owned riff-score-v1 reference: id, artifact_id, title, source_job_id, sha256, bytes, token_count, truncated, abc, cot, provenance and compatible; display_error may explain unavailable readable notation. Historical scores remain inspectable when compatible=false, but exact reuse requires compatibility with the selected engine."},
            "acoustic": {"method": "GET", "path": "/api/acoustics/{acoustic_source}",
                         "description": "Inspect saved sound metadata, recorded musical inputs, captured decoder settings and compatibility. No latent bytes or internal file paths are returned."},
            "finish_audio": {"method": "POST", "path": "/api/generations", "body": "acoustic_source; optional decoder, title, review_id. Creates a new queued child preserving the saved original and source seed."},
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
                             "body": "Optional width, height, fps, start_seconds, end_seconds, profile=share|master (default master), transport=png|h264 (default png). Dimensions/rate remain unchanged by profile. PNG is encoded at the advertised CRF; H.264 clients must use the advertised min/max bitrate and bits_per_pixel target. MP4 audio is lossy AAC; completed results also link the untouched full original WAV, including for passage exports."},
            "export_status": {"method": "GET", "path": "/api/video-exports/{export_id}"},
            "export_frame": {"method": "POST", "path": "/api/video-exports/{export_id}/frames",
                             "body": "PNG bytes (image/png), or one Annex-B H.264 frame (video/h264) when transport=h264; X-Riff-Frame: zero-based frame index. Draw shared artwork at source_start + index / fps using the recording's motion data. H.264 chunks must use the advertised profile target; server-side stream validation does not prove perceptual quality or enforce an uploaded bitrate."},
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
        "acoustic": {
            "inputs": ["acoustic_source", "decoder"],
            "behavior": "Decodes completed sound with the compatible VAE, skipping AR/NAR and main-model loading. Omitted decoder controls restore capture defaults. Core/halo/storage overrides can change the audio; title may change, musical inputs remain provenance. render_mode=sound captures synthesis without decoding.",
        },
    }
