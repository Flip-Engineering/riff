# Riff graphical setup

The installer is an Elixir/OTP application with a small Rust macOS entry point. A packaged user opens **Riff Setup**, chooses **Install Riff**, and then **Open Riff**. Setup includes the pinned music models and sidecars. When the desktop payload includes the local writer runtime, it includes the writer's weights, vocabulary and configuration too. The installed **Riff.app** starts the existing studio service and opens it without a terminal. Updates use the same verified preparation and activation operation.

This directory contains the implementation and local acceptance fixtures. Web previews use ad-hoc signing and verified release assets. Organization signing and notarization are separate delivery improvements. A control-only development build stops at **Music models are ready**; the complete installer includes the studio, runtime, engine and model downloads.

## Runtime and data boundaries

Desktop update checks normally use GitHub release metadata. If the anonymous API
is rate-limited or unavailable, the updater follows the repository's public stable
release redirect and reads that version's published payload receipt. It requires
the matching version, platform and asset filename, a positive byte count and a
SHA-256 digest; download and payload validation are unchanged. No GitHub login,
developer token or external CLI is required. A missing desktop receipt means the
release is still being published and leaves the installed version unchanged.

The default root is `~/Library/Application Support/Riff`. The package stages application files in `releases/<version>` and immutable runtimes in `runtimes/<digest>`. Recordings, keys, settings and model files stay in `workspace`. Nothing from a developer's private workspace, library or credentials belongs in the public payload.

The current desktop runtime includes a replaceable Python compatibility component for the existing studio. Setup, downloads, activation and the memory admission policy run in bundled Elixir/OTP; native entry points and the operating-system lock helper are Rust. End users do not install Python, Elixir, Homebrew, Git, a compiler or an engine build tool. The portable runtime, native audio.cpp engine and media tools come from the verified payload built by [desktop](../desktop/).

Music sources are read from the payload's hash-verified `app/sources.json`. Every required file is streamed with backpressure and fixed-size hash buffers. Completed files are verified and reused; interrupted `.part` files resume with HTTP ranges after their existing bytes are hashed. A response with the wrong range, length or hash is never promoted to its final filename. Existing mismatched files are retained under an unverified name. Stop affects the operation's own worker and keeps completed downloads.

A bundled Rust port holds the kernel's advisory locks. They are released when their owner exits, so no guessed process lifetime or stale-file deletion decides ownership. The setup operation guard remains held through startup; the activation lock is released after metadata commits and before launching the studio, so the launcher can select its engine under the same lock without blocking setup's readiness check. Launch metadata changes use a durable prepared/committed journal with fsynced backups. A launcher encountering an abandoned prepared journal restores the complete prior selection before starting. Every installed application version also retains its complete runtime receipt; startup fallback checks the previous application with that version's Python, media, control and writer paths, then restores them together. The current source, runtime and recordings are preserved during preparation.

## Developer builds

Build requirements are Elixir, OTP, Rust 1.89 or newer, and macOS build/signing tools. These are build-host requirements only. Dependencies are locked in `mix.lock`.

```sh
mix deps.get
mix check
python3 -m unittest discover -s test -p '*_test.py'
```

The Python test file checks the temporary legacy launcher bridge. It does not implement an alternative installer.

Build the private pinned OpenSSL component with `desktop/build_openssl.exs` first. The packager requires its receipt and verifies its pins and bytes; it never silently falls back to Homebrew OpenSSL. The retained ENGINE API satisfies the shipped OTP NIF ABI, while dynamic engines, loadable modules and automatic configuration are disabled in that component.

```sh
MIX_ENV=prod RIFF_INSTALLER_CRYPTO_RECEIPT=/absolute/build/openssl.json \
  mix run --no-start scripts/package_macos.exs
```

This creates a timestamped control-only `Riff Setup.app` under `_build/macos`. Its `Contents/Resources/runtime` is a self-contained control runtime suitable for the desktop payload. It includes ERTS, native dependencies, notices, and `control-build.json`, which binds source/configuration/native/static/dependency-lock inputs without recording a private build path. Native dependencies stay within that runtime when it is copied elsewhere. Private BEAM source metadata is removed or relocated. The build rejects a source change during packaging.

After building the desktop payload using that control runtime, wrap it:

```sh
MIX_ENV=prod RIFF_INSTALLER_CRYPTO_RECEIPT=/absolute/build/openssl.json \
  RIFF_INSTALLER_BUNDLE_PAYLOAD=/absolute/verified/payload \
  mix run --no-start scripts/package_macos.exs
```

`RIFF_INSTALLER_PACKAGE_PATH` optionally selects a fresh output `.app` path. Setup version metadata comes from the bundled payload. Only manifest-listed files with matching hashes are copied; the packager does not resign or rewrite the already verified payload's bytes. Organization signing/notarization is deliberately not performed by this local-proof script.

## Local interfaces

The GUI listener binds only `127.0.0.1`. Each launch generates a private token in the served page, sent as `X-Riff-Installer-Token` for API requests. Host, origin and fetch-site checks accompany a self-only content policy. Browser requests cannot choose download URLs, package paths, destinations or launch services.

| Request | Operation |
| --- | --- |
| `GET /api/status` | State, required assets, progress and application availability |
| `POST /api/install` / `/api/retry` | Verify, resume, prepare and activate the configured package |
| `POST /api/cancel` | Stop this setup operation |
| `POST /api/open` | Recheck readiness and open Riff; production responds `opened:true, closing:true` before shutting down setup |

`ready` requires every required model and component to pass verification, the desktop to start, and the studio to report the selected version and ready engine. `prepared` means verified files exist but application activation is incomplete. A completed byte counter alone is never readiness.

The headless updater uses the same implementation through a fixed release entry:

```text
bin/riff_installer eval Riff.Installer.Update.main()
stdin: {"protocol":1,"action":"prepare"|"activate","payload":"/absolute/tree","root":"/absolute/install"}
```

It emits JSON lines with `type:progress` and a final `type:result`, `status:prepared|activated|error`. Preparation reads the new package's pins, verifies/downloads all required assets, stages components and runs their startup checks. Activation rehashes prepared models, performs no network download, and commits metadata under the root lock. The calling studio must already hold its idle/restart boundary; this path never calls that studio over HTTP or invokes launchctl. GUI activation has a separate explicit external-installer handoff carrying matching gate and pending-update identities.

After the studio accepts a GUI restart, setup tolerates interrupted HTTP connections while waiting for connection refusal to confirm shutdown. The wait is bounded by `:studio_shutdown_timeout` (30 seconds by default, overridable with `:shutdown_timeout` in the internal desktop options). A timeout or permanent HTTP error preserves the current process; it does not authorize stopping it.

## Acceptance fixtures

`mix test` covers range/redirect/integrity handling, retained files, cancellation, competing installers, authenticated routes, payload staging, writer assets, path escape, interrupted metadata transactions and app-entry preservation. `test/launcher_runtime_test.py` exercises native-lock crash recovery and coherent runtime fallback without using the managed installation. `ui-tests` checks the actual GUI at desktop and mobile sizes.

`test/desktop_gui_fixture.exs` is a release-eval fixture for full local GUI/service acceptance. It requires a fresh private root, a `org.flip-engineering.riff.fixture.*` service label, a separate studio port, and a reviewed descriptor supplied as `RIFF_INSTALLER_FIXTURE_REQUEST`. The descriptor names `root`, `payload`, `models_source`, optional `writer_source`, `service_label`, and `studio_port`. Existing verified model files are linked into that private fixture and rehashed by setup. It does not accept the production root, label or port. The reviewer owns starting and removing only that fixture's launchd job. Set `RIFF_INSTALL_ROOT` to its private root when executing the fixture's persistent `Riff.app` entry.

Small fixture models prove transfer and transaction behavior; they are not generation tests. Real bundled engine/media/writer output, isolated desktop activation, update/restart, and signed public delivery remain separate acceptance evidence.
