# Installation and updates

## Graphical desktop installer

The Apple Silicon installer preview includes the application runtime, Metal
engine, media tools and local writer libraries. Open Riff Setup, choose Install,
and follow the model download progress. Setup verifies and reuses existing model
files, then offers Open Riff and installs a persistent Riff app in Applications.
The desktop flow needs no terminal, Python installation or developer tools.
Web previews use ad-hoc signatures; organization signing and notarization remain
separate improvements. See [desktop delivery](../desktop/README.md) for package
availability and validation.

The desktop preview requires Apple Silicon and macOS 15 or newer. Graphical
NVIDIA packaging is separate work; current CUDA support uses the source path
below. Source builds and local packaging proof do not establish a signed public
desktop release.

## Developer and source setup

| Host | Engine | Requirements |
|---|---|---|
| Apple Silicon macOS | Metal or CPU | Python 3.9+, Apple command-line tools |
| Linux x86-64 | NVIDIA CUDA or CPU | Python 3.9+, Git, C++ compiler, Python venv support |
| NVIDIA acceleration | CUDA | Compatible NVIDIA driver, CUDA Toolkit and `nvcc` on PATH |

The native Windows desktop is not currently supported. A Linux environment such
as WSL2 may be used, but WSL2 has not been included in hardware validation.

Download `install.py` from the [latest release](https://github.com/Flip-Engineering/riff/releases/latest)
and run `python3 install.py`. It installs versioned app files, creates a launcher,
and opens the browser studio. Model setup is a separate visible action in Studio
settings. For a terminal-only install use `--no-open`; `--root PATH` chooses the
installation directory.

On macOS the default location is `~/Library/Application Support/Riff`; a
`Riff.command` launcher is added to `~/Applications`. Linux uses
`${XDG_DATA_HOME:-~/.local/share}/riff` and creates a desktop entry. The studio
binds to `127.0.0.1`; it is a personal application, not a public inference server.

### Your other devices

Connect Tailscale on the studio host and your other devices. From the Riff source
or installed release folder, run `python3 network_access.py`, then restart Riff.
For an installed release, set `RIFF_HOME` to its persistent `workspace` directory
when running the helper. It configures a background HTTPS Tailscale Serve proxy
and prints the private address. If that HTTPS port already serves another app,
choose another with `--https-port 8443`; existing routes are preserved.

Use the complete HTTPS address printed by the helper. To also use Riff's usual
port on your other devices, run `python3 network_access.py --https-port 7878`
and restart Riff. This adds `https://your-device.your-tailnet.ts.net:7878` while
keeping any existing HTTPS routes to the same studio usable.

The backend remains bound to loopback. Remote requests require the host's
Tailscale user identity and the configured HTTPS origin, including generation
and downloads. Tagged devices without a user identity do not receive access.
The account and address live in `data/network.json`, outside Git and releases.
The Mac or Linux host must remain awake with Riff and Tailscale running.

Engine setup pins audio.cpp and its patches through `sources.json`. It builds
only YuE2 and the selected accelerator, then probes devices before activation.
Reusing a build verifies the complete patched source; a setup interrupted between
patches can resume. The check uses a temporary Git index and preserves local edits.
Weights download in bounded chunks with resume support and SHA-256 verification.
Build concurrency defaults to half the available CPU threads and can be set with
`python3 setup_engine.py --jobs N`. CUDA architecture selection is configurable
with `--cuda-arch`, for example `--cuda-arch '75;86;89'`; it otherwise detects the
build host's GPU. Existing custom engines, GGUF models and device IDs can be used
through the advanced engine settings.

### Warm engine (resident weights)

By default every take starts a new `audiocpp_cli`, which uploads the YuE2 AR,
NAR and VAE weights to the accelerator again. Warm mode keeps one engine process
running and sends each take to it, so the weights are uploaded once and stay
resident between takes. This reduces repeated bulk transfers to the GPU, which
helps on hosts where large uploads are unreliable (for example GPUs behind a
narrow PCIe link), and removes the per-take load time.

Enable it by adding `"warm_engine": true` to `data/engine.json`, or by posting
`{"warm_engine": true}` to `/api/system/engine`. It takes effect with the next
take. The engine must be built from the current patch set
(`patches/yue2-warm-engine.patch`); an older engine does not advertise
`feature.yue2.keep_resident`, and Riff keeps using one process per take.

- Composition, full renders, performance-only and sound-synthesis takes, saved
  scores and saved performances use the warm engine.
- Finishing a saved sound (acoustic decode) uses a decoder-only session and
  still runs in its own process; the warm engine stays loaded meanwhile.
- Stopping a take stops the warm engine (SIGTERM, never SIGKILL); the next take
  starts a new one. The engine also restarts after a crash, a backend compute
  failure, or a change to the engine binary, model files, backend, device or
  threads.
- While idle, the engine holds its weights in memory (about the size of the
  selected GGUF files plus the VAE). Turning warm mode off stops it before the
  next take. Engine start-up and lifecycle messages go to `data/warm-engine.log`;
  each take's own output still goes to `data/<take>.log`.

## Credentials and optional writing

Desktop setup carries the local writer and FFmpeg. For source installations,
audio reviews and animated MP4 exports need FFmpeg with H.264 encoding. On macOS install it through your package manager;
on Ubuntu use the `ffmpeg` package. Linux credentials need `secret-tool` from
`libsecret-tools` and an unlocked Secret Service session, such as GNOME Keyring.
The application will not save keys to an unencrypted file when that service is
unavailable. Basic generation works without an OpenRouter connection.

Source setup for the local MLX writer requires Apple Silicon and `uv`. From the current release
folder, run `RIFF_HOME=/path/to/install/workspace ./setup-writer.sh`. Source
checkouts can run `./setup-writer.sh` directly. OpenRouter writing uses the saved
connection and does not require MLX.

The player’s MP4 button animates the recording’s seed artwork with its audio.
Choose the complete recording or a passage, enter its in/out times, or use the
marked listening passage. Video settings expose width, height, and frame rate; WAV remains available next
to MP4. Keep the export window open until rendering finishes. Riff streams one
frame at a time into FFmpeg and muxes the original recording as AAC; it does not
capture browser playback or decode a second full copy of the song into browser
memory. Cancellation stops the encoder and removes the incomplete video.

## Update behavior

Riff checks the official Flip-Engineering/riff stable release. It verifies the
archive against the SHA-256 digest supplied by GitHub's release API, checks safe
archive paths, and runs an import preflight before activation. The trust boundary
is the official GitHub repository and HTTPS; this is not an independent signing
system.

New code goes into a separate release folder. Desktop updates carry prebuilt
platform runtimes and engines, verify every file, and use the bundled Elixir
installer to prepare changed model sets. A missing compatible desktop asset
keeps the current app and offers a later retry. Source-installed updates build
changed engines in a separate directory. Current recordings
and the SQLite library stay in the persistent workspace. Restart activates the
prepared release. A failing startup preflight falls back to the previous version;
engine activation records preserve user changes between launches. Old releases
are retained for recovery and are not automatically pruned.

Automatic checks default to once a day. Preparing updates while idle is opt-in;
restart remains a visible action. Desktop preparation can be stopped from Studio
settings. Music, writing, reviews and exports must finish or be cancelled before
activation. Source checkouts receive release information but are updated with Git.
Desktop recovery must restore a matching app and runtime together; use Riff Setup
to repair an installation. Back up
`workspace/data/` and `workspace/outputs/` together. Model caches can be downloaded
again; the library and recordings cannot.
