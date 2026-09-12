# Validation

September 11, 2026. Claims below distinguish model inference, application behavior,
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
- Browser workflows for playback, library CRUD, writing, saved sounds, reviews,
  composition, note selection, transposition, audition, MIDI export, theme choice,
  narrow layouts and settings persistence.
- Browser export draws from the same seed geometry as the SVG cover, responds to
  play/pause/seek, and produces an MP4 whose decoded first frame matches the Live
  sound renderer within codec tolerance. Desktop, 390px, and 320px layouts pass.
- A real 28-second recording was played, sought, and exported through private
  Tailscale HTTPS at 1280×990 and 24 fps. The MP4 contains H.264 video and the
  complete AAC-encoded recording; the artwork includes only the riff wordmark.
  Audio range requests returned 206 and a foreign-origin export request returned 403.
- Installation from the packaged archive in a temporary workspace, real HTTP
  startup, and fallback from a deliberately broken candidate release.

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
