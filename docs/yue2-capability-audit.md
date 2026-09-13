# YuE2 capability audit

Audited September 12, 2026 against upstream
[88da114a](https://github.com/multimodal-art-projection/YuE/tree/88da114a67df892af0329472073b96a5ef700b93)
and initially Riff v0.5.3 (`5919837`); Riff coverage updated September 13 for exact
score replay. Upstream `main` at that audit contains YuE2; the original
YuE is on its separate `YuE-v1` branch. Riff uses the native implementation and
patches pinned in [sources.json](../sources.json), not the Python backend.

The ordinary musical inputs are exposed. The remaining gaps concern exact stage
reuse, complete generation artifacts, and composition workflows. A source audit
establishes available operations, not musical equivalence between runtimes.

## Inputs and operations

| Upstream capability | Riff UI and agent exposure | Coverage |
| --- | --- | --- |
| Style, lyrics, language, instrumentation and vocal direction | Open text, optional lyrics, free play, instrumental mode, local/cloud writer; common generation recipe | Exposed. These are open prompts, not genre or language enumerations. |
| `cot=off`, `melody`, `full`; supplied ABC | Composition mode, score workspace, import, producer/writer recipe | Exposed. Direct generation omits dormant ABC while retaining the editable draft. |
| Score-only generation | Compose a score, score history, `POST /api/plans` | Exposed. The generated score can be viewed, auditioned and edited before audio. |
| Seven sampling controls independently for score and semantic music | Twelve refinement overrides, top-level music temperature, and duration converted to the music-token maximum | All fourteen mapped. See the field table below. |
| Guidance and seed | Studio, producer, writer and queue | Exposed, including guidance below 1 and integer seeds through `2**63 - 1`. |
| Acoustic integration and step count | Midpoint and native AB2, custom steps, saved-performance refinement | Exposed. AB2 is a Riff extension; upstream's public configuration specifies midpoint. |
| Semantic generation without synthesis; synthesis from saved semantics | Performance to render later, interrupted-stage recovery, Render performance, `performance_source` | Exposed. Codes and duration are checked; captured score references preserve the symbolic input. |
| Restoring an unchanged `SymbolicPlan` by its token IDs | Saved score attachments, score history, variations, writer/producer recipe and `score_source` | Exposed for newly captured scores. Native replay retains exact IDs, empty-score presence and truncation. Historical scores without captured tokenizer provenance remain editable ABC. |
| Saved acoustic latents and separate `decode()` | Internal native synthesis and VAE decoding | **Missing operation.** Completed latents are not retained for decode-only recovery or comparison. |
| Complete artifact bundle, effective settings and model identities | WAV, ABC/MIDI, recipe, code hashes, generation logs and stage status | **Partial.** No single verified bundle binds every stage, effective defaults, tokenizer, model, decoder and runtime to each recording. |
| Standard/legacy VAE selection; full or tiled decoding | Custom model/VAE GGUF paths; native standard tiled decoder | **Partial.** Named compatible decoders and supported tile controls need native verification and discovery. |
| Melody-preserving covers from supplied notation | ABC import, melody mode, style/lyrics revision | Exposed manually. **Missing convenience:** release chord annotations while preserving both vocal and instrumental melody voices. |
| Agent score edits with checks on what changed | Note editing, transposition, tempo/meter/key, visual proposal, Undo and audio A/B | **Partial.** No structured comparison of notes, durations, harmony, voices and form against requested invariants. |
| Multiple candidates and resumable batches | Individual queued takes, ancestry, studies, comparisons and producer proposals | Exposed individually. **Missing workflow:** a named family of variants with shared inputs, differences and group comparison. |
| VAE audio encoding | No studio operation | Companion-model capability, separate from the generator's public request. An encoder round trip needs native implementation and validation; it does not by itself implement musical inversion/editing. |

Primary contracts: [request and sampling types][protocol], [stage API and
artifact storage][pipeline], [generation guide][generation], [editing guide][editing],
[cover guide][covers], [batch implementation][cli] and [VAE implementation][vae].
Riff's implementation is in [run.py](../run.py), [model_options.py](../model_options.py),
[studio_core.py](../studio_core.py), [symbolic.py](../symbolic.py),
[score.js](../web/score.js) and [capabilities.py](../capabilities.py).

### Sampling field mapping

| Upstream field | Score control | Semantic music control |
| --- | --- | --- |
| `temperature` | `refinement.abc_temperature` | `temperature` |
| `top_p` | `refinement.abc_top_p` | `refinement.semantic_top_p` |
| `top_k` | `refinement.abc_top_k` | `refinement.semantic_top_k` |
| `repetition_penalty` | `refinement.abc_repetition_penalty` | `refinement.semantic_repetition_penalty` |
| `penalty_window` | `refinement.abc_penalty_window` | `refinement.semantic_penalty_window` |
| `min_tokens` | `refinement.abc_min_tokens` | `refinement.semantic_min_tokens` |
| `max_tokens` | `refinement.abc_max_tokens` | `floor(max_seconds * 25)` |

Both AI writers and the producer now use the complete generation schema,
including every refinement field, supplied/generated notation and source context.
The dedicated score editor returns a proposed score; whole-song writing returns
the complete recipe. These serve different editing scopes.

## Work to prioritize

1. **Acoustic checkpoints.** Exact plan reuse now preserves token IDs and
   captured tokenizer provenance through the queue. Next, save completed acoustic
   latents and expose decode-only recovery. Decoded ABC remains an editing view;
   re-encoding it does not guarantee the original token sequence. Historical raw
   files remain intact, but unknown capture provenance is not invented.
   Recovery should avoid rerunning a completed expensive stage.
2. **Melody and harmony controls with a visible comparison.** Offer an explicit
   operation to free the harmony of a supplied score. Merely selecting melody
   mode does not strip its chords. Report what changed after a score revision;
   only check preservation constraints the artist actually requested. Unsupported
   notation should be identified without forbidding otherwise usable ABC.
3. **Recording provenance and decoder choices.** Bind model, tokenizer, decoder,
   runtime and effective settings to saved artifacts. Offer verified named
   decoders and compare them using identical latents. Existing benchmark and
   listening decoders have different purposes; legacy is not an assumed upgrade.
4. **Grouped variations.** Let people and agents create, inspect and compare a
   family of takes, choosing which composition or performance to retain. Upstream
   best-of-eight is a candidate-selection workflow, not another generation mode
   or checkpoint.

The native request parser also accepts supplied acoustic noise. Riff does not
currently expose it. An advanced experiment could reference a validated owned
noise artifact, rather than accept arbitrary filesystem paths. This is a native
runtime facility, not a missing field in upstream `SongRequest`.

## Runtime and companion tools

Upstream offers Torch/eager/vLLM execution, CUDA graphs, FP8 AR weights and
phase-specific offloading. These are execution choices to evaluate against the
native Metal/CPU/CUDA backend, not musical controls to copy blindly into a menu.
The same applies to warm session reuse between queued jobs. Riff's current engine
work targets repeated conditioning, acoustic network evaluations, attention and
projection cost; memory and complete-render time need separate measurements.

The linked cover workflow obtains notation using SheetSage2/MERT2 before calling
YuE2. Riff accepts supplied notation and does not run transcription. The examined
generator API has no reference-audio argument, stem output, waveform inpainting,
voice-cloning input or LoRA loader. Those are not hidden controls that can be
enabled by UI work alone.

Fine-tuning needs a separate audit of training representations. The public VAE
encoder is not the semantic audio tokenizer; its source identifies that tokenizer
as unreleased. The published MERT2 encoders return continuous features, while
YuE2 expects discrete semantic codes. [Issue #7][adaptation] tracks reconstructing
the missing path where needed, testing useful adaptation targets independently,
and implementing actual adapter training and native loading.

[protocol]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/src/yue2/protocol.py
[pipeline]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/src/yue2/pipeline.py
[generation]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/docs/generation.md
[editing]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/docs/editing.md
[covers]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/docs/covers.md
[cli]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/src/yue2/cli.py
[vae]: https://github.com/multimodal-art-projection/YuE/blob/88da114a67df892af0329472073b96a5ef700b93/src/yue2/modeling_vae.py
[adaptation]: https://github.com/Flip-Engineering/riff/issues/7
