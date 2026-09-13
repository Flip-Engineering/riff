# YuE2 in Riff

The [upstream model](https://huggingface.co/m-a-p/YuE2-3B) generates a symbolic plan
and semantic music tokens autoregressively, produces acoustic latents through flow
matching, then decodes stereo audio. Riff drives the pinned audio.cpp implementation
of this pipeline. Its low-memory default uses Q4 main weights and an F16 VAE.

| Musical task | Riff control |
|---|---|
| Words and musical style | Optional lyrics and free-form sound direction |
| Unprompted exploration | Free play; empty conditioning reaches the model |
| Melody and harmony planning | Composition: Melody / Melody and chords |
| Direct music generation | Composition: Off |
| Compose before rendering audio | Compose a new score |
| Supply or reuse a composition | ABC import, prior score, editable draft |
| Reharmonize or change arrangement | Score editor and optional plain-language composer |
| Reinterpret an existing score | Import ABC, supply lyrics/style, choose a planning mode |
| Sampling and repeatability | Temperature, top-p/top-k, penalties, windows, seed |
| Planning length | Minimum/maximum ABC tokens |
| Performance length | Duration and minimum semantic tokens |
| Semantic guidance | Guidance |
| Acoustic rendering | Solver steps and synthesis method: Midpoint or Multistep (AB2) |
| Retain phrasing during acoustic refinement | Refine this performance; saved native codes, original score, revised sound, seed and solver steps |
| Understand a written composition | Voice maps, individual tone-preview voices, written duration and duration matching |
| Agent operation | Discover `/api/capabilities`; use the same library, composition, review and generation queue |
| Decoder or quantization | Custom GGUF paths in Studio settings |
| Compare revisions | A/B takes at the same listening position, passage loops, input differences, short studies, parent links, and runnable producer recommendations |
| Share a recording or passage | Animated seeded-artwork MP4 with optional in/out points and listening-loop shortcut; WAV, PNG artwork, and generation recipe |

The score is conditioning for a new performance, not an audio editor. Changing
ABC, harmony or lyrics renders another take. The native runtime does not expose
sample-accurate audio inpainting, editable stems, voice-reference encoding or a
compatible YuE2 LoRA loader. The studio does not label unrelated adapters or
quantizations as those features. Cover-style work accepts a supplied score and
lyrics; there is no transcription pipeline.

The composition workspace captures the actual ABC planning tokens from audio.cpp.
Pinned patches add score export, score-only composition, performance-code capture,
optional conditioning and acoustic progress. They also release completed prefix
graphs and share temporary cache-upload storage across layers. Metal uses an F16
static attention cache, as CUDA already does; this may change sampling compared
with the former F32 Metal cache. Checkpoint weights remain unchanged. MIDI export and
tone audition are derived from ABC through abcjs, not from the generated WAV.

**Synthesis method** exposes the acoustic integrator separately from its step
count. Midpoint is YuE2's original method and remains the default. Multistep (AB2)
uses the previous velocity estimate after a midpoint startup: `steps + 1` model
evaluations per synthesis block, compared with `2 * steps`. Both are second-order
methods, with different error and stability behavior, so their audio can differ.
The performance codes, duration and semantic sampling settings are preserved.
Use `solver=ab2` in a studio/producer recipe or `--solver ab2` in the CLI. Old
recipes use midpoint; an engine that lacks AB2 is rejected before rendering it.
[Validation](validation.md) records measured time and listening comparisons.

New music takes save native `.codes.i32` performance data beside their audio.
Choose **Performance to render later** to save the generated phrasing without
starting acoustic synthesis. Take history offers **Render performance** for these
performances and for completed stages recovered from interrupted renders. The
original job remains in history; rendering creates a new take. Opening a saved
performance preserves its score and duration, and **Undo revision** restores the
previous draft. Partial or changed artifacts are preserved but cannot be queued.
Agents use `render_mode=performance` and inspect `performance_available` on the
resulting job; `performance_source` accepts its ID for the subsequent render.
Re-rendering bypasses semantic sampling and runs acoustic synthesis and decoding.
Riff resolves the source through its library ID, checks the artifact hash, carries
forward the actual score and uses the saved performance's duration. The code stream
retains musical phrasing; acoustic conditioning, seed, method and solver steps remain
editable. New words or structural changes generally need a fresh performance.
This is not a stem separator or a guarantee that an unwanted musical event can be
removed at the acoustic stage. Older takes without saved codes remain available
for ordinary variation and score reuse.

The optional OpenRouter producer receives this capability and its available source
ID alongside the audio, previous inputs and score. Its structured proposal can
request new music, a performance to render later, a re-render, or an editable score. Each uses the same validation
and queue as the UI. Proposals remain inspectable and editable before execution.
`GET /api/capabilities` describes the operations and recipe schema for other agents.
Mutations use JSON and `X-Riff-Request: 1`, with the studio's existing origin and
private-network access checks. Read a track or review, then submit its proposed
recipe to `POST /api/generations`; add `parent_track_id` and `review_id` to preserve
its ancestry, then poll the returned job ID. A `performance_source`
is an owned library track or completed-stage job ID, never a caller-supplied filesystem path.

Video exports accept optional `start_seconds` and `end_seconds`. Both refer to the
original recording; omitting them exports the whole song. The returned
`source_start` and `source_end` are aligned to audio samples. Draw frame `i` using
the shared artwork renderer and motion data at `source_start + i / fps`, upload
the numbered PNG, then finish the export to obtain its MP4 download. The exported
audio is the same selected passage. The complete recording stays in the library.
These operations are also listed at `/api/capabilities`.

The CLI saves performance codes automatically. To re-render them, use
`--performance saved.codes.i32` with the source's lyrics, style, planning mode and
ABC. `--performance-only` saves the code stream before acoustic synthesis. These
options operate on the model's own output; they do not infer notation from audio.

All optional numeric controls validate the runtime's physical or mathematical
limits. Duration determines the semantic token budget using the model's 25 Hz rate;
the context size is 24,576 tokens. Longer prompts and scores also consume that
context. Positive custom solver counts remain available. A plan that reaches its
configured token budget is retained and identified in the score workspace.

Memory mapping already supplies demand paging for weights. Riff does not implement
an Engram memory layer or a second SSD weight-streaming scheme. Such architectural
experiments would need separate model and quality validation.
