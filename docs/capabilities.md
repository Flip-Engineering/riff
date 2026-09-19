# YuE2 in Riff

The [upstream model](https://huggingface.co/m-a-p/YuE2-3B) generates a symbolic plan
and semantic music tokens autoregressively, produces acoustic latents through flow
matching, then decodes stereo audio. Riff drives the pinned audio.cpp implementation
of this pipeline. Its low-memory default uses Q4 main weights and an F16 VAE.

The [upstream capability audit](yue2-capability-audit.md) maps the current public
API to Riff and records remaining stage-reuse and composition workflows.

| Musical task | Riff control |
|---|---|
| Words and musical style | Optional lyrics and free-form sound direction |
| Unprompted exploration | Free play; empty conditioning reaches the model |
| Melody and harmony planning | Composition: Melody / Melody and chords |
| Direct music generation | Composition: Off |
| Compose before rendering audio | Compose a new score |
| Supply or reuse a composition | ABC import, exact saved-score attachment, editable draft |
| Reharmonize or change arrangement | Score editor and optional plain-language composer |
| Develop a complete musical idea | Local AI or OpenRouter writer; complete editable generation recipe, source score and listening context |
| Reinterpret an existing score | Import ABC, supply lyrics/style, choose a planning mode |
| Sampling and repeatability | Temperature, top-p/top-k, penalties, windows, seed |
| Planning length | Minimum/maximum ABC tokens |
| Performance length | Duration and minimum semantic tokens |
| Semantic guidance | Guidance |
| Acoustic rendering | Solver steps and synthesis method: Midpoint or Multistep (AB2) |
| Retain phrasing during acoustic refinement | Refine this performance; saved native codes, original score, revised sound, seed and solver steps |
| Finish saved sound without repeating synthesis | Finish audio; owned completed acoustic tensor and captured decoder settings |
| Refine decoding of the same sound | Saved synthesis: tile size, overlap and weight storage, with the compatible VAE |
| Understand a written composition | Voice maps, individual tone-preview voices, written duration and duration matching |
| Agent operation | Discover `/api/capabilities`; use the same library, composition, review and generation queue |
| Decoder or quantization | Custom GGUF paths in Studio settings |
| Compare revisions | A/B takes at the same listening position, passage loops, input differences, short studies, parent links, and runnable producer recommendations |
| Analyze a reference for a cover | Planned C02: optional Gemini direct-audio analysis into editable style, lyrics, duration and ABC; not currently exposed |
| Share a recording or passage | Animated seeded-artwork MP4 with optional in/out points and listening-loop shortcut; WAV, PNG artwork, and generation recipe |

The score is conditioning for a new performance, not an audio editor. Changing
ABC, harmony or lyrics renders another take. The native runtime does not expose
sample-accurate audio inpainting, editable stems, voice-reference encoding or a
compatible YuE2 LoRA loader. The studio does not label unrelated adapters or
quantizations as those features. Existing-score work accepts supplied notation
and lyrics; there is no transcription pipeline.

[Saved sound synthesis](acoustic-recovery.md) retains a completed acoustic stage
before audio decoding. `acoustic_source` creates a new queued take from those
exact latents; omitted decoder controls retain their captured settings. The
producer receives the original musical inputs and can choose this operation
when decoder refinement is appropriate. `render_mode=sound` saves synthesis
without decoding audio.

The composition workspace captures the actual ABC planning tokens from audio.cpp.
**Use this score** retains those tokens for subsequent generations, including
valid empty scores. It skips composing another score while leaving the sound,
lyrics, music sampling and acoustic controls editable. The displayed ABC is an
editing view: changing it, importing notation or accepting an altered composer
proposal replaces the attachment. Undo restores the original score and planning
mode. Direct generation detaches the score and preserves the readable draft.

Agents use the opaque `score_source` reference with `abc` empty and `cot=melody`
or `full`. `GET /api/scores/{score_source}` describes an owned score and whether
the selected engine can replay it. The studio verifies the captured tokenizer
and format, then prepares independent inputs for each job. Saved notation stays
viewable when a different engine is selected; compatibility gates reuse. A
notation-display error retains the verified score reference. Legacy scores
without captured tokenizer provenance remain available as editable ABC.

Writers and producer reviews receive verified available scores, their readable
notation, previous inputs and symbolic context. They can retain a score by
reference, supply revised ABC, or leave both inputs empty for a new composition.
Saved-performance rendering carries its original score reference forward, so
re-encoding display text cannot silently change the conditioning tokens.

Pinned patches add score export and replay, score-only composition, performance-code capture,
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

The optional AI writers use the same generation contract. `POST /api/inspiration`
accepts the current recipe, `idea_engine` (`ai`, `openrouter` or `phrases`), an
optional `brief`, and `write_scope` (`all`, `words` or `sound`). AI responses include
`generation`, `writer_model` and `writer_summary`. They can return complete ABC,
planning mode, every supported sampling override, solver settings and output type.
The local writer may omit unchanged fields; the studio merges them with the source
before validation. Selecting a source recording adds its original inputs, generated
score, artist notes and selected or latest completed producer review. Only valid
library IDs can supply a reusable performance. Holds preserve the artist's words
or sound, and writing for an already queued generation retains its requested
duration and output. Standalone writing fills editable controls and supports Undo;
typing while a request runs takes precedence over its response. OpenRouter uses
the saved connection and its provider's output capacity. Its writing requests do
not inherit the separate local writer token setting.

Video exports accept optional `start_seconds` and `end_seconds`. Both refer to the
original recording; omitting them exports the whole song. The returned
`source_start` and `source_end` are aligned to audio samples. Draw frame `i` using
the shared artwork renderer and motion data at `source_start + i / fps`, upload
the numbered PNG, then finish the export to obtain its MP4 download. The exported
audio is the same selected passage. The complete recording stays in the library.
These operations are also listed at `/api/capabilities`.

The CLI saves performance codes automatically. To re-render them, use
`--performance saved.codes.i32` with the source's lyrics, style, planning mode and
`--score-tokens saved.plan.json` (or editable `--abc`). `--performance-only` saves the code stream before acoustic synthesis. These
options operate on the model's own output; they do not infer notation from audio.

All optional numeric controls validate the runtime's physical or mathematical
limits. Duration determines the semantic token budget using the model's 25 Hz rate;
the context size is 24,576 tokens. Longer prompts and scores also consume that
context. Positive custom solver counts remain available. A plan that reaches its
configured token budget is retained and identified in the score workspace.

Memory mapping already supplies demand paging for weights. Riff does not implement
an Engram memory layer or a second SSD weight-streaming scheme. Such architectural
experiments would need separate model and quality validation.
