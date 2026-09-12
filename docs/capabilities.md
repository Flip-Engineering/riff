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
| Acoustic rendering | Solver steps |
| Decoder or quantization | Custom GGUF paths in Studio settings |
| Compare revisions | A/B takes at the same listening position, passage loops, input differences, short studies, parent links, and runnable producer recommendations |
| Share a recording | Animated seeded-artwork MP4 with audio, WAV, PNG artwork, and generation recipe |

The score is conditioning for a new performance, not an audio editor. Changing
ABC, harmony or lyrics renders another take. The native runtime does not expose
sample-accurate audio inpainting, editable stems, voice-reference encoding or a
compatible YuE2 LoRA loader. The studio does not label unrelated adapters or
quantizations as those features. Cover-style work accepts a supplied score and
lyrics; there is no transcription pipeline.

The composition workspace captures the actual ABC planning tokens from audio.cpp.
A small pinned patch adds score export and a score-only exit before semantic and
acoustic generation. A second patch permits empty conditioning, and a third reports
acoustic progress. These patches do not change checkpoint weights. MIDI export and
tone audition are derived from ABC through abcjs, not from the generated WAV.

All optional numeric controls validate the runtime's physical or mathematical
limits. Duration determines the semantic token budget using the model's 25 Hz rate;
the context size is 24,576 tokens. Longer prompts and scores also consume that
context. Positive custom solver counts remain available. A plan that reaches its
configured token budget is retained and identified in the score workspace.

Memory mapping already supplies demand paging for weights. Riff does not implement
an Engram memory layer or a second SSD weight-streaming scheme. Such architectural
experiments would need separate model and quality validation.
