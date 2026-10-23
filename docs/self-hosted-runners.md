# Self-hosted runners

Issue #31 moved Riff validation and delivery to self-hosted runners. Linux jobs
run on the homelab. Apple Silicon jobs run on GitHub-hosted macOS runners,
because no self-hosted macOS runner exists or is planned. CUDA builds and
inference run as homelab farm jobs, not in CI.

## Workflow routing

| Workflow / job | Required labels | Execution |
| --- | --- | --- |
| `ci.yml` control, application, browser, native Linux CPU | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | push to `main`, same-repository pull requests, `workflow_call`, `workflow_dispatch` (a dispatch runs every `ci.yml` job) |
| `homelab-linux-validation.yml` | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | manual exact-source dispatch |
| `ci.yml` native Metal | GitHub-hosted `macos-14` (Apple Silicon) | same triggers as the Linux `ci.yml` jobs |
| `apple-silicon-validation.yml` | GitHub-hosted `macos-15` (Apple Silicon) | manual exact-source dispatch |
| `desktop.yml` preview | GitHub-hosted `macos-15` (Apple Silicon) | `main`/`v*` tag/manual desktop workflow |
| `desktop.yml` publish | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | tag or manual publish |
| `pages.yml` deploy | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | Pages workflow |
| `release.yml` validate/release | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | release workflow |


The native Linux CPU job retains the existing solver, CPU-tensor LoRA,
score-replay and acoustic checks, plus the `native-lora` receipt/numerical
evidence upload. The Apple Silicon workflow runs the same CPU-tensor LoRA check
plus the Metal attention check; only the attention check exercises Metal.
Routing the standard jobs to self-hosted Linux does not remove Linux CPU coverage.

The Linux workflows verify provisioned packages and tools rather than installing
them with `apt`. The hosted macOS jobs install FFmpeg with Homebrew when the
image lacks it.

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

The Apple Silicon Metal builds run directly on the hosted macOS runner because
the shared queue is Linux host infrastructure, not a macOS toolchain. Linux and
Apple Silicon qualification remain distinct.

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

## Platform decisions

Decided by the root on 2026-10-23:

- **Apple Silicon**: no self-hosted macOS runner exists and none will be added.
  The operator's Mac is not infrastructure. The `ci.yml` Metal job runs on
  GitHub-hosted `macos-14`; the desktop preview and the manual Apple Silicon
  qualification run on GitHub-hosted `macos-15`, because the desktop package
  targets macOS 15 or later. A hosted macOS run is the supported Apple Silicon
  qualification.
- **CUDA**: no CI runner has direct GPU access. Homelab GPUs are reached only
  through the farm queue and inference infrastructure, so CUDA builds and
  inference (including #24) run as farm jobs. The former `ci.yml` CUDA compile
  probe, which required a `riff-cuda` runner that does not exist, was removed.
- **Linux CPU**: the standard workflows run on `baton2-native-linux-x64` and need
  the runner-group workflow allowlist to include each workflow path. The runner
  must provide the shared compile queue; native builds fail closed without it.
  Recheck the runner inventory and allowlist before dispatch, because both change
  outside this repository.

Linux CPU and Apple Silicon Metal results remain distinct. A Linux pass is not
evidence for the macOS desktop package or Metal engine.
