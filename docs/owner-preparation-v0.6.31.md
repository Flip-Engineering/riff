# Reviewed owner preparation for immutable v0.6.31

This recipe prepares the existing official CUDA release without selecting an
engine, downloading models, changing installed metadata or stopping music. It is
separate from the generic queue integration in #81. The coordinator reviews and
executes the owner step; implementation/test workers must not run it against the
real queue or owner installation.

The pinned helper is [prepare_release_0_6_31.py](../scripts/prepare_release_0_6_31.py),
SHA-256 `7bdca6d9ae6542bf9c4c5406bcaa3b855877a45b26df97eb00835dcce68f4a59`.
It uses explicit checks and rejects `-O`, `-OO` and `PYTHONOPTIMIZE`. Before staging
or submitting a build it checks owner/archive/queue/tool access and an actual OTP
`:json` encode/decode round trip. The build and imported Python validation use the
same cleaned environment; stale `RIFF_`, `CMAKE_`, `SCCACHE_` and `ACTIONS_` values
are removed before the preparation-specific values are set. Compiler variables
such as `CC`/`CXX` remain visible and are bound by native build receipts.

## Preconditions and privileged copy

Choose the existing install-owner account and an owned persistent staging parent
on the host build volume. The host must provide that account with queue
executable/state access, Python 3.9+, Git, CMake, C++, CUDA `nvcc`, Elixir/OTP with
`:json`, `nice` and `ionice` on its PATH. Confirm the owner toolchain environment
before execution; the worker checkout and worker's private toolchain directories
need not be traversable by the owner. The helper fails on missing prerequisites
and never falls back to an unqueued compile.

Root must first review the exact helper and provision a **new** persistent owned
NVMe parent. These are root-side commands for review, not commands run by the
implementation worker. They fail if the destination already exists; preserve an
existing stage and choose a separately reviewed new parent instead of removing it.
The root step copies the SHA-pinned helper into the owner's directory, so owner
execution does not depend on access to the worker checkout.

```sh
set -eu
: "${RIFF_PREP_SOURCE:?absolute path to the reviewed helper}"
: "${RIFF_PREP_PARENT:?new persistent staging directory on the build volume}"
: "${RIFF_INSTALL_OWNER:?existing install-owner account}"
prep_source=$RIFF_PREP_SOURCE
prep_parent=$RIFF_PREP_PARENT
prep_owner=$RIFF_INSTALL_OWNER
prep_group=$(id -gn "$prep_owner")
printf '%s  %s\n' 7bdca6d9ae6542bf9c4c5406bcaa3b855877a45b26df97eb00835dcce68f4a59 "$prep_source" | sha256sum --check -
mkdir -m 0700 "$prep_parent"
chown "$prep_owner:$prep_group" "$prep_parent"
install -m 0400 -o "$prep_owner" -g "$prep_group" "$prep_source" "$prep_parent/prepare_release_0_6_31.py"
curl --fail --location --proto '=https' --proto-redir '=https' --output "$prep_parent/riff-v0.6.31.tar.gz" https://github.com/Flip-Engineering/riff/releases/download/v0.6.31/riff-v0.6.31.tar.gz
printf '%s  %s\n' 9b4b9e2dfa25495d22230030e4850928956b77109e7a2cc462db8dd80ac5a0f3 "$prep_parent/riff-v0.6.31.tar.gz" | sha256sum --check -
chown "$prep_owner:$prep_group" "$prep_parent/riff-v0.6.31.tar.gz"
chmod 0400 "$prep_parent/riff-v0.6.31.tar.gz"
```

If the coordinator already has the official digest-verified archive, it can copy
those exact bytes instead of downloading them again, retaining the checksum and
owner/mode checks. The helper also verifies the expected 2,260,513-byte size and
checksum before extraction.

## Exact install-owner command

After the copy and toolchain preconditions pass, run the following **as
the specified install owner**, not as root. This recipe intentionally does not embed a sudo command.

```sh
set -eu
: "${RIFF_PREP_PARENT:?owned persistent staging directory}"
: "${RIFF_INSTALL_OWNER:?existing install-owner account}"
cd "$RIFF_PREP_PARENT"
printf '%s\n' '7bdca6d9ae6542bf9c4c5406bcaa3b855877a45b26df97eb00835dcce68f4a59  prepare_release_0_6_31.py' | sha256sum --check -
nice -n 19 ionice -c 3 python3 prepare_release_0_6_31.py --expected-owner "$RIFF_INSTALL_OWNER" --archive "$RIFF_PREP_PARENT/riff-v0.6.31.tar.gz" --stage-parent "$RIFF_PREP_PARENT"
```

The helper creates a unique retained `riff-v0.6.31-cuda-*` stage beneath that
parent. It extracts the authenticated archive into `source/`, sets `RIFF_HOME` to
that isolated source directory, and submits this exact compile command there:

```sh
nice -n 19 ionice -c 3 /bin/sh scripts/run_native_build_queued.sh cuda --compile-only --jobs 2
```

Its queue executable is `/var/tmp/baton2-compile-queue/compile-queue-runner`, state
root is `/var/tmp/baton2-compile-queue`, source ID is the exact source commit below,
and queue artifact destination is `<stage>/queue-artifact`. `TMPDIR` is the new
`<stage>/tmp`. It never points the destructive wrapper at live workspace engines.
`--compile-only` skips device probing, models and engine selection;
`--build-only` would still select the engine and must not replace it here.

The helper retains `request.json`, `queue.log`, all native/cache outputs and
`prepared.json`. Validation checks the successful CUDA receipt, specialized
fingerprint, actual CMake backend/architecture, crypto receipt and library ABI,
and recomputed source/toolchain/File API identity against the retained input and
build receipts. `prepared.json` records the binary SHA-256 and actual input,
namespace and system-SHA identities. Keep the **entire stage**, including failed
or interrupted stages, because compiled binaries and native work can reference
its persistent absolute paths. There is no automatic cleanup or direct retry.

## Immutable identity and remaining activation review

| Identity | Required value |
| --- | --- |
| Release | v0.6.31, release ID 423718516 |
| Annotated tag object | `0456789fd326bdebca4fa8ef0dd866ee10e33a48` |
| Source commit | `0230da64dd549f4925478903ce8bafc74d3d8c59` |
| Source tree | `03a07bee425beb583d574e820e0f719b72d438b1` |
| Archive SHA-256 | `9b4b9e2dfa25495d22230030e4850928956b77109e7a2cc462db8dd80ac5a0f3` |
| Embedded install.py SHA-256 | `f2d6232f0890a3079edcf3f04df79bb207bdfd587d4e017535b83eb2b31acf88` |
| Pinned audio.cpp | `fbe3eedbf6c504e45189e2cdcf1b257740a28863` plus exact sources.json patches |

The selected system-SHA identity specializes the runtime fingerprint; measure it
from the prepared receipt. Neither the old engine directory name nor the base
fingerprint alone proves the runtime identity. This helper and recipe do not
change the published release/tag/assets, and require no release version bump.

**Pending metadata import is not approved by this preparation recipe.** The
installed source runtime has no public prepared-engine import CLI. A separate
review must cover staging the same official archive through `install.stage_release`,
matching current model pins, writing only backend/binary into the target
`.engine.json`, and atomically publishing pending state without racing an existing
maintenance task or pending update. This helper performs none of those writes.

Activation must use Studio restart/`Maintenance.apply_pending`, which rechecks
busy state and reserves `generator.quiesce`. An idle snapshot is not a reservation;
do not call `install.activate` directly or cancel work to deploy. After separately
reviewed activation, verify live v0.6.31, the previous backend/device/thread/warm-engine settings,
existing model settings, library/recordings and continued musician work. That live
execution and verification remain with the coordinator. The generic queue repair
also requires updated running code and inherited queue policy; setting an
environment variable cannot retrofit the immutable old runtime.
