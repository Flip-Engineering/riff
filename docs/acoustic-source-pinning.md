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

## Optional system SHA-256 implementation

The coordinator selected platform crypto discovered at build time, with portable
SHA-256 when it is unavailable. The sixteenth pinned patch,
[`yue2-weight-system-sha.patch`](../patches/yue2-weight-system-sha.patch), uses:

- macOS: the existing CommonCrypto path.
- Linux: `find_package(OpenSSL QUIET COMPONENTS Crypto)` and EVP when headers and
  dynamic libcrypto are found. Missing Crypto or a static-only library selects
  portable SHA; nothing is downloaded, bundled or statically linked.
- Other platforms: portable SHA. Riff's engine workflows support Linux/macOS;
  [native Windows is unsupported](install.md), so this change does not add CNG.

`python setup_engine.py --backend cpu --portable-sha` explicitly disables system
SHA discovery; direct CMake builds can set `RIFF_CHECKPOINT_SYSTEM_SHA=OFF`.
The normal setup command explicitly re-enables discovery on subsequent builds.
No new package is required: installing a platform's OpenSSL development package
is optional. A dynamically linked engine uses that target system's libcrypto;
this change supplies no library payload or portable binary distribution promise.

`implementation.yue2.sha256` in native CLI discovery records identifies
`openssl-evp`, `commoncrypto` or `portable`. The CMake cache also records
`RIFF_CHECKPOINT_SHA_BACKEND`; the queued build receipt records `sha256_backend`.
The **engine fingerprint and build directory include the discovered choice and
libcrypto/header digests**, and
configured compiler-cache input identity includes it too. The homelab queue's
artifact key uses the same pinned CMake module to discover the choice and binds
libcrypto/header hashes when selected. This keeps a portable artifact from being
restored for a crypto-enabled build, or vice versa. Managed engines also retain
an adjacent private crypto receipt. Before capability cache lookup or a launch,
Riff verifies its recorded library digest against current file identity. A
changed or missing library makes the engine unavailable with a setup/rebuild
message; it does not launch the stale engine and expose a linker error. Library
hashes are cached against device/inode/size/mtime/ctime, so ordinary unchanged
polls do not reread libcrypto. Setup rediscovers and fingerprints the new library,
or builds portable when unavailable; it does not rebuild during a music request.

EVP initialization, update or finalization errors fail hashing; they cannot yield
an accepted partial digest. Existing file-read, identity and session retirement
guards remain in place. The source check runs the same 80 format/cache cases with
the selected backend and forced portable SHA, comparing complete digest and
checkpoint bytes and cross-checking file digests with OTP. Fixtures include empty
files, 63/64/65-byte boundaries, read-block boundaries and a 1 MiB + 1-byte file.
A fresh CMake helper build with Crypto discovery disabled verifies portable
selection and identical output; macOS CI forces portable alongside CommonCrypto.
These are targeted component checks, not a second native engine build.

Validation in progress: the fresh portable first-take baseline pins in
18,803.990 ms with the prior queue-built CPU engine. Its WAV matches the earlier
baseline and shared model identities remain unchanged. The new CPU engine is
waiting in the shared compile queue; the after timing, backend equivalence checks
and 69 pipeline checks are not yet qualified for this patch.

## First-use SHA-256 acceleration options

Part of [#60](https://github.com/Flip-Engineering/riff/issues/60), after the
session cache merged in [#64](https://github.com/Flip-Engineering/riff/pull/64).
The options below preserve the design comparison that informed the coordinator
decision above; system discovery and fallback refine the original recommendation.

On the same EPYC host, `openssl dgst -sha256` (OpenSSL 3.0.13), reading the
2,665,632,320-byte main GGUF through stdin with a warm page cache, took
3.064 s wall / 2.339 s user. `sha256sum` took 3.256 s wall / 2.187 s user.
Both produced the pinned `97af67d7…` digest, incurred zero input blocks and left
the shared file's identity unchanged. The normalized
[reference receipt](evidence/acoustic-pin-accelerated-sha-2026-10-24.json)
preserves the results; the original portable native probe took 21.270 s.
These were separate single samples on a busy host, not an integrated comparison
or proof of which instructions OpenSSL selected.

Matching the OpenSSL reference would save about **18.21 s per first main-file
hash**, an 85.6% reduction (6.94 times faster). Applying only that saving to the
baseline 23.386 s total source-hashing stage gives about 5.18 s. If all four
hashes improved by the same factor, that stage would be about 3.37 s. These are
projections, not measured first-take or end-to-end generation times; cold storage
can reduce the benefit. The cached second take already hashes zero source bytes.

### Link OpenSSL's libcrypto

Use its streaming [EVP digest API](https://docs.openssl.org/3.0/man3/EVP_DigestInit/)
for SHA-256, retaining the current file reads and identity guards. Only libcrypto
is needed, not libssl or an `openssl` subprocess. OpenSSL handles CPU dispatch;
its [x86 capability documentation](https://docs.openssl.org/3.0/man3/OPENSSL_ia32cap/)
includes SHA extensions. The measured CLI makes this the best-supported route
to the saving above, though the native integration still needs measurement.

Linux x86_64/arm64 and Windows need a new native build dependency and matching
target libraries; assembly support and cross-compilation must be checked.
macOS can retain CommonCrypto. CMake discovery/linking, native cache fingerprints,
CI images and release manifests must account for the chosen library. Dynamic
linking needs a packaged library and reliable loader paths on clean installs;
static linking increases the binary and makes library updates require a rebuild.
Python's or Erlang's own crypto dependency does not satisfy the native CLI's
packaging contract. An explicit portable build can remain available.

OpenSSL 3 is [Apache-2.0 licensed](https://github.com/openssl/openssl/blob/openssl-3.0/LICENSE.txt).
Pin a maintained release and preserve its license and applicable notices in the
distributed components; track its security updates. For digest equivalence, run
the common plan below with EVP, the portable path and CommonCrypto. Also exercise
OpenSSL's supported capability masking, digest initialization/update/finalization
errors, and clean installations with the intended library/provider configuration.

### Reuse ggml or audio.cpp code

Inspected runtime `fbe3eedbf6c504e45189e2cdcf1b257740a28863` and its vendored ggml:
no accelerated SHA-256 implementation was found in ggml's `src` or `include`.
audio.cpp's
[`sanotts/runtime.cpp`](https://github.com/0xShug0/audio.cpp/blob/fbe3eedbf6c504e45189e2cdcf1b257740a28863/src/community_models/sanotts/runtime.cpp)
contains scalar SHA-256 for a truncated text seed. It is not an incremental
full-digest file API. The current checkpoint fallback already uses that standard
compression algorithm; factoring it into a shared utility offers **no demonstrated
first-take acceleration** and cannot justify the 18.21 s saving.

This route adds no external library or runtime packaging requirement. It would
need an upstream API/refactor, streaming state and full-digest exposure, with
cross-platform builds and patch maintenance. audio.cpp is
[Apache-2.0](https://github.com/0xShug0/audio.cpp/blob/fbe3eedbf6c504e45189e2cdcf1b257740a28863/LICENSE);
its ggml is
[MIT](https://github.com/0xShug0/audio.cpp/blob/fbe3eedbf6c504e45189e2cdcf1b257740a28863/external/ggml/LICENSE).
Retain the corresponding notices if moving code. Equivalence testing must cover
both the complete 32-byte digest and unchanged SanoTTS text-seed outputs, in
addition to the common plan. A future upstream accelerated API could be revisited;
it is not present in this pinned revision.

### Vendor SHA-NI and ARMv8 SHA2 compression paths

Keep the portable streaming/padding code and add isolated accelerated block
functions, selected by runtime CPU detection. For example, the
[SHA-Intrinsics x86](https://github.com/noloader/SHA-Intrinsics/blob/master/sha256-x86.c)
and [ARM](https://github.com/noloader/SHA-Intrinsics/blob/master/sha256-arm.c)
sources provide compression functions, not complete guarded file hashing.
Their headers declare public-domain dedication and identify earlier contributors;
pin and review the exact source/provenance and preserve attribution before adoption.

There is no new shared-library payload. The cost moves into Riff: separate
compiler-specific translation units/flags, CPU detection for every required x86
feature and platform-specific ARM capability queries, plus GCC/Clang/MSVC and
cross-compilation coverage. Never enable SHA instructions for the whole binary
or infer runtime support from a compiler macro; unsupported CPUs must reach the
portable fallback safely. macOS can keep CommonCrypto. CPU acceleration could
approach the reference saving, but this candidate has not been benchmarked.
Equivalence tests must explicitly select each supported accelerated path and the
fallback, test unaligned input, and run on CPUs without the extensions to detect
illegal instructions. Riff would own ongoing correctness and portability fixes.

### Common equivalence and integration checks

For any candidate, compare all 32 digest bytes with independent known SHA-256
vectors and the existing portable implementation: empty input, binary data,
padding/block boundaries, varied incremental chunk boundaries, unaligned buffers,
and a streamed input beyond 4 GiB to catch length truncation. Compare the actual
pinned main/VAE/tokenizer hashes too. Preserve the existing source-mutation,
inode replacement, alias retargeting and warm-session retirement checks; an
accelerated hash must not bypass a failed identity guard. Verify checkpoint
header/payload hashes and equivalent capture/replay output, accounting for the
changed producer executable digest. Then measure first capture and cached repeat
on each supported platform, reporting source bytes, cache hits, CPU/wall time and
page-cache conditions. This note has not run those future implementation tests.

### Recommendation

Choose **OpenSSL libcrypto through EVP** for a separate implementation proposal,
keeping CommonCrypto on macOS and an explicit portable build. It has measured
headroom and maintained CPU dispatch, with less custom cryptographic code to
maintain than vendored intrinsics. Accept it only with an explicit native
dependency, license/notices and clean-install packaging plan; do not rely on a
developer machine's library. Internal reuse has no current speed evidence, and
vendoring avoids a library at the cost of Riff owning dispatch and architecture
maintenance. The coordinator accepted system-library discovery with portable fallback, rather
than a required or bundled dependency, as implemented above.
