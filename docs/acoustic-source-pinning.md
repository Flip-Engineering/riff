# Acoustic source pinning: guarded digest reuse

Investigation for [#60](https://github.com/Flip-Engineering/riff/issues/60),
2026-10-24 UTC. Implementation authorized by the root after the investigation.

The baseline recurring cost was a full native SHA-256 pass over roughly 2.94 GB before
every acoustic capture. Warm model residency did not cache these digests.
On this Linux host, hashing consumes almost all of that time even with every
page resident. Cache verified digests within the session, while checking source
identities before every take; never simply skip verification after the first take.
The implementation adds `patches/yue2-weight-digests.patch` to do this.

## Baseline trace

Before this change: runtime `fbe3eedbf6c504e45189e2cdcf1b257740a28863` with the 14 baseline patches in
[sources.json](../sources.json):

- [`yue2-vae-checkpoint.patch`](../patches/yue2-vae-checkpoint.patch) adds
  `Yue2PipelineRuntime::Impl::run_request` → `pin_acoustic_sources`. Whenever
  `acoustic_latents_out` is set, it hashes the selected main GGUF, VAE GGUF,
  tokenizer and running executable before planning, AR or NAR. Main/VAE identity
  guards run before and after hashing; tokenizer guards surround pinning.
  Config digests come from the exact model, VAE and generation JSON bytes parsed
  at asset load, rather than rereading those files per take.
- `checkpoint::hash_file` reads the entire file in 64 KiB blocks, checks the open
  descriptor before/after reading and checks the pathname still names that
  identity. POSIX identity includes device, inode, size, nanosecond mtime and
  ctime. Linux uses the patch's portable SHA-256; macOS normally uses
  CommonCrypto. The timer includes these hashes and small metadata operations,
  with no GGUF copy, weight conversion or graph work.
- [`yue2-warm-engine.patch`](../patches/yue2-warm-engine.patch) retains uploaded
  weights, but calls this same pinning path for every job. Acoustic replay has
  a separate full VAE hash under `yue2.acoustic.verify_decoder_ms`.
- [`studio_core.py`](../studio_core.py)'s Generator calls
  [`acoustic_artifacts.py`](../acoustic_artifacts.py) `launch` after admission
  for synthesis. It records main/VAE/sidecar/executable digests in a launch
  receipt; `capture` compares the native checkpoint against that receipt.
  [`run.py`](../run.py) already caches digests against resolved path plus
  `(device, inode, size, mtime_ns, ctime_ns)`, and checks identity after a miss.
  Those Python cache hits do not avoid the independent native hashes.
  Saved-acoustic replay prepares an independently copied `.yac` through
  [`AcousticArtifact`](../runtime/lib/riff/runtime/acoustic_artifact.ex).
  This copies latents, not GGUF weights, and is outside the pinning timer.

## Measurements

Linux x86_64, AMD EPYC 7551, GCC 13.3 Release CPU engine, two inference threads.
Application source `8e6c28177af27fd377c219fbb38a66179dcfb627`; unchanged native
fingerprint `a97282a0cd11f431-cpu`. Reused the engine previously built through
the shared compile queue. Verified all six shared model files against
`sources.json` without writing them.

The [probe](evidence/acoustic-pin-probe.cpp) links that engine's existing,
unmodified `acoustic_checkpoint.cpp.o`. Its small harness was also compiled
through the queue. Each cell below is one sequential native `hash_file` call;
each result matched the expected SHA-256. Times are milliseconds.

| Source | Bytes | Shared, warm | Private, evicted | Private, immediate repeat |
|---|---:|---:|---:|---:|
| Main GGUF | 2,665,632,320 | 21,270.091 | 23,257.408 | 20,539.089 |
| VAE GGUF | 265,218,656 | 2,044.398 | 2,487.383 | 1,957.431 |
| Tokenizer | 2,561,218 | 23.180 | 21.241 | 19.815 |
| Executable | 6,265,232 | 48.034 | 56.489 | 43.683 |
| Sum | 2,939,677,426 | 23,385.703 | 25,822.522 | 22,560.018 |

Shared files were warmed by verification. For the cold comparison, made ordinary
independent copies, fsynced and hash-verified them, then applied
`POSIX_FADV_DONTNEED` only to those owned copies. `mincore` reported zero resident
pages before each evicted read and all pages resident before each repeat.
Evicted reads incurred 5,741,576 input blocks; warm reads incurred zero. These
are cold Linux page-cache reads, not cold NVMe/controller-cache measurements.
The warm shared sum used 23,315.715 ms CPU; the warm private sum used
22,549.722 ms CPU. Main GGUF accounts for about 91% of the warm hashing time.

Two successful requests in the **same** `--jobs -` process, with
`yue2.keep_resident=true`, seed 1234, 25 semantic frames and two midpoint steps:

| Take | Source pinning, ms | Session wall, ms |
|---|---:|---:|
| First | 21,556.132 | 60,192.687 |
| Second | 22,437.148 | 55,901.671 |

The second take logged both AR and NAR resident reuse. WAVs and acoustic
checkpoints were byte-identical across the two takes; checkpoint main/VAE/engine
hashes matched the verified files. Pinning therefore repeats with warm weights
and a warm page cache. These are approximately one-second takes, not the
six-second studies reported in the issue.

The existing Python cache took 4,586.009 ms on its first pass over all six model
files plus the executable, and 1.693 ms for the identical cached pass. These
measure `run.file_digest`, not the proposed native cache. Small owned-fixture
checks confirmed that same-size edits with restored mtime changed ctime and
missed that cache, and equal-size replacements with restored mtime changed inode
and missed it. Neither check qualifies an unimplemented native cache.

Full numeric results, normalized commands, hashes and method are in the
[evidence receipt](evidence/acoustic-pin-cpu-2026-10-24.json). Raw logs, probe
build, orchestration and private copies remain in the investigation worktree's
ignored `test-results/issue60/`. No hardware-wide cache drop was used. This is
one busy host, load average roughly 47–61 during isolated measurements, one
sample per cache/file cell and two short takes; it is not a throughput acceptance
matrix. Metal and CUDA remain unmeasured.

## Implementation

The fifteenth pinned patch, [`yue2-weight-digests.patch`](../patches/yue2-weight-digests.patch),
adds session-owned `SourceFile` entries for the main GGUF, VAE, tokenizer and
running executable. Each entry binds a lazy full guarded SHA-256 to its original
`(device, inode, size, mtime, ctime)` identity. Unchanged captures reuse those
digests. Seed, solver, guidance, storage and other request metadata are rebuilt
for every capture; the checkpoint format and Python launch comparison stay intact.
There is no persistent cache and no trusted Python-supplied substitute digest.

Before every take, including one without checkpoint capture, the engine checks
selected model/VAE/tokenizer/config identities before planning or retained-weight
use. Both the originally selected GGUF pathname and the loader's canonical source
are guarded, so retargeting a selected symlink also invalidates the session.
Config anchors surround the original parsing too; an absent generation
config remains an anchored absence. Changed sources invalidate the session.
The CLI marks that failure fatal in the warm protocol, requiring a new process
rather than attaching a new pathname's digest to old uploaded weights.
`warm_engine.engine_key` now includes ctime, so a same-size edit with restored
mtime also restarts the engine proactively.

Post-NAR checks still precede checkpoint publication. VAE identity is checked
around decoding; a later decode failure keeps the previously validated NAR
checkpoint. VAE-only replay caches its independent VAE digest and checks only
its selected decoder sources, without requiring main-model assets. Windows now
queries actual `FileBasicInfo.ChangeTime` rather than creation time, failing
closed when that information cannot be obtained. File identities remain local
guards and are never serialized as paths or metadata cache keys.

Native tests exercise hit/miss byte accounting, in-place same-size edits with
restored mtime, equal-size inode replacement, symlink substitution, deletion,
config appearance, source changes between warm takes, invalid-session retirement,
fresh-session recovery and VAE mutation during decoding. Real capture validation
also checks digest counters, output equality and checkpoint integrity.

## Fixed-engine validation

Queued CPU build `48d269d2573480e2-cpu` passed. Both SHA implementations pass
79 format/cache cases; 69 pipeline cases, 44 LoRA cases, the score replay oracle,
solver checks and 26 Python warm/setup cases pass.

Same real model, recipe and two-thread resident-engine protocol as above:

| Take | Pinning, ms | Source bytes hashed | Digest hits | Session wall, ms |
|---|---:|---:|---:|---:|
| First | 25,061.615 | 2,939,695,634 | 0 | 73,520.720 |
| Second | 0.142 | 0 | 4 | 37,617.300 |

Both WAVs match the baseline bytes. Acoustic payload and metadata match baseline
except the new producer binary digest and consequent header checksum; both fixed
checkpoints are byte-identical and have valid header/payload hashes. An additional
production CLI check successfully plans once, then edits an owned VAE sidecar
without changing size or mtime: the next warm request emits `fatal=1` and exits 3
before neural generation. A second production CLI test retargets its selected
VAE symlink to an identical-byte owned file while leaving the old canonical
source unchanged; that next request is also fatal before generation. Shared
model file identities remain unchanged.

The [fixed-engine receipt](evidence/acoustic-pin-fixed-cpu-2026-10-24.json) records
commands, counters, timings, build proof and validation. These are two short CPU
takes on a busy shared host; the pin-stage counters demonstrate avoided hashing,
while total-session differences also include host-load variation. No new full-model
Metal/CUDA performance or Windows build qualification is claimed.

This removes repeated full reads for unchanged warm sessions; the first capture
still pays the hash cost. Fresh processes gain little from a session-only cache.
Accelerated Linux SHA is a separate possible improvement to first-use cost,
requiring dependency/portability and digest-equivalence review.

Metadata reuse assumes normal local filesystem change semantics, not an attacker
able to forge inode/timestamp history or a filesystem that reports stale/coarse
attributes. Pre/post checks also do not make mutable files an atomic snapshot:
a writer can race the interval between checking and reading. The current full
hash path has this interval too. If the requirement includes hostile concurrent
writers, use verified immutable snapshots or filesystem-backed integrity rather
than claiming stat caching provides that guarantee. Ordinary changes present
before a take must be detected before planning; changes during a stage must fail
that stage's output verification. Preserve an already validated NAR checkpoint
when a later VAE stage fails.
