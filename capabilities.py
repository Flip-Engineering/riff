"""Discover the same musical operations used by the studio and producer."""
from review_recipe import response_schema, lora_schema
from model_options import CONTEXT, TOKEN_RATE
from video import VIDEO_ENCODING, VIDEO_FRAME_BATCH
from operations import catalog
from families import catalog as family_catalog
from paths import ROOT


def describe():
    recipe = response_schema({}, False)["properties"]["generation"]
    recipe["required"] = []
    recipe["properties"]["loras"] = lora_schema()
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
    surface = catalog(recipe, (ROOT / "VERSION").read_text().strip())
    surface["operations"].update(family_catalog(recipe))
    return {
        "model": "YuE2", "context_tokens": CONTEXT, "music_tokens_per_second": TOKEN_RATE,
        "recipe_schema": recipe,
        "video_encoding": VIDEO_ENCODING,
        "video_frame_batch": VIDEO_FRAME_BATCH,
        "request_header": {"X-Riff-Request": "1"},
        "agent_surface": surface,
        "operations": {
            "loras": {"method": "GET", "path": "/api/loras", "description": "Installed adapters and native activation availability."},
            "lora_search": {"method": "GET", "path": "/api/loras/hub?q=&sort=downloads&page=0&limit=20"},
            "lora_detail": {"method": "GET", "path": "/api/loras/hub/detail?repo={owner/name}&revision=main"},
            "lora_install": {"method": "POST", "path": "/api/loras", "body": "repo, revision (resolved commit), files (safetensors paths); optional local name. Returns a cancellable install task."},
            "lora_import": {"method": "POST", "path": "/api/loras/import?name={file.safetensors}",
                            "body": "Raw safetensors bytes, Content-Type application/octet-stream. Optional X-Riff-LoRA-Metadata JSON includes name, license, trigger_words and adapter_config for PEFT. No host filesystem paths."},
            "lora_task": {"method": "GET", "path": "/api/loras/tasks/{task_id}"},
            "lora_cancel": {"method": "POST", "path": "/api/loras/tasks/{task_id}/cancel", "body": "Empty object."},
            "lora_edit": {"method": "PATCH", "path": "/api/loras/{adapter_id}", "body": "name, notes, tags, default_strength or trigger_words."},
            "lora_updates": {"method": "GET", "path": "/api/loras/{adapter_id}/updates"},
            "lora_clean": {"method": "POST", "path": "/api/loras/{adapter_id}/clean", "body": "Empty object. Deletes only the files the installation holds that its manifest does not reference (listed as unreferenced, counted in retained_bytes); referenced files and manifest.json are never touched. Returns what was removed and the bytes reclaimed."},
            "lora_delete": {"method": "DELETE", "path": "/api/loras/{adapter_id}", "body": "Empty object; confirm:true acknowledges recorded references. Queued/active take references always prevent deletion."},
            "library": {"method": "GET", "path": "/api/state"},
            "lineage": {"method": "GET", "path": "/api/tracks/{track_id}/lineage",
                        "version": 1, "body": "Read-only connected graph of recorded parent/performance references, including archived takes and unavailable source placeholders. Edges expose recorded-input differences and distinguish missing values from explicit values. No ancestry is inferred from seed, title or musical similarity; cycles are reported without rewriting recipes."},
            "recording": {"method": "GET", "path": "/api/tracks/{track_id}",
                          "body": "The saved recording's recipe and audio analysis. lyrics.state is recorded, instrumental, or missing. lyrics.text is the exact retained input for recorded takes, including Unicode and line breaks; a later prompt edit never changes it. Imported CLI output without a recorded lyric argument stays missing."},
            "score": {"method": "GET", "path": "/api/scores/{score_source}",
                      "description": "Inspect an owned riff-score-v1 reference: id, artifact_id, title, source_job_id, sha256, bytes, token_count, truncated, abc, cot, provenance and compatible; display_error may explain unavailable readable notation. Historical scores remain inspectable when compatible=false, but exact reuse requires compatibility with the selected engine."},
            "acoustic": {"method": "GET", "path": "/api/acoustics/{acoustic_source}",
                         "description": "Inspect saved sound metadata, recorded musical inputs, captured decoder settings and compatibility. No latent bytes or internal file paths are returned."},
            "finish_audio": {"method": "POST", "path": "/api/generations", "body": "acoustic_source; optional decoder, title, review_id. Creates a new queued child preserving the saved original and source seed."},
            "generate": {"method": "POST", "path": "/api/generations", "body": "recipe_schema"},
            "compose": {"method": "POST", "path": "/api/plans", "body": "recipe_schema; cot=melody or full"},
            "revise_score": {"method": "POST", "path": "/api/composition/revise", "body": "abc, cot, brief and current recipe inputs"},
            "write": {"method": "POST", "path": "/api/inspiration", "body": "recipe_schema with brief and idea_engine; optional write_scope=all|words|sound, hold_words and hold_sound. Library parent/performance references supply prior inputs, symbolic plans, owned available_scores and listening notes. Returns summary and a complete editable generation recipe, retaining score_source or revising abc as appropriate."},
            "review": {"method": "POST", "path": "/api/tracks/{track_id}/reviews",
                       "body": "focus (text), keep_lyrics (boolean); optional references: attachment IDs from review_reference_add, in listening order. Attached clips go to the model in the same request as the take, and the clips are consumed by this review."},
            "review_result": {"method": "GET", "path": "/api/reviews/{review_id}",
                              "body": "status, notes, summary and the executable generation. When clips were attached it also returns references (id, sha256, label, link, note, duration, bytes, format; no file paths), observations attributed to generated_take or a reference id, sent_audio (format, size and hash of what was sent after MP3 conversion), and warnings for a partial answer. POST generation with parent_track_id and review_id to keep seed, score and review lineage; the resulting take's recipe records review_references (id, sha256, label) as provenance only."},
            "review_master_add": {"method": "POST", "path": "/api/review/masters?sha256={sha256}",
                                  "body": "Exact primary edited-master audio bytes, Content-Type application/octet-stream. Returns sha256, bytes, duration and format. Separate from library recordings and comparison references; never accepts a server host path."},
            "review_master_submit": {"method": "POST", "path": "/api/reviews",
                                     "body": "request_id (stable artist identity), master {sha256, bytes, duration}, source_recipe, edit_context (object), model, focus, keep_lyrics; optional source_track_id for association only. Same ID and inputs return the saved review, even failed/cancelled/interrupted; changed inputs conflict. Uses the existing owner review queue. Result includes master identity, sent_audio and result with complete redacted provider_response. Queued is not proof of listening."},
            "review_cancel": {"method": "POST", "path": "/api/reviews/{review_id}/cancel", "body": "Empty object. Cancels queued/running listening work; provider contact may already have occurred."},
            "review_references": {"method": "GET", "path": "/api/review/references", "body": "Pending reference clips, the review settings that apply (reference_probe_seconds) and an estimate of what a review would send (clips, audio_seconds, source_bytes, estimated_sent_bytes and notices). Add ?track_id= to include that recording's audio. The estimate is a report; nothing refuses a review for size or duration."},
            "review_reference_add": {"method": "POST", "path": "/api/review/references?name={clip.wav}",
                                     "body": "Raw audio bytes, Content-Type application/octet-stream. Optional X-Riff-Reference-Metadata JSON: label, link (http or https), note. Stored outside Git in the workspace data directory as an opaque, hashed clip. No host filesystem paths."},
            "review_reference_cancel": {"method": "POST", "path": "/api/review/references/cancel", "body": "Empty object. Stops every clip check in progress; each upload then fails with a cancelled message."},
            "review_reference_edit": {"method": "PATCH", "path": "/api/review/references/{reference_id}", "body": "label, link or note of a pending clip."},
            "review_reference_remove": {"method": "DELETE", "path": "/api/review/references/{reference_id}", "body": "Empty object. Removes a pending clip; clips named by a saved review receipt are kept."},
            "review_reference_reuse": {"method": "POST", "path": "/api/review/references/reuse", "body": "review_id. Attaches fresh editable copies of that review's clips for another review or a retry."},
            "job": {"method": "GET", "path": "/api/jobs/{job_id}"},
            "delete_track": {"method": "DELETE", "path": "/api/tracks/{track_id}",
                             "body": "Empty object. Permanently removes the recording and its WAV. Surviving takes receive independent copies of referenced performance data and their parent link is cleared. Saved videos and shared score/synthesis artifacts are retained."},
            "cancel": {"method": "POST", "path": "/api/jobs/{job_id}/cancel"},
            "move": {"method": "POST", "path": "/api/jobs/{job_id}/move", "body": "direction: up, down or first"},
            "visualization": {"method": "GET", "path": "/api/tracks/{track_id}/visualization"},
            "export_video": {"method": "POST", "path": "/api/tracks/{track_id}/video-exports",
                             "body": "Optional width, height, fps, start_seconds, end_seconds, transport=png|h264 (default png). One fixed high-quality encoding configuration, advertised in video_encoding. Both paths end in libx264 at the advertised preset, tune and CRF with BT.709 colour. H.264 clients send frames at the advertised fixed quantizer, or where a browser cannot, at the min/max bitrate and bits_per_pixel target. MP4 audio is lossy AAC; completed results also link the untouched full original WAV, including for passage exports."},
            "export_status": {"method": "GET", "path": "/api/video-exports/{export_id}"},
            "export_frame": {"method": "POST", "path": "/api/video-exports/{export_id}/frames",
                             "body": "PNG bytes (image/png), or one Annex-B H.264 frame (video/h264) when transport=h264. Batch either path using video_frame_batch.content_type: each frame is preceded by its unsigned 32-bit big-endian byte length, within the advertised frame and byte limits. X-Riff-Frame is the zero-based index of the first frame. Frames must remain in order. After a lost response, GET export_status and resume at received, sending only any unaccepted suffix. Draw shared artwork at source_start + index / fps using the recording's motion data. H.264 chunks use the advertised encoding target; server-side stream validation does not prove perceptual quality or enforce an uploaded bitrate."},
            "export_finish": {"method": "POST", "path": "/api/video-exports/{export_id}/finish"},
            "export_cancel": {"method": "POST", "path": "/api/video-exports/{export_id}/cancel"},
            "export_download": {"method": "GET", "path": "/api/video-exports/{export_id}/download"},
        },
        "performance": {
            "inputs": ["performance_source", "style", "abc", "score_source", "cot", "lyrics", "steps", "solver", "seed", "loras"],
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
