# Validation

September 12, 2026. Claims below distinguish model inference, application behavior,
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
- Installed v0.4.1 exercised an actual Gemini 3.8 Flash saved-performance proposal
  through the UI and native queue, preserving the open draft, source ID and review
  ancestry. A complete 32-second MP4 exported through private HTTPS in 27.90 seconds
  with matching audio duration and no browser or frame-delivery errors.

Browser/provider fixtures validate interface behavior; they do not establish model
quality. Musical reviews use direct audio requests to the explicitly selected
OpenRouter model. Review notes are subjective observations, separate from measured
runtime and signal properties. Private recordings and provider responses are not
included in release artifacts.

## Platform coverage

CI builds the native CPU backend on Linux and Metal on Apple Silicon, with device
probes. A CUDA 12.8 build checks the NVIDIA compilation path with an explicit target
architecture. CI does not contain a GPU inference acceptance run. NVIDIA hardware
inference and Windows/WSL2 behavior still require hardware validation. Each release
links to the exact workflow results rather than treating configured jobs as passed.
