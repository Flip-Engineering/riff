# Self-hosted runners

[Issue #31](https://github.com/Flip-Engineering/riff/issues/31) tracks migration
of validation and delivery to self-hosted runners. Linux jobs run on the homelab.
The October 25 operator instruction requires self-hosted Apple validation too,
superseding the October 23 hosted-macOS exception. No operator-verified remote
Apple runner identity, custom label or group/workflow access has been supplied
for this migration. Current Apple qualification is therefore **blocked**, not
replaced by a hosted run. The operator's local Mac remains excluded. CUDA builds
and inference run as homelab farm jobs, not in CI.

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
| `ci.yml` native Metal | `self-hosted`, `macOS`, `ARM64`, operator-verified custom label | same triggers as Linux; route blocked until provisioned |
| `apple-silicon-validation.yml` | same Apple selector; macOS 15+ assertions retained | manual exact-source dispatch after route review |
| `desktop.yml` preview | same Apple selector; macOS 15+ assertions retained | manual desktop dispatch on `main` or a reviewed `v*` tag |
| `metal-lora-inference.yml` | same Apple selector; device eligibility checked separately | manual exact-source dispatch after route and model-run approval |
| `desktop.yml` publish | `self-hosted`, `Linux`, `X64`, `riff-linux` | tag or manual publish |
| `pages.yml` deploy | `self-hosted`, `Linux`, `X64`, `riff-linux` | Pages workflow |
| `release.yml` validate | reusable `ci.yml`, explicit `linux-source-only: true` | all five Linux functional checks; no Apple qualification claim |
| `release.yml` release | `self-hosted`, `Linux`, `X64`, `riff-linux` | publishes source assets only after Linux qualification succeeds |


The native Linux CPU job retains the existing solver, CPU-tensor LoRA,
score-replay and acoustic checks, plus the `native-lora` receipt/numerical
evidence upload. The Apple Silicon workflow runs the same CPU-tensor LoRA check
plus the Metal attention check; only the attention check exercises Metal.
Routing the standard jobs to self-hosted Linux does not remove Linux CPU coverage.

The workflows verify host packages rather than installing them with `apt` or
Homebrew. Apple jobs require provisioned FFmpeg/ffprobe where their existing
checks use them; toolchain setup actions and pinned build tools remain intact.

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

The existing Apple build commands are retained in this source proposal, but
are not authorized to run until the operator approves the remote Apple route
and its native build custody. The Linux queue is not an Apple toolchain and
cannot qualify Metal or the desktop package. The maintained Apple compile
queue's platform-compatible entry point and custody must be supplied, integrated
and reviewed before enabling the selector. That integration remains unfinished
until the actual host interface is known. No direct-build fallback from a missing
queue is authorized by this migration.

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

Current platform state (October 25, 2026):

- **Apple Silicon**: self-hosted execution is required. Source routing is ready
  for an operator-confirmed remote runner; no actual route has been verified by
  this source audit. The former hosted `macos-14`/`macos-15` declarations are
  removed. Historical hosted passes, including the PR #45/#82 checks, remain
  historical results and are excluded from current self-hosted qualification.
  Run 40199464372 on c074c8a also remains evidence of an unsupported paravirtual
  device, not Metal inference success. Physical Metal inference (#24) remains
  unqualified. Linux CPU fixtures do not qualify it.
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


## Apple route handoff required from the operator

The four Apple jobs select an actual custom label through the repository
variable `RIFF_APPLE_RUNNER_LABEL`. This variable names a selector, not a runner
name; runner names are not automatically custom labels. The built-in required
labels are `self-hosted`, `macOS` and `ARM64`. No custom label is invented or
configured by this change. Keep the variable unset until the following facts
are supplied and reviewed under #31:

1. The registered remote runner's exact name/ID, current online status, actual
   macOS/arm64 host and complete labels; ownership must exclude the operator's
   local Mac. Supply the real custom label to use in `RIFF_APPLE_RUNNER_LABEL`.
2. The runner group, repository access for public `Flip-Engineering/riff`, and
   workflow allowlist entries for `ci.yml`, `apple-silicon-validation.yml`,
   `metal-lora-inference.yml` and `desktop.yml` at their reviewed refs. Retain
   outside-collaborator approval and workflow-diff review before self-hosted PR
   execution. Registering a runner or changing these permissions is root-owned.
3. A runner-owned workspace/temp volume with measured free space, separate job
   directories, and the maintained native-build queue/custody approved for that
   Apple host. Supply its actual command/state paths and supported interface if
   queue integration is needed; do not point Metal builds at the Linux queue.
4. Actual macOS version (15+ for desktop and full Apple validation), Xcode and
   Metal compiler/SDK, Git, C/C++ compiler, Python, CMake, pkg-config and shell
   utilities. Provision FFmpeg/ffprobe for media/browser/package checks. Existing
   actions still select Python 3.14, Node 22, OTP 28.0.2 and Elixir 1.18.4;
   desktop also selects Rust 1.89.0 and checksum-pinned CMake 4.4.3. Verify these
   versions on the real host rather than assuming a hosted-image toolchain.
5. Actual Metal device facts. The no-model probe must report simdgroup reduction
   before inference is eligible; successful compilation, CPU-tensor tests or an
   attention skip are not physical Metal inference qualification.

When the variable is empty, the Apple matrix entry/job is routed to the already
configured `riff-linux` pool **only to report a hard failure before Apple
validation**. The shared preflight explains the missing route in the job summary;
no Apple build, test or package acceptance runs. This prevents a silent skip,
a hosted fallback or a job queued for a fabricated runner. Linux's independent
CPU lane still runs its existing assertions. Once a real custom label is set,
all four jobs require the complete self-hosted Apple label set. The preflight
then verifies self-hosted execution and actual Darwin/arm64 identity, sets
job-local `TMPDIR`/Mix/Hex paths and derives `ImageOS` for BEAM setup from the
actual macOS version. Job receipts record the runner name and requested labels;
the requested labels alone are not an inventory or physical-device proof.

After source review and route provisioning, root must authorize exact-source
runs and retain their runner/toolchain/device receipts and all existing test and
desktop acceptance results. A reviewed source proposal or an unset/missing route
is not completed supported-platform qualification. This change does not dispatch
jobs, provision infrastructure or alter release `v0.6.31`.

## Publishing the workflow migration

Publishing this change requires an authorized GitHub App installation token with
repository permissions `contents: write` and `workflows: write`. Opening its PR
also requires `pull_requests: write`; `metadata: read` accompanies repository
access. GitHub documents [Git access to workflow files](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app#choosing-permissions-for-git-access)
and [installation token permissions](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app).

Verify three separate layers before retrying publication: the App registration
requests Workflows read/write, the repository owner's installation has approved
that permission, and the credential helper actually requests it in the token's
`permissions` object. A helper that explicitly requests only Contents and Pull
requests still issues a token without Workflows even after the App grant changes.
The workflow's `permissions:` block controls its job token; editing that block
does not fix a Git push authorization failure.

Root owns approval of the publication route and any App/helper permission change.
Use the existing model identity and repository-bound helper after root supplies
the reviewed helper revision and approved installation. Preserve the authored
migration commits and perform an ordinary nonforce branch push, then open a
focused draft PR for #31. Do not retry with another identity, token, API write
path or broader permission after a refusal. Source publication can remove hosted
routing before an Apple runner is provisioned: keep `RIFF_APPLE_RUNNER_LABEL`
unset so Apple validation fails explicitly on the existing self-hosted Linux
pool. Enabling the Apple selector still requires the runner and compile-custody
handoff above. A new release tag must reference the reviewed migration before
its reusable validation can satisfy the self-hosted policy; existing published
tags remain immutable.

## Linux-qualified source publication

`release.yml` publishes the portable source archive, installer and checksums;
it does not publish a desktop runtime or a prebuilt CUDA/Metal engine. Its
explicit reusable-workflow input selects all five existing Linux checks:
control, Python 3.9/3.14 application, browser and queued CPU native validation.
All their assertions and native receipts remain required. Standard `ci.yml`
push, pull-request and manual runs keep both CPU and Metal lanes by default;
an unavailable Apple route still fails its diagnostic. Native check names retain
the runner-label string and backend used by the previous matrix.

After root reviews and merges this publication policy, root may create a NEW
`vX.Y.Z` tag matching VERSION on the reviewed source. The tag's release workflow
must pass the Linux checks before packaging, isolated install checks and source
publication. `scripts/publish_source_release.sh` checks the exact tag/checkout,
actual self-hosted Linux x86-64 publisher and asset digests, then creates the
release with explicit source-only qualification notes, source SHA and run link.
It does not replace an existing release or move a tag. Run it through the
maintained workflow, not as a manual substitute for validation.

Apple/Metal, desktop acceptance and real CUDA device/inference qualification
remain separate. A Linux source release does not satisfy them or close #31/#24.
The desktop workflow still requires its platform build/acceptance and a
successful `release.yml` source release before attaching compatible payloads;
source installation never substitutes for a missing desktop payload.

A private candidate archive retains its original source/hash even if its PR is
merged later. A subsequent tag builds and validates its own exact source; do
not relabel the earlier candidate with the merge or publication-policy SHA.
