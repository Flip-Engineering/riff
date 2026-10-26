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
| Linux x86-64 | NVIDIA CUDA or CPU | Python 3.9+, Git, C++ compiler, Python venv support; optional: OpenSSL libcrypto development files for faster model verification |
| NVIDIA acceleration | CUDA | Compatible NVIDIA driver, CUDA Toolkit and `nvcc` on PATH |

Metal requires the selected device to report simdgroup reduction support for
YuE2's `RMS_NORM`. macOS virtual machines can expose a paravirtual GPU without
this capability. Setup and Settings report it as unsupported and offer CPU;
select **CPU**, then **Download and set up**, or run
`python3 setup_engine.py --backend cpu`. Unverified Metal capabilities also block
generation with a clear message before model launch.

Linux engine builds discover dynamic system libcrypto at build time. It is never
bundled or statically linked; without the development headers and library, setup
builds portable SHA-256. After a distro upgrade, a missing system libcrypto,
or one with a different major version, makes the managed engine unavailable
with a clear message to run **Download and set up** in Settings. Rerunning setup
discovers the current system, chooses an engine fingerprint for that ABI and
rebuilds, or selects portable SHA if Crypto is unavailable. Compatible patch
updates remain usable. Models and recordings are retained. Terminal users can rerun `python3 setup_engine.py --backend cpu` (use `--backend cuda` for CUDA),
or explicitly choose `--portable-sha`.

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

On a Linux host requiring shared compile custody, start the studio (or the setup
command) with `RIFF_REQUIRE_COMPILE_QUEUE=1`. The maintained queue wrapper uses
the provisioned queue described in [self-hosted runners](self-hosted-runners.md#native-build-custody).
`RIFF_COMPILE_QUEUE` and `RIFF_COMPILE_QUEUE_STATE` select another queue executable
and state directory. Setting either also requires queue custody; a missing queue,
failed build or invalid receipt fails preparation without a direct build retry.
The host must supply Elixir/OTP with `:json`, CMake, the backend toolchain, `nice`
and `ionice`, and grant the install owner access to the shared queue.

Queued setup and managed source updates use a fresh persistent directory under
`workspace/native-builds/` (or `RIFF_NATIVE_BUILD_ROOT`, set to an owned staging
directory). Put this directory and the caller's `TMPDIR` on the host's build
volume. The wrapper runs with niceness 19, idle I/O priority, and two compile jobs
by default; `setup_engine.py --jobs N` overrides the job count. Its isolated
`RIFF_HOME` keeps the wrapper's replacement of local build output away from the
live workspace's engines. Source inputs receive a content-derived queue identity,
including for release archives without Git metadata. Preparation validates the
source/backend/toolchain and system-crypto receipts and retains `request.json`,
`native-cache-receipts/` and `prepared.json` with the binary digest.

Keep the entire native build directory: the prepared/selected binary and CMake
metadata refer to its persistent paths. Failed and interrupted stages are also
retained for their owner to inspect; setup does not prune old engines or releases.
`--compile-only` skips model downloads, device probes and engine selection.
`--build-only` skips downloads but still probes and selects the engine. Managed
source updates prepare without selecting the new engine, publish pending state
only after successful validation, and activate through Studio's existing idle
reservation when restarting. Existing installations must run code containing this
integration and inherit the queue policy; setting it on an older release does
not retrofit that release. Prebuilt desktop updates do not use this source-build
queue, and unconfigured portable source setups retain their direct build behavior.

### CUDA host compiler selection

CUDA setup and the maintained queue wrapper verify the CUDA host compiler before
configuration or queue admission. `CUDAHOSTCXX` selects an explicit executable;
a compatible choice keeps its literal path, while an incompatible choice fails
with instructions to select a compiler supported by that toolkit. For example,
set `CUDAHOSTCXX=/path/to/supported-g++` for that setup command. `CXX` remains the
ordinary C++ compiler and is only a preferred candidate when no CUDA-specific
choice was made. With no explicit host choice, setup tries available C++, GCC
and Clang executables, including versioned executables on PATH.

Verification uses the selected `CUDACXX` (or `nvcc` on PATH) to preprocess a tiny
CUDA file including `cuda_runtime.h`, so the actual toolkit's host/version guards
decide compatibility. There is no fixed GCC ceiling or GPU-name heuristic in
Riff. This preflight produces no object/device code and needs no GPU or models;
it does not replace CMake's configuration checks or native build qualification.
Host-check bypass flags and competing host overrides in compiler flags are
rejected. See NVIDIA's [documented preprocessing phase](https://docs.nvidia.com/cuda/archive/12.0.0/cuda-compiler-driver-nvcc/index.html#supported-phases).

Setup passes the verified literal compiler to CMake as
`CMAKE_CUDA_HOST_COMPILER` and carries `CUDAHOSTCXX`/`CUDACXX` into the queued
child environment. [CMake uses this host selection when CUDA is first configured](https://cmake.org/cmake/help/v3.28/envvar/CUDAHOSTCXX.html).
An existing CUDA build with another or unspecified host compiler is preserved;
use a fresh workspace or queued staging directory instead of changing its
compiler in place. Receipts still require a nonempty literal compiler path,
its actual bytes, and agreement with the selected host. A generated
`CMAKE_CUDA_HOST_LINK_LAUNCHER` or `CUDAHOSTCXX` environment-variable name cannot
stand in for an empty compiler identity. Selection helper bytes and the resolved
NVCC/host identity are bound into the maintained queue inputs and receipts. The
selection is frozen before the queue key is formed; inner setup and receipt
validation reject host/NVCC replacements even at the same path. CPU and Metal
setup do not invoke NVCC selection or preprocessing.

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
