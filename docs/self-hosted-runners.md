# Self-hosted runners

Issue #31 moved Riff validation and delivery to self-hosted runners. Linux jobs
run on the homelab. Apple Silicon jobs run on GitHub-hosted macOS runners,
because no self-hosted macOS runner exists or is planned. CUDA builds and
inference run as homelab farm jobs, not in CI.

## Workflow routing

Riff's Linux jobs run on five homelab runners with the label `riff-linux`: `atari-homelab-riff` and `atari-homelab-riff-2` to `atari-homelab-riff-5`. All five run as the service user `riffci` with `HOME=/mnt/nvme4tb/ci-runners/riff-homelab`. The first runner's directory is that `HOME`; the others are `runners/2` to `runners/5` under it, and each has its own `_work` directory. `riffci` is in the `baton-compile` group so native builds can use the shared compile queue. Baton's native runners (`baton2-native-linux-x64`) are not used by Riff, so Riff jobs do not wait on Baton compiles.

## Isolation between concurrent jobs

The five runners take jobs at the same time on one host, so anything outside a runner's own `_work` directory is shared between concurrent jobs. That includes `/tmp` and the toolchains in the shared `HOME` (`.rustup`, `.cargo`, `.mix`, `.hex`, `.cache/ms-playwright`, `.npm`).

- The first step of every Linux job in `ci.yml` and `homelab-linux-validation.yml` writes `TMPDIR=$RUNNER_TEMP`, `MIX_HOME=$RUNNER_TEMP/mix` and `HEX_HOME=$RUNNER_TEMP/hex` to `GITHUB_ENV`. `RUNNER_TEMP` is the runner's own `_work/_temp`, which the runner empties for each job. `erlef/setup-beam` therefore installs Hex and rebar into the job's own `MIX_HOME`; before this, a concurrent `mix local.hex --force` removed `.mix/archives/hex-2.6.0` while another job's `mix deps.get` was loading it. The compile queue passes the caller's environment to the queued build, so the build's temporary directory is also inside the job.
- Code and tests name their temporary directories with random bytes. `System.unique_integer/1` is unique only inside one BEAM VM: separate VMs running the same code produce the same values, so two jobs created the same `/tmp/riff-cache-19970`.
- The native build places the sccache server socket at `$TMPDIR/riff-cache-<pid>-<16 hex digits>/server.sock`. A Unix socket path can be at most 107 bytes on Linux. With the directory layout above the path is at most 106 bytes; a runner directory with a longer path needs a shorter `TMPDIR`.
- Cargo locks its registry and package cache, npm's cache is safe for concurrent use, and `playwright install` takes a lock in its browser cache, so those stay shared. `rustup toolchain install` in the shared `.rustup` is a no-op while the stable toolchain is current. When a new stable release appears, several control jobs install it into the same `.rustup` at once; if that produces failures, give each runner its own `RUSTUP_HOME`.
- Tests wait for the fixture's own completion signal and have no wall-clock deadline of their own, because load on the shared host (load average 30 to 70 on 64 cores) decides how long each step takes. Python helpers in `tests/fixture_waits.py` also fail as soon as the thread or process that would produce the signal has exited. ExUnit's per-test timeout stops a hang in the Elixir tests; for the Python and Playwright tests the job timeout does.

| Workflow / job | Required labels | Execution |
| --- | --- | --- |
| `ci.yml` control, application, browser, native Linux CPU | `self-hosted`, `Linux`, `X64`, `riff-linux` | push to `main`, same-repository pull requests, `workflow_call`, `workflow_dispatch` (a dispatch runs every `ci.yml` job) |
| `homelab-linux-validation.yml` | `self-hosted`, `Linux`, `X64`, `riff-linux` | manual exact-source dispatch |
| `ci.yml` native Metal | GitHub-hosted `macos-14` (Apple Silicon) | same triggers as the Linux `ci.yml` jobs |
| `apple-silicon-validation.yml` | GitHub-hosted `macos-15` (Apple Silicon) | manual exact-source dispatch |
| `desktop.yml` preview | GitHub-hosted `macos-15` (Apple Silicon) | `main`/`v*` tag/manual desktop workflow |
| `desktop.yml` publish | `self-hosted`, `Linux`, `X64`, `riff-linux` | tag or manual publish |
| `pages.yml` deploy | `self-hosted`, `Linux`, `X64`, `riff-linux` | Pages workflow |
| `release.yml` validate/release | `self-hosted`, `Linux`, `X64`, `riff-linux` | release workflow |


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
- **Linux CPU**: the standard workflows run on Riff's own runner, `riff-linux`
  (#49, PR #52). Riff is a public repository, so the runner must be in a runner
  group that allows public repositories and selects Riff; the org Default group
  does not, and jobs then queue with no runner assigned. The group's workflow
  allowlist must include each workflow path. The runner
  must provide the shared compile queue; native builds fail closed without it.
  Recheck the runner inventory and allowlist before dispatch, because both change
  outside this repository.

Linux CPU and Apple Silicon Metal results remain distinct. A Linux pass is not
evidence for the macOS desktop package or Metal engine.
