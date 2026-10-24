# Self-hosted runners

Issue #31 tracks moving Riff validation and delivery to self-hosted runners. The
standard workflows no longer declare GitHub-hosted Ubuntu or macOS images.

## Workflow routing

Riff's Linux jobs run on `atari-homelab-riff` (label `riff-linux`), a runner dedicated to Riff on the homelab. It runs as the service user `riffci` from `/mnt/nvme4tb/ci-runners/riff-homelab`, and `riffci` is in the `baton-compile` group so native builds can use the shared compile queue. Baton's native runners (`baton2-native-linux-x64`) are not used by Riff, so Riff jobs do not wait on Baton compiles.

| Workflow / job | Required labels | Execution |
| --- | --- | --- |
| `ci.yml` control, application, browser, native Linux CPU | `self-hosted`, `Linux`, `X64`, `riff-linux` | push to `main`, same-repository pull requests, `workflow_call`, `workflow_dispatch` (a dispatch runs every `ci.yml` job) |
| `ci.yml` CUDA compile probe | `self-hosted`, `Linux`, `X64`, `riff-cuda` | manual `workflow_dispatch` only |
| `homelab-linux-validation.yml` | `self-hosted`, `Linux`, `X64`, `riff-linux` | manual exact-source dispatch |
| `apple-silicon-validation.yml` | `self-hosted`, `macOS`, `ARM64` | manual exact-source dispatch |
| `desktop.yml` preview | `self-hosted`, `macOS`, `ARM64` | `main`/`v*` tag/manual desktop workflow |
| `desktop.yml` publish | `self-hosted`, `Linux`, `X64`, `riff-linux` | tag or manual publish |
| `pages.yml` deploy | `self-hosted`, `Linux`, `X64`, `riff-linux` | Pages workflow |
| `release.yml` validate/release | `self-hosted`, `Linux`, `X64`, `riff-linux` | release workflow |


The native Linux CPU job retains the existing solver, CPU-tensor LoRA,
score-replay and acoustic checks, plus the `native-lora` receipt/numerical
evidence upload. The Apple Silicon workflow runs the same CPU-tensor LoRA check
plus the Metal attention check; only the attention check exercises Metal.
Routing the standard jobs to self-hosted Linux does not remove Linux CPU coverage.

The Linux workflows verify provisioned packages and tools rather than installing
them with `apt` or Homebrew. The CUDA container installs its own build packages
inside the pinned image.

## Native build custody

Every Linux native engine build in `ci.yml` and
`homelab-linux-validation.yml` runs through
`scripts/run_native_build_queued.sh`. The wrapper submits only
`scripts/build_cached_native.exs` to the host shared compile queue at
`/var/tmp/baton2-compile-queue/compile-queue-runner` with state root
`/var/tmp/baton2-compile-queue`. `RIFF_COMPILE_QUEUE` and
`RIFF_COMPILE_QUEUE_STATE` select another provisioned queue. A missing queue or
state root is a hard failure; the wrapper has no direct-build fallback.

The solver, CPU-tensor LoRA, score-replay and acoustic checks run after the
queued build as ordinary caller steps. They do not hold compilation custody.
The queue artifact is the complete `engines` tree plus
`native-cache-receipts/`, so a cache hit installs the same build and receipt
that a fresh execution produces.

The CUDA job runs in the pinned NVIDIA container and therefore needs the host
queue mounted at the same path. It must preserve the queue's ownership and
state-file permissions; the wrapper still fails closed if the mounted queue is
unavailable. The Apple Silicon Metal validation stays direct because the shared
queue is Linux host infrastructure, not a macOS toolchain. Linux and Apple
Silicon qualification remain distinct.

## Source trust

Standard `pull_request` jobs run only when the pull request head belongs to
`Flip-Engineering/riff`. That job-level guard is advisory, **not a security
boundary**: a fork pull request can edit the workflow file. Before adding
`ci.yml` to the runner-group allowlist, require GitHub approval for all outside
collaborators and review workflow diffs; otherwise keep pull-request jobs off
self-hosted and use the manual exact-source workflow. Manual workflows record the
runner name, OS, architecture and source SHA, and the Apple Silicon workflow
records the active toolchain after setup. Keep the organization runner-group
workflow allowlist restricted to reviewed workflow paths.

## Current provisioning gaps

The organization inventory recorded on 2026-10-20 found two online Linux runners
with `riff-linux`. It found no `macOS`/`ARM64` runner and no
`riff-cuda` runner. At that time the `baton2-native-homelab` runner group allowed
only `homelab-linux-validation.yml`; `ci.yml`, `desktop.yml`, `pages.yml`,
`release.yml` and `apple-silicon-validation.yml` still require an organization
owner to add them to the group workflow allowlist. Recheck the inventory and
allowlist before dispatch because both change outside this repository.

- **Linux CPU**: route is defined; the standard workflow cannot use the runner
  group until the allowlist includes `ci.yml` (and `desktop.yml`, `pages.yml`,
  `release.yml` for delivery). The runner must provide the shared compile queue;
  native builds fail closed without it.
- **Apple Silicon Metal**: **platform gap**. `desktop.yml` preview and
  `apple-silicon-validation.yml` route to `self-hosted, macOS, ARM64`, but no
  such runner is registered. They will wait for a runner; they must not be
  replaced with a hosted macOS run. Desktop packaging and Metal qualification
  are unverified until a remote Apple Silicon runner is provisioned and an
  actual run records its identity and toolchain.
  The desktop preview also assumes `rustup` with Rust 1.89, `pkg-config`,
  Xcode/Metal, CMake, FFmpeg/FFprobe, Python, Node and Elixir are in the runner
  PATH; provision and verify them before the first desktop run.
- **CUDA**: `ci.yml` CUDA is manual-dispatch only and requires `riff-cuda`. No
  such runner is registered; it is not part of normal Linux CI and has no hosted
  fallback. The CUDA runner must mount `/var/tmp/baton2-compile-queue` into the
  pinned container and preserve queue ownership; without that mount the queued
  build fails before compilation rather than running outside custody.

Linux CPU and Apple Silicon Metal results remain distinct. A Linux pass is not
evidence for the macOS desktop package or Metal engine.
