# Riff project todo

Updated: 2026-09-20 05:04 UTC

This is the canonical current-work list. The much larger private ledger in
`data/TASKS.md` remains useful for receipts and historical evidence, but its
old release sections are not a second live backlog. A task is removed from
this list only when it is completed, deliberately closed, or explicitly
rejected; rejected experiments remain documented in the private ledger.

## Open-issue execution pass

- #22: reproduced a delayed pre-save poll replacing the post-save state and
  removing the newly saved sound from the UI. Refresh application is now
  monotonic by request order; a deterministic regression holds and releases the
  older response. Version 0.6.17 release acceptance is tracked separately.
- #20: encode/upload overlap is an isolated, unshipped experiment. Matched
  measurements were stopped under severe disk pressure and high host load;
  no throughput claim or long-PNG reliability fix is accepted from that run.
  Receipts: `data/export-fixed-baseline-20260920` and
  `data/export-fixed-overlap-20260920`. The lower-quality profiles remain removed.
- Updater recovery shipped in 0.6.16: normal update checks succeed while the
  anonymous GitHub API returns 403, using public version-bound payload receipts.
  Standard Riff Setup installed it; all 300 tracks and 2344 protected files
  were unchanged. This does not close external-host and storage-reporting work.

- Library deletion (0.6.14): Delete song in recording options, confirmation,
  independent performance copies for children, broken parent links, safe player
  clearing, and retained video downloads. Local checks and release acceptance
  are recorded separately; no real user songs are deleted during validation.

The September 19 request covers the remaining open issues. Work proceeds through
export throughput (#20), variation lineage, shared agent operations
and study families (#11/#10), continuous material workflow (#14), bounded warm workers
(#15), and optional review references (#16). Research tracks #7/#17/#18 keep their
stated boundaries: no unverified adapter loader, fixed external arranger, or
production rewrite. Each issue needs its own acceptance evidence; shipping a
dependency does not close its consumers. Keep commit, CI, publication, installed
update and live verification distinct.

Share/Master export profiles (#19) were explicitly rejected and removed in
0.6.15. The issue is closed as not planned; do not reintroduce quality tiers as
a substitute for optimizing the renderer, encoder or transport at equal quality.

Score-plan analysis (#13) was rejected and its issue deleted at the user's
request. Its isolated, uncommitted prototype is not part of main or any release.
The intended feature is traceable variation ancestry and changed creative inputs,
not notation conformance, automatic regeneration or a score-analysis dashboard.
The first lineage increment exposes recorded parent/performance edges as a
versioned read-only graph and an Origin & variations disclosure in recording
details. Archived takes remain connected; missing sources stay explicit; cycles
are reported without repairing or discarding original recipes. This is not yet
named families, promotion, pending-job lineage or idempotent agent operations.

Export profile release 0.6.11 passed main/release CI and desktop publication and
was installed through the normal updater. All 290 baseline tracks, 2,333 protected
files and review settings were preserved. A live 120-frame 4K/60 Share export and
byte-exact original-WAV download passed; both public installer links updated.

The all-track path audit covered 289 tracks, including nine archived tracks. One
additional ad hoc import (Counterweight hardbass) omitted `riff/`; its single
database path was corrected with the original row preserved and WAV hash
unchanged. The repeat audit found all 289 files present and all audio endpoints
returning 200 with matching lengths. No normal-generation path change was needed.

## Active now

- [ ] **E03 — Make YuE2 inference faster as well as smaller.** Measure
  conditioning, semantic generation, acoustic attention/projections, solver
  network evaluations and VAE decode separately. Continue the evidence-based
  AR-to-NAR cache, projection packing/fusion and attention work; verify natural
  EOS, long-context behavior, cancellation and numerical/audio quality before
  selecting a default. Report wall time, compute, peak memory and PCM/code
  parity together. Do not call an operator microbenchmark an end-to-end win.
  The private cache prototype now has a real-context pair: semantic codes
  match, conditioning is removed, but the short reuse run is slower and has a
  slightly higher sampled peak, so it remains opt-in evidence rather than a
  selected optimization. A controlled 1/2/4/8-thread Metal sweep produced
  identical semantic and PCM hashes; four threads was fastest in one cold run,
  but upload variance dominates and no default change is justified. Receipt:
  `data/research-20260919-thread-sweep/results.json`.
- [ ] **E04 — Complete the current YuE2 capability and acceleration audit.**
  Keep the audit grounded in the current YuE2 repository, protocol and native
  runtime. Map every supported input and output to Riff, test the three
  conditioning modes empirically, and turn credible acceleration methods into
  bounded experiments. Keep unsupported controls visibly unsupported rather
  than inventing stem routing, reference-audio conditioning or per-instrument
  guarantees. The September 19 cross-check now binds the audit to the pinned
  audio.cpp spec and separates artist controls from teacher-forcing/debug files;
  acceleration, alternate backends and remaining provenance gates stay open.
- [ ] **E06 — Add acoustic checkpoints and decode-only recovery.** Persist
  validated acoustic latents with shape, type, hashes and model provenance;
  expose Finish audio to the UI and agent queue; preserve interrupted sources;
  and prove identical PCM when the same latents are decoded with the same
  decoder.
- [ ] **E09/C01 — Share a warm model safely across queued work.** Replace the
  unconditional writer/generation exclusion with measured resource admission.
  Permit concurrent writer, planning and generation jobs only when host RAM
  and device memory leave headroom; queue otherwise; cancel one job without
  corrupting another; and measure overlap, latency, memory and total render
  time. Keep the policy compatible with the eventual Elixir/Rust runtime. A
  live 16 GB probe correctly queued before launching either model because host
  availability was 3.44 GB with warning pressure while the native estimate was
  3.60 GB; this is safe admission evidence, not a concurrency result.
- [ ] **V05/V07 — Finish the living sound form.** Reconcile the latest visual
  feedback: the form is currently better overall but may be too subtle, while
  earlier revisions became busy and fast. Preserve the unified shapeform,
  phrase-scale motion, quiet opening, waveform expressed through the contour,
  mineral-hue variation, restrained internal texture, no gold treatment and
  only the corner `riff` textmark. Compare actual playback and exported frames
  at equal seed/audio/time; do not approve a still image in place of motion
  review.
- [ ] **V10/V11 — Optimize export without a quality-choice workaround.**
  The user rejected Share/Master profiles. Remove the split and retain the
  original high-quality settings, original WAV, accurate receipts and saved
  exports. Lowering quality is not a software optimization. Continue measured
  render/encode/transport/storage work at equal quality under #20; the old #19
  profile proposal is rejected. Long-run and independent mobile-player checks
  remain useful acceptance work; see [export performance](export-performance.md).
- [ ] **V11 — Accelerate high-resolution export end to end.** Profile canvas or
  WebGL rendering, pixel readback, frame encoding, transport, FFmpeg and final
  assembly separately. Evaluate raw or lightweight lossless frame transport,
  bounded worker backpressure, direct or hardware encoding and resumable
  checkpoints while preserving deterministic geometry, audio sync,
  cursor-independent progress and cancellation. See [issue #20](https://github.com/Flip-Engineering/riff/issues/20).
  The browser H.264 transport now avoids PNG encoding and a second video
  encode when supported; PNG remains the fallback. Focused checks cover real
  4K/60 output, worker fallback, cancellation and final stream validation.
  Representative short/long benchmarks, quality metrics and resource receipts
  remain open; the transport change alone is not a measured throughput win.
  Completed exports now persist browser stage timings, server write/finalization
  timings and byte counts. The matched benchmark and measurement limitations
  are documented in [export performance](export-performance.md).
  A longer browser run exposed embedded H.264 timing overriding the requested
  frame clock. Input timestamp generation now pins the requested rate; the
  production 4K/60 regression covers two seconds rather than a quarter-second.
- [ ] **D07/D08 — Finish the human-first installer and updater.** Keep the
  clickable web download flow, model-inclusive setup and app-managed updates.
  Complete external-host first launch, model reuse/download accounting,
  post-activation cleanup and storage reporting; keep previous runnable state
  and the library recoverable. Web delivery is the target; App Store
  distribution and Developer ID/notarization are not prerequisites.
  The published v0.6.8 desktop update was installed through the ordinary updater.
  Localhost and both private HTTPS routes reported 0.6.8 with a ready Metal
  engine; a live 30-frame 4K/60 export passed. All 285 recordings and 2,326
  protected files were preserved, as were review connection settings. Both
  public download buttons selected the published installer. External-host
  first-install acceptance remains separate.
- [ ] **Q01 — Run the promised independent review passes.** Review model/API
  contracts and failure/recovery/concurrency first, then review UI/UX and live
  artwork against the actual artist feedback, then verify installation,
  updates, private access, key/library preservation and cross-feature
  regressions. Apply findings and rerun only affected checks.
- [ ] **OPS — Revalidate the current local deployment.** Keep `main`, the
  running Riff service, the web download path and both private Tailscale HTTPS
  routes aligned after each completed delivery. Verify the CLI-only Codex
  remote-control pairing separately. Never put provider keys in the repo.

## Model and product work queued after the active gates

- [x] **Y01 — Empirical YuE2 mode matrix.** Using the accepted Named Voice
  Relay source only as a control, compared exact saved-score `cot=full`, exact
  saved-score `cot=melody`, direct `cot=off`, and the Riff instrumental cue at
  the same seed and bounded duration. Gemini 3.8 Flash direct audio review
  rejected all four: full became a mechanical stutter loop, melody a vocoded
  techno loop, off a slow R&B/trap ballad, and instrumental panting over a
  hip-hop snap. The matrix proves the modes alter failure shape but none
  preserves the accepted foreground; it does not justify calling any mode a
  vocal/timbre or stem control. Receipt: `data/research-20260919-luna-relay-v1/yue2-mode-matrix-receipt.json`.
- [ ] **U08 — Melody freedom and comparison.** Let users release chord
  annotations while retaining valid melody voices; show note, duration, voice,
  harmony and form differences; share the same transformations with writers,
  producer reviews and agents without silently discarding useful ABC.
- [ ] **U09/#10 — Study board and take families.** Present card-simple
  candidates as a branchable take graph: generate bounded studies, compare
  them, retain seed/score/recipe lineage and promote one to a longer take.
  Candidate count remains a user choice.
- [ ] **#11 — Agent orchestration surface.** Give agents the same queue,
  study, replay, review and receipt operations as the studio without DOM
  scripting. Make cancellation, resumability and ownership visible.
- [ ] **#14 — Continuous material workflow.** Connect Sketch, Compose,
  Perform and Finish into one understandable flow while retaining advanced
  controls and plain-language explanations of their effects.
- [ ] **#15 — Resource-aware study workers.** Add sketch/detail/master quality
  tiers, warm reuse and bounded parallel candidates without turning a fixed
  best-of-N policy into a model restriction.
- [ ] **C02 — Gemini cover-analysis import (deferred).** Add an optional audio
  attachment that asks `google/gemini-3.8-flash` for an editable style,
  duration, tempo/key, section map, lyric text and ABC melody/harmony scaffold.
  Expose a faithfulness value as explicit score/section/word constraints with
  field-level confidence, validation and an edit-before-render step. Keep the
  audio opaque and provenance-bound; do not send it directly to YuE2. This is
  the Riff interpretation of the linked cover workflows, without ComfyUI or a
  second transcription model, and remains deferred behind issue #16.
- [ ] **E07 — Per-take provenance and decoder compatibility.** Bind effective
  defaults, runtime, tokenizer, generator/decoder and stage artifacts to each
  take, with compatibility-aware replay and export bundles.
- [ ] **E08 — Native noise and companion-encoder research.** Evaluate raw
  acoustic-noise reuse/interpolation and the companion VAE encoder only with
  valid dimensions, lineage and direct audio comparisons. This does not imply
  inversion, covers or voice cloning.
- [ ] **L01/#7 — Semantic tokenizer, fine-tuning and LoRA research.** Reverse
  engineer the missing audio-to-discrete-semantic path, distinguish it from
  the text/ABC and VAE paths, validate representation compatibility, and only
  then expose compatible adapter CRUD and loading. Keep this a major side
  task; no speculative adapter loader is shipped.
- [ ] **P01 — Elixir/Rust migration.** Move application orchestration and
  distribution toward Elixir, using Rust where native integration needs it.
  Preserve the tested Python boundary during each migration step and retain
  library, queue, Keychain, Tailscale, writer/producer and renderer contracts.
- [ ] **P02 — Bend2 rewrite branch (deferred research track; [issue #18](https://github.com/Flip-Engineering/riff/issues/18)).** Build a separate
  rewrite branch slowly toward feature parity with `main`, without displacing
  the Elixir/Rust mainline or destabilizing the current application. Start with
  a contract map and one reversible study worker, then add library, queue,
  cancellation, model-session, renderer, provider-key, installer and update
  surfaces behind explicit parity gates. Compare correctness, artifact
  preservation, memory, elapsed time, GPU/CPU support and ergonomics at every
  promotion point; merge nothing until it is feature-equivalent for its slice.
- [ ] **D06 — Reduce repeat release-build time.** Investigate exact-input
  compiler/native caches keyed by sources, patches, toolchain and backend;
  preserve reproducibility and update deprecated workflow actions only with
  verified replacements.
- [ ] **NVIDIA — Verify the second backend.** Complete actual CUDA generation,
  memory accounting, cancellation and installer acceptance. Compilation alone
  is not runtime proof; do not change the working Metal path while this is
  unverified.

## Music exploration still open

- [ ] **M-RELAY — Restart bounded studies from the only accepted control.** The
  sole accepted example is `Named Voice Relay — instrument map test`
  (`86f84307b7374308bc7b8071c828ffc0`). Its foreground is a near-monotone,
  changing, fast rhythmic minimal vocal with sparse text; later relays,
  chapters and assemblies are permanently rejected evidence of the wrong
  direction. Run many varied short studies before any full song, and judge
  direct audio with `google/gemini-3.8-flash`; never use Whisper, ASR or
  transcription.
- [ ] **A01 — Artist-selected full-song acceptance.** A reviewed candidate is
  not artist acceptance. Promote a full render only after its bounded studies
  establish identity, euphony, form and a credible ending.
- [ ] **M01 — DECLARE IT replacement.** Continue only if a new Arabic/Korean
  fusion study earns its way through the small-study gate; preserve the
  forceful caller/chant brief without forcing a grand or theatrical treatment.
- [ ] **R01 — ACP/Infrared branch.** Keep the researched historical-materialist
  references and finished candidates available, but explore contrasting
  euphonic forms and form/content relationships rather than repeating the
  rejected weather-heavy or liberal/new-left framing.
- [ ] **M02 — Foldroom.** Revisit the tactile plucked/dub pocket only as a new
  bounded instrumental study; do not inherit the unwanted late vocal or old
  prohibitions that contradicted the actual sound.
- [ ] **M03 — New broadly appealing concept.** Use a fresh programmatic random
  string as a nonliteral attractor, compare several genuinely different
  euphonious directions, and move from studies to a complete song only after a
  chosen direction survives review. Popularity is a goal, not a promise.

## Completed or deliberately closed

- [x] **V09 — Cursor-independent export on the supported worker path.**
  `docs/validation.md` records the completed 180-second, 2160×2160/60 fps
  export without pointer input (10,800 frames). Focused browser checks cover
  task yielding, progress, cancellation and exact PNG frames. Browsers using
  the main-thread fallback retain browser-dependent scheduling; closing or
  suspending the page still interrupts export. Compression and comparative
  throughput remain open under V10/V11 and issues #19/#20.
- [x] Core Riff studio, free/wordless generation, library and saved-sound CRUD,
  secure OpenRouter key storage, structured Gemini producer/review, seed
  retention in variations, score capture/replay, direct-generation/study
  repair, sound-compass interaction, waveform/shape synthesis and high-quality
  MP4 export are shipped in the current release line.
- [x] The current provider is `google/gemini-3.8-flash`; ordinary direct audio
  review does not use Whisper, ASR or any transcription model. The optional
  Gemini cover-analysis exception is planned separately under C02.
- [x] The public contributor rewrite and local `wahargis` → `flip-engineer`
  repository alignment were applied. Historical hosted activity cannot be
  erased by local configuration.
- [x] Issue #9 (captured score attachment across variations) is complete.
- [x] Issue #12 (associative design provenance as a product feature) is closed
  as not planned; random attractors remain a private creative practice.
- [x] Approved nondisruptive storage cleanup is complete; active work and
  uncommitted development were preserved.

## Explicitly out of the current implementation path

- No Whisper, ASR or hidden secondary transcription model. The only planned
  audio-to-notation exception is an explicit Gemini cover-analysis action,
  deferred under C02.
- No promise that ABC labels are discrete stems, timbres or lead-instrument
  routing.
- No rigid external arranger, DAW or sampler pipeline in the current pass;
  that boundary is recorded in issue #17.
- No Engram-style architecture or custom SSD weight streamer without evidence
  that the existing mmap/runtime path is insufficient.
