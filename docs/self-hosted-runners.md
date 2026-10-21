# Self-hosted runners

Issue #31 tracks moving Riff validation and delivery to self-hosted runners. The
standard workflows no longer declare GitHub-hosted Ubuntu or macOS images.

## Workflow routing

| Workflow / job | Required labels | Execution |
| --- | --- | --- |
| `ci.yml` control, application, browser, native Linux CPU | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | push to `main`, same-repository pull requests, `workflow_call` |
| `ci.yml` CUDA compile probe | `self-hosted`, `Linux`, `X64`, `riff-cuda` | manual `workflow_dispatch` only |
| `homelab-linux-validation.yml` | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | manual exact-source dispatch |
| `apple-silicon-validation.yml` | `self-hosted`, `macOS`, `ARM64` | manual exact-source dispatch |
| `desktop.yml` preview | `self-hosted`, `macOS`, `ARM64` | `main`/`v*` tag/manual desktop workflow |
| `desktop.yml` publish | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | tag or manual publish |
| `pages.yml` deploy | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | Pages workflow |
| `release.yml` validate/release | `self-hosted`, `Linux`, `X64`, `baton2-native-linux-x64` | release workflow |

The Linux workflows verify provisioned packages and tools rather than installing
them with `apt` or Homebrew. The CUDA container installs its own build packages
inside the pinned image.

## Source trust

Standard `pull_request` jobs run only when the pull request head belongs to
`Flip-Engineering/riff`. Fork-origin pull requests skip the self-hosted jobs; a
maintainer can use the manual exact-source validation workflow after reviewing
the proposed source. Manual workflows record the runner name, OS, architecture
and source SHA, and the Apple Silicon workflow records the active toolchain after
setup. Keep the organization runner-group workflow allowlist restricted to
reviewed workflow paths.

## Current provisioning gaps

The organization inventory recorded on 2026-10-20 found two online Linux runners
with `baton2-native-linux-x64`. It found no `macOS`/`ARM64` runner and no
`riff-cuda` runner. At that time the `baton2-native-homelab` runner group allowed
only `homelab-linux-validation.yml`; `ci.yml`, `desktop.yml`, `pages.yml`,
`release.yml` and `apple-silicon-validation.yml` still require an organization
owner to add them to the group workflow allowlist. Recheck the inventory and
allowlist before dispatch because both change outside this repository.

- **Linux CPU**: route is defined; the standard workflow cannot use the runner
  group until the allowlist includes `ci.yml` (and `desktop.yml`, `pages.yml`,
  `release.yml` for delivery).
- **Apple Silicon Metal**: **platform gap**. `desktop.yml` preview and
  `apple-silicon-validation.yml` route to `self-hosted, macOS, ARM64`, but no
  such runner is registered. They will wait for a runner; they must not be
  replaced with a hosted macOS run. Desktop packaging and Metal qualification
  are unverified until a remote Apple Silicon runner is provisioned and an
  actual run records its identity and toolchain.
- **CUDA**: `ci.yml` CUDA is manual-dispatch only and requires `riff-cuda`. No
  such runner is registered; it is not part of normal Linux CI and has no hosted
  fallback.

Linux CPU and Apple Silicon Metal results remain distinct. A Linux pass is not
evidence for the macOS desktop package or Metal engine.
