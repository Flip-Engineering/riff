# Validation

September 13, 2026. Claims below distinguish model inference, application behavior,
and compilation. The browser is an isolated Chromium instance; tests do not attach
to a personal browser or use stored credentials.

## Measured inference

An Apple M4 MacBook Air with 16 GB unified memory ran the pinned YuE2 Q4 main model
and F16 VAE through audio.cpp Metal. Earlier short 12–30 second previews measured
approximately 2.70–2.72 GiB peak process footprint at guidance 1.0. A 12-second
Reedlight take completed in 28.22 seconds; the idle Python studio afterward measured
19.44 MiB physical footprint. Browser and whole-system memory are excluded.

A fresh managed Metal build completed from the pinned source and patches, and its
device probe found the M4 Metal accelerator. A real score-only planning request
completed in 18.22 seconds with 654 ABC tokens, without rendering a WAV. The decoded
ABC was opened in the composition workspace. Subsequent 28-second studies rendered
from supplied multi-voice ABC, exercising score-guided music generation.

Long performances are materially more expensive: earlier roughly five-minute
renders peaked around 8.85–12.11 GiB depending on planning and solver settings.
Short previews are the default development workflow; their measurements are not a
RAM guarantee for a full song. Process peaks are sampled and can miss the final
interval. Linux process metrics exclude GPU memory.

### Native synthesis changes in 0.5.0

A full-input native run completed 5,038 performance frames and 201.5187 seconds
of stereo audio through the ordinary queue at 10,409,618,136 bytes peak process
footprint. It used the Q4 model, F16 VAE and one midpoint solver step. This proves
the complete pipeline on the 16 GB M4; one step is an engineering comparison,
not a finished musical rendition.

Re-rendering that saved performance with identical conditioning and seed measured:

| Measurement | Previous engine | Direct retained cache |
| --- | ---: | ---: |
| Peak process footprint | 6,585,949,408 bytes | 3,387,855,720 bytes |
| Acoustic-stage footprint | 6,585,949,408 bytes | 2,625,442,160 bytes |
| Complete render wall time | 146.91 seconds | 177.43 seconds |

The native code stream and decoded PCM were byte-identical. The rewrite writes
the retained F16 conditioning cache directly and frees the completed prefill
graph before acoustic synthesis. It removes duplicate K/V tensors rather than
reducing the performance length or solver count. These sequential wall times
varied with device conditions and do not establish an overall speedup; the memory
result is separate from throughput. The 3.39 GB peak describes saved-code
re-rendering, not fresh semantic generation or whole-system memory.

Operator profiling identified attention and quantized projections as the largest
acoustic costs. A specialized Metal F16 attention kernel reuses K/V loads across
16 queries. The existing path remains for masked attention, other head dimensions
and query counts below 4,096. Shorter-query benchmarks favored the original tile. A warmed, alternating comparison using the same graph and
allocations measured 359.10 ms versus 352.53 ms for 5,040 queries and 18,929 keys,
about 2% faster for that operator. All output floats matched exactly, including
tests at 37, 128 and 202 queries and partial final tiles. The final dispatcher retains the earlier tile for these shorter shapes. Run the reproducible
comparison with `python scripts/check_native_attention.py --build PATH`; set
`GGML_METAL_WIDE_ATTENTION_DISABLE=1` to select the earlier kernel. The check reports
a skip if Metal execution is unavailable.

The macOS CI runner exposes an Apple Paravirtual Metal device without SIMD-group
matrix multiplication. The benchmark queries support for the actual attention
operation before executing; this virtual device receives an explicit skip, while
the real M4 runs every numerical case. Native compilation and the analytic solver
checks still run on CI. Failed benchmark processes retain their diagnostics.

### Acoustic computation and multistep integration

For performance length F, conditioning length P and model width D, each acoustic
network evaluation contains attention proportional to F(P+F)D and projections
proportional to FD², repeated across transformer layers. Midpoint requires 2S
network evaluations for S steps. The optional second-order Adams–Bashforth solver
requires S+1, using midpoint for its first step and reusing the previous velocity
thereafter. It reduces the number of expensive network calls; it does not change
the attention algorithm's quadratic dependence on performance length.

A 32-second saved-performance comparison at 16 steps measured 32 versus 17
network evaluations. Acoustic graph computation fell from 37.33 to 19.73 seconds;
the complete native render fell from 49.08 to 31.81 seconds. A 32-step AB2 render
used 33 evaluations and took 49.96 seconds. All three retained identical semantic
codes and duration, with different PCM. A blinded direct-audio Gemini 3.8 Flash
comparison found no clear audible differences and reported uncertain confidence.
A second 28-second chant study used the same 32-versus-17 evaluation counts; its complete render measured 45.59 versus 27.36 seconds. Its blinded Gemini review also reported no material audible difference. These two reviews are study-specific listening evidence, not general quality equivalence.
Midpoint remains the default; the artist and producer can select either method.

The actual native integrator is tested against analytic constant, time-dependent
and exponential ODEs, including backward integration, second-order convergence,
evaluation counts, progress, invalid shapes and non-finite output. Run
`python scripts/check_native_solver.py --build PATH` without model weights.
The [linear multistep derivation](https://arxiv.org/abs/1610.08417) describes the
underlying method family; Riff's audio measurements assess this implementation
on YuE2 separately.

## Producer variations and studio refresh in 0.6.5

Producer recommendations retain the source recording’s exact seed, including zero
and the full 63-bit range. The structured response schema and application validator
both preserve it, and the saved proposal, Edit in studio and one-click generation
carry that value through. The artist can still edit the seed manually. Independent
song writing remains free to choose a new seed. Reopening an older complete
recommendation applies the source seed to its returned recipe; historical
recordings and stored review rows are not rewritten.

The served page carries its asset-build identity. When a newer studio is running,
an explicit Refresh studio action preserves the tab’s creative inputs, undo,
selected recording, paused playback position and library context. It waits for
playback, exports, queue submission and active writing or score revision to finish;
a pending score suggestion stays available until applied or dismissed. The
handoff uses tab-scoped storage with an explicit creative-field list and excludes
API keys and account settings. A storage failure leaves the current tab intact.
A page loaded before this feature needs one ordinary refresh to receive it.

Focused checks cover provider responses proposing a blank or different seed,
manual seed editing, delayed real HTTP writing and score requests, a real PNG
worker/FFmpeg export, raw empty/numeric draft values, tab-specific restoration,
storage failure, credential exclusion and desktop/mobile refresh-button access.

## Application checks

- Python 3.9 and 3.14 import/startup preflight, without model files.
- Backend tests for queue ownership, cancellation, recovery, audio ranges, library
  persistence, credential handling, native request construction, score-only jobs,
  composition requests, update activation and archive validation.
- Strict producer recipes include supplied/generated symbolic context, preserve
  held lyrics, reject incomplete responses, and pass the normal generation validator.
- Tailscale access tests cover exact origin and user checks, rejected spoofed or
  missing identities, loopback proxy boundaries, and preservation of other Serve routes.
- Real FFmpeg exports test PNG frame order, same-origin requests, cancellation,
  H.264 video with AAC audio, complete recording duration, and ranged downloads.
  Passage tests decode distinct source frequencies to verify the selected audio,
  sample-aligned bounds and matching frame count. Existing export history keeps
  complete-recording bounds through the database upgrade.
- Browser workflows for playback, library CRUD, writing, saved sounds, reviews,
  composition, note selection, transposition, audition, MIDI export, theme choice,
  narrow layouts and settings persistence.
- Browser export draws from the same seed geometry as the cover, responds to
  play/pause/seek, and produces an MP4 whose decoded first frame matches the Live
  sound renderer within codec tolerance. Desktop, 390px, and 320px layouts pass.
- Native queue tests cover ordering, schema migration, unchanged creation times,
  active take preservation, and recovery after temporary storage errors. Browser
  tests exercise ordering with the keyboard and preserve focus across refreshes.
- A/B browser checks preserve playback position, pause state and the composition
  draft, exercise passage loops and input differences, and test immersive playback
  and Escape focus restoration at desktop and narrow widths.
- Motion tests distinguish bass, voice and air energy, stereo balance and width,
  and transient attacks; silence remains still. Renderer checks cover deterministic
  seeking, independent responses, and the sole corner wordmark at multiple ratios.
  Both the WebGL2 renderer and Canvas fallback have been exercised. A local M4
  browser measurement of the contour renderer at 1280×990 averaged 1.24 ms per WebGL frame (1.4 ms at p95);
  this measures drawing, not PNG encoding, network delivery or whole-video export.
- The motion cache includes signed filtered waveform samples with tests for
  silence, amplitude and opposite-phase stereo. Surface and movement settings
  persist without changing the generation draft and remain fixed during export.
- Native saved-code reuse reproduced a 32-second source WAV byte for byte with
  identical inputs. Removing per-layer cache upload scratch buffers also preserved
  both music codes and the waveform exactly. A long-score allocation probe with
  9,476/7,937-token guided prefixes and a 10,500-token music capacity peaked at
  10.54 GB after these lifetime changes and F16 Metal caches; the earlier run
  exceeded 21 GB. This is allocation evidence, not a completed long-song render
  or a speed claim. F16 cache sampling can differ from the older F32 cache.
- Performance reuse checks reject missing, changed and out-of-library artifacts
  before queue submission. Producer proposals can re-render the supplied saved
  performance or compose a score through normal queue validation, preserving
  the draft and ancestry. Browser checks cover written score duration and
  individual voice audition.
- Performance-only generation and completed stages from interrupted renders can
  be opened from history and rendered through the same queue. Recovery preserves
  partial files without offering them as complete performances. Tests cover exact
  code hashes, source lineage, actual duration, original job status, score retention,
  shared draft undo and keyboard focus across history refreshes.
- A real 28-second recording was played, sought, and exported through private
  Tailscale HTTPS at 1280×990 and 24 fps. The MP4 contains H.264 video and the
  complete AAC-encoded recording; the artwork includes only the riff wordmark.
  Audio range requests returned 206 and a foreign-origin export request returned 403.
  The same browser checks pass on explicit HTTPS port 7878, and a separate
  Tailscale device reached the studio successfully. Both HTTPS addresses retain
  the account and origin checks.
- Installation from the packaged archive in a temporary workspace, real HTTP
  startup, and fallback from a deliberately broken candidate release. Five real-Git
  cases cover overlapping patches, idempotent prepared builds, interrupted setup,
  checksum failure, and preservation of tracked edits, untracked files and the
  real index. The complete source check resolves a false rejection by the older
  updater when a later patch changed an earlier patch’s context.
- The v0.4.2 candidate passed 85 Python tests and the complete browser workflow
  suite. Its waveform renderer also completed a headed 240-frame measurement
  during local music inference: 5.22 ms mean and 6.30 ms p95 at 1280×990. This
  concurrent measurement is separate from the earlier isolated drawing benchmark.
- The v0.4.3 refinement moves signed-waveform history into the surface geometry.
  Checks distinguish waveform polarity and history in the mesh itself, verify
  silence and constant-offset behavior, and keep the aperture clear. Real playback
  frames were inspected across four mineral palettes and the Surface range. A
  headed 240-frame drawing measurement averaged 1.25 ms, with 1.40 ms at p95, at
  1280×990; no model inference was running during this measurement.
- The integrated v0.5.0 candidate passed all 89 Python tests and the complete browser suite, including synthesis-method selection, persistence, producer recipes and older-engine rejection.
  Delayed waveform pressure now changes contour spacing, curvature and material
  light together. Actual playback frames were inspected across four mineral
  palettes. Existing mesh, aperture, silence, seeking, reduced-motion, Canvas
  fallback and full/passage MP4 checks pass with the shared renderer.
- Installed v0.4.1 exercised an actual Gemini 3.8 Flash saved-performance proposal
  through the UI and native queue, preserving the open draft, source ID and review
  ancestry. A complete 32-second MP4 exported through private HTTPS in 27.90 seconds
  with matching audio duration and no browser or frame-delivery errors.

- v0.5.2 repairs a hidden-score submission in Direct mode. Actual 8-second Direct
  and 6-second study generations completed with `cot=off` and no ABC argument,
  while preserving the score draft. Browser tests also cover selected lyrics,
  stale selections, score-only transitions, independent study controls and errors.
- MP4 now defaults to 3840×2160 at 60 fps. A real 32-second export produced 1,920
  H.264 frames and complete 31.9987-second AAC audio, preserving the studio draft.
  This first high-resolution run took 412.36 seconds; export is not real-time.
  Same-seed artwork comparison rejected an overly dark first material candidate;
  revised lighting retains contour legibility with internal mineral hue variation.

- v0.5.3 passes 97 application tests and the complete browser workflows. Compass
  coverage includes SVG coordinate mapping at desktop and phone widths, actual
  touch events, cancelled gestures, pointer capture outside the pad, keyboard
  input, overlapping writing requests and preserved drafts. AI writing passes the
  complete generation contract through the editable studio and native queue.
  An actual Gemini 3.8 Flash proposal produced an 11.9987-second native take with
  ABC, sampling overrides and AB2; the local writer also returned a valid recipe.
- Motion comparisons distinguish temporal smoothing from material rendering.
  The first smoothed candidate was overly still. Broad deformation was restored
  while independent fast ripples remained removed. Source-time filtering retains
  waveform polarity, settles in silence and stays consistent across analysis
  rates and seeks. Both actual Metal/WebGL and Canvas fallback rendering are
  exercised: headless Chromium's default can otherwise hide the shader path.
  Matched 16-second GPU exports produced all 960 frames and complete audio. Direct
  frames and decoded exports show the same changing shape; provider opinions
  about its subtlety remain subjective, not artist acceptance.

Browser/provider fixtures validate interface behavior; they do not establish model
quality. Musical reviews use direct audio requests to the explicitly selected
OpenRouter model. Review notes are subjective observations, separate from measured
runtime and signal properties. Private recordings and provider responses are not
included in release artifacts.

## Saved compositions and rendering

The installed 0.6.3 studio completed score-only capture, exact saved-score reuse,
performance-only generation and audio rendering through its normal controls. The
82 native score IDs and 200 music codes remained unchanged across those stages.
The resulting eight-second recording played successfully, and an actual Gemini
3.8 Flash audio review returned a complete recommended take retaining its saved
score and performance references. Model/parser validation includes 92 CPU cases,
16 exact conditioning-prefix comparisons and paired short guided/unguided neural
renders with identical replayed score IDs, music codes and decoded PCM. These
checks establish those runs, rather than universal equality across engines or
hardware. The application and control suites also exercise captured tokenizer
snapshots, interruption recovery, cancellation, editor attachment/Undo and
provider generation contracts.

The 0.6.4 artwork comparison keeps the existing material and strengthens connected
fold deformation. Four mineral palettes, matched audio timestamps and a short
continuous capture were reviewed; an initially darker candidate was rejected.
Audio-clock smoothing and oscillation rates are unchanged, while the form travels
slightly farther in response to the music. The selected geometry remains still
in silence. Artistic preference remains separate from functional validation.

The renderer now defers Canvas shading during successful WebGL frames. It retains
per-frame depth ordering because omitting that order changed a few tied-depth
fallback pixels. The corrected implementation passes 192 public-scene comparisons,
48 GPU-history-to-Canvas data comparisons, the 14-frame exact tie reproducer and
52 broader pixel comparisons, including real context loss and restoration. In
alternating 120-frame batches, median CPU scene preparation fell from 0.938 to
0.644 ms. This narrow stage measurement excludes complete drawing, PNG encoding,
transport, video compression and music inference.

Video export uses a dedicated PNG worker when the browser provides Worker,
OffscreenCanvas.convertToBlob and createImageBitmap. One frame remains in flight;
encoding does not wait for animation-frame or idle callbacks. In a headed
Chromium comparison at 3840×2160 and 60 fps, stationary/moving/stationary pointer
runs measured approximately 51 ms median encoding per frame throughout. The
original main-thread path measured about 52 ms with a stationary pointer and
135 ms while it moved. Each worker run produced all 60 frames. This reproduces
interaction sensitivity; its direction differed from the artist's original
report, and it does not establish a universal stationary-export speedup.

Browsers without those worker capabilities, or where initialization fails, retain
the canvas.toBlob fallback and its browser scheduling behavior. Closing or
suspending the page still interrupts a browser-driven export. Worker checks cover
exact decoded PNG pixels, startup and feature fallback, cancellation during each
encoding stage, late callbacks, worker errors, FFmpeg cancellation and retry.

## Platform coverage

CI builds the native CPU backend on Linux and Metal on Apple Silicon, with device
probes. A CUDA 12.8 build checks the NVIDIA compilation path with an explicit target
architecture. CI does not contain a GPU inference acceptance run. NVIDIA hardware
inference and Windows/WSL2 behavior still require hardware validation. Each release
links to the exact workflow results rather than treating configured jobs as passed.
