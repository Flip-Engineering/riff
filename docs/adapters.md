# YuE2 adapters and reusable conditioning

Checked September 10, 2026 against the public model registry, upstream project,
and Riff's pinned audio.cpp implementation.

**No compatible published YuE2 LoRA collection was found.** This is a search result,
not proof that no private, untagged, or future adapter exists. The raw query results
are preserved in `adapter-research.json`.

- The [Hugging Face base-model query](https://huggingface.co/api/models?filter=base_model%3Am-a-p%2FYuE2-3B&full=true)
  returned `audio-cpp/Yue2-3B-GGUF` and `ngquocvinh/YuE2-3B-GGUF`. Both identify
  themselves as quantizations, not trained style or voice adapters.
- A [broader YuE2 model search](https://huggingface.co/api/models?search=YuE2&full=true)
  found the base model, VAEs, mirrors, ComfyUI bundles, and unrelated name matches.
  `Nerva1228/yue2` is tagged as a FLUX.1-dev text-to-image LoRA, so its name alone
  does not establish music-model compatibility.
- The [official model card](https://huggingface.co/m-a-p/YuE2-3B) documents lyrics,
  style, symbolic melody/chord planning, supplied ABC scores, and VAE choices.
  The [official repository tree](https://api.github.com/repos/multimodal-art-projection/YuE/git/trees/main?recursive=1)
  returned 88 entries without truncation and no adapter, LoRA, or training filenames.
- In the [pinned audio.cpp YuE2 implementation](https://github.com/0xShug0/audio.cpp/tree/fbe3eedbf6c504e45189e2cdcf1b257740a28863/src/models/yue2),
  request/session declarations and AR/NAR weight loading expose no LoRA loader.
  Repository-wide LoRA support for another model family does not establish support
  for this architecture.

Riff therefore manages saved **sound prompts** with create, browse, edit, and remove
operations. These are prompt presets, not learned adapters. Existing recordings
keep their own complete recipe when a saved sound changes or is removed. ABC
conditioning remains available in the composition controls.

Before adding adapter downloads or activation, verify an artifact's exact base
checkpoint, target tensor names, AR/NAR coverage, format, license, and a compatible
loader in this native runtime. A GGUF quantization, VAE, or voice-conversion model
must not be offered as a drop-in YuE2 LoRA. No adapter downloads were added. The optional local Qwen writer is a separate text model, not a YuE2 adapter.

Free play leaves lyrics and style empty unless a sound description is supplied.
Riff's small, reproducible native patch removes YuE2's nonempty-field guards and
marks its CLI conditioning fields optional. It preserves tokenization, weights,
and the generation pipeline. No artificial section marker is inserted.

Instrumental mode supplies an `[Instrumental]` cue to the music runtime when no
lyric text is provided, and a basic instrumental direction only when the sound
is blank. Artist-written descriptions and custom lyric cues are preserved. In a
same-seed brass study, this cue produced a clean instrumental passage where empty
lyrics introduced singing; it is a useful default, not a guarantee. The
[official model card](https://huggingface.co/m-a-p/YuE2-3B)
describes song generation, and instrumental requests may still produce voice-like
sounds. Neither lyrics nor a section plan is required in the studio.
