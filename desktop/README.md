# Desktop delivery

The desktop package carries the application, Elixir/ERTS control service, native
audio.cpp engine, media tools and a portable compatibility interpreter with the
local writer's libraries. Riff Setup downloads and verifies the separately
licensed music and writer models, then installs a persistent Riff app entry.
People using Riff do not install compilers, language runtimes or terminal tools.

These scripts are for release builders on Apple Silicon, with macOS 15 or newer,
Elixir/OTP 28, Rust 1.89+, Xcode command-line tools, CMake and pkg-config. Python
is a transitional application component; new orchestration lives in Elixir and
small native helpers in Rust. The current desktop payload targets Metal. CUDA
source builds are validated separately; a graphical NVIDIA desktop package and
actual NVIDIA inference acceptance remain delivery work.

## Pinned components

`components.json` records immutable source/runtime URLs, byte counts and SHA-256
digests. `writer-wheels-macos-arm64.json` binds every binary dependency to the
writer lock file and its published PyPI digest. Builds install only those
verified wheels, with dependency resolution and compilation disabled.

The native build verifies the full patch set against `sources.json` and exports
a temporary Git index before compiling. Untracked source cannot enter CMake's
source discovery. Native receipts bind the result to the complete source
fingerprint. Every payload file has a size, digest and executable bit; the
runtime identity hashes the ordered runtime records.

Mach-O inspection requires each non-system dependency to resolve inside the
payload. Riff-owned binaries must contain no developer home/build paths.
Verified vendor wheels retain their upstream public CI paths and license
notices. FFmpeg's GPL sources, build instructions and x264/LAME sources accompany
the media binaries. The control release includes its OTP, Elixir, dependency,
OpenSSL and Rust notices.

## Build and inspect

Choose fresh destinations for each build. The examples below are release-host
commands, not installation instructions:

```sh
elixir desktop/build_engine.exs --source audio.cpp --output desktop/.build/engine
elixir desktop/build_media.exs --output desktop/.build/media
elixir desktop/build_python.exs --output desktop/.build/python
elixir desktop/build_openssl.exs --output desktop/.build/openssl
```

Build the control release using [the installer instructions](../installer/README.md),
passing the private OpenSSL build receipt. The control runtime records all its
source input hashes; packaging rejects a stale scheduler or installer build.
Package a curated source release using `scripts/package_release.py`, then run
`desktop/package.exs` with its archive, SHA-256 and exact commit, the native
binary/source/build receipt, media folder/build receipt, Python folder/build
receipt, and sanitized control runtime. See the strict named options at the top
of the script. The package rejects a changed or incomplete component. Its
preflight runs with a system-only tool path and checks actual Python startup,
media tools, relocated crypto and the Elixir scheduler entry point.

`desktop/check_payload.exs --payload … --models … --image … --output …` performs
an actual short native generation, WAV-to-MP3 conversion and PNG-to-H.264/AAC
60 fps export, plus SQLite/TLS checks. It requires already verified model files;
it writes only into a fresh acceptance directory. This is component acceptance,
separate from the graphical first install and update tests.

## Installer and updates

Build the graphical setup app with `RIFF_INSTALLER_BUNDLE_PAYLOAD` pointing to
the verified payload. The setup app contains everything except the model files,
which it downloads with visible, resumable progress. Model terms are presented
before downloading. Installed application data and Keychain credentials are
kept separately from replaceable releases and runtimes.

`desktop/archive.exs --payload … --output … --epoch …` produces
`riff-v<VERSION>-macos-arm64.payload.tar.gz` and a checksum receipt. Archives
contain regular files under `app/` and `runtime/`, plus `manifest.json`, with
neutral ownership and stable timestamps. Existing archives are never overwritten.
The app selects this exact platform asset from the official GitHub release,
checks its published digest and every extracted file, then uses the bundled
Elixir prepare/activate interface. A missing desktop asset preserves the current
installation and offers a retry; it never starts a compiler on the artist's
computer. Activation waits for all music, writing, review and export work to
finish. Failed or cancelled preparation retains reusable verified downloads.

Local builds use ad-hoc signatures for development proof. Public click-to-open
distribution still requires an appropriate organization Developer ID signature
and notarization of the complete app, after payload assembly. A developer's
personal identity must not be used as a substitute. Do not remove quarantine or
weaken Gatekeeper as an installation step. Signing and real first-install proof
must be reported separately from source, unit or packaging test success.

## Validation

```sh
elixir desktop/test/build_test.exs
elixir runtime/test/admission_test.exs
elixir runtime/test/resources_test.exs
```

The installer has its own Elixir transaction/downloader tests, Rust helpers,
graphical browser fixtures and crash-recovery cases. Desktop updater integration
tests exercise cancellation ownership, archive tampering and the actual control
protocol. Keep actual model overlap, graphical install, live data preservation
and hardware performance receipts separate from fixture results.
