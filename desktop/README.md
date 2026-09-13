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

The web preview uses ad-hoc signatures. It has no Developer ID signature or
notarization, so macOS may block its first launch under default security settings.
Organization signing and notarization remain a separate delivery improvement;
a developer's personal identity must not be used as a substitute. No quarantine
removal or Gatekeeper exception is part of the installation process. Signing
and real first-install proof are reported separately from packaging test success.

## Manual Actions preview

Open **Actions → Build desktop preview → Run workflow** to build the checked-out
`main` commit. A version-tag dispatch is also supported when the tag equals
`v<VERSION>` and its commit belongs to `main`. The workflow checks the exact
dispatched commit, verifies the tag again before uploading, and rejects source
changes during the build. It runs only in `Flip-Engineering/riff`, through
`workflow_dispatch`; pull requests and ordinary pushes do not start it.

The [workflow](../.github/workflows/desktop.yml) uses GitHub's `macos-15` ARM64
runner and checks its architecture and macOS version before building. GitHub's
standard ARM64 runner has 7 GB of RAM; native compilation uses two jobs and no
model weights are downloaded. [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

All actions are pinned to full commit IDs. The control build selects OTP
28.0.2 and Elixir 1.18.4 with strict version matching, plus Rust 1.89.0. CMake
4.4.3 comes from a size- and SHA-256-verified PyPI wheel, bound to `sources.json`.
Xcode and pkg-config come from the runner image. Application tests use the
runner's full FFmpeg installation, installing it through Homebrew only if absent;
the smaller shipped media tools are built and checked separately. Actual tool
versions and the runner image version are saved in the receipts. The image can change,
so this is a recorded build environment, not a claim that all future binaries
will be byte-identical. [Pinned setup-beam contract](https://github.com/erlef/setup-beam/blob/54075bcc5e249e4758d363f27d099f55d843f124/README.md).

The build uses the committed scripts in this order: native engine, media tools,
portable application runtime, private OpenSSL, sanitized control release,
curated source archive, verified desktop payload, and outer graphical app.
Control, scheduling, packaging, launcher and application tests must pass. The
payload checks relocated component startup, verifies every component receipt,
and includes the local writer libraries. Music and writer model weights remain
the installer's verified downloads.

Successful runs attach one preview artifact containing the setup `.app` in
`Riff-Setup-macos-arm64.zip`, which preserves its executable files, the desktop update archive, the source
archive, component and toolchain receipts, `preview.json`, and `SHA256SUMS`.
The workflow summary links to that artifact. Diagnostic logs are retained
separately even if a build fails; incomplete installers are not uploaded as
successful previews. Preview artifacts expire after 14 days. [Actions artifact
behavior](https://github.com/actions/upload-artifact/tree/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a).

The build has only `contents: read` permission. By default it ends with the
Actions artifact and does not change any release or the app's update feed.

To publish, dispatch the exact version tag with **publish** enabled. The separate
publisher requires a completed, successful `release.yml` run at the same commit
and an existing stable release with its source asset and the ad-hoc preview
notice. That release workflow already requires the source CI suite. The
publisher checks the artifact ID, checksum-list digest, all file hashes, source
commit, component pins and payload receipt before uploading. The released source
archive, rebuilt source archive and payload's source receipt must have the same
digest. Existing assets
with the same bytes are reused; changed assets are rejected, never overwritten.
Use a new version for a changed installer or runtime.

Only this publisher receives `contents: write`. It uses the built-in GitHub
Actions token, so release assets are uploaded by the bot without a personal
token or signing identity. GitHub still records who dispatched the workflow.
It attaches the stable installer ZIP, exact versioned desktop payload, payload
receipt, `desktop-preview.json` and `SHA256SUMS-macos-arm64`. The existing source
archive and its `SHA256SUMS` remain intact. Published assets become available to
the website and desktop updater; the updater checks GitHub's asset digest and
the payload manifest. The workflow never creates a release itself.

The signature check verifies the ad-hoc package and identifies its limits in
the artifact and release notes. CI does not run the graphical installer, model
download or music generation; it does not replace the separate hardware,
installation and listening receipts.

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
