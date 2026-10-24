# Export performance receipts

Completed exports expose a versioned `receipt` from
`GET /api/video-exports/{id}`. It is stored with the export and remains available
after the app restarts. Historical, unfinished and cancelled exports have no
receipt; missing measurements are not reported as zero.

## Fixed export quality

Share/Master choices were rejected and removed. Export uses one fixed high-quality
configuration without reducing the requested dimensions, frame rate or passage.
It delivers H.264 video in fast-start MP4 with lossy AAC audio, not a lossless
video archive. Completed results expose `original_audio`
with a download of the untouched full-recording WAV, even for passage exports.
The dialog labels the AAC fallback and provides the WAV beside the MP4.

| Setting | Value |
| --- | --- |
| Final video encode, both paths | libx264 slow, tune animation, CRF 19 |
| Colour | BT.709 from full-resolution chroma, limited range, tagged |
| Browser H.264 intermediate | fixed quantizer 14; else VBR 0.11 bits/pixel/frame, 8–64 Mbps |
| MP4 audio | AAC 320 kbps |

Every export ends in the same libx264 encode on the server. Frames arrive either
as lossless PNG (small exports and browsers without WebCodecs H.264) or as browser
H.264, which is an intermediate the server decodes and re-encodes, never copies.
The browser takes each H.264 frame straight from the export canvas as a
`VideoFrame` (no bitmap copy or worker round trip) and encodes it at a fixed
quantizer where supported, variable bitrate in quality latency mode otherwise, with
a requested keyframe every two seconds. Quality mode must not drop frames to meet
a bitrate ([WebCodecs latency modes](https://www.w3.org/TR/webcodecs/#latency-mode)).
Frames are not flushed one by one: drawing, encoding and upload overlap while
uploads stay in frame order. The browser groups consecutive frames into batches
of at most 16 frames and 32 MiB, rather than waiting for an HTTP acknowledgement
for every frame. Both PNG and H.264 use the same bounded batch protocol. A frame
larger than the batch byte limit keeps the existing single-frame path. Servers
without the advertised batching capability retain compatibility with the older
upload protocol. After a lost response the browser checks the received frame
index and resumes only the unaccepted suffix, including a partly accepted batch.
An encoder may hold frames until more arrive, so the
export drains it whenever its upload queue is full (one second of H.264 frames;
up to one batch of PNG frames, also limited by the batch byte budget) and at the
end. The PNG path snapshots the next frame before returning from `submit` and
keeps at most two bitmaps, so the worker encodes the previous frame while the
next is drawn and uploaded. Progress and cancellation yields are bounded to a
human-scale cadence, and preview copies are throttled independently of the
encoded frames. A drain is bounded to 60
seconds and remains cancellable. Every output passes the dimension, frame-count
and frame-rate validation before it can be downloaded.

Receipts record the final encoder, CRF, preset, tune, colour, frame source
(`png` or `browser_h264`), lossy status and audio bitrate. Capabilities advertise
the same settings, including the quantizer and fallback bitrate targets, to
browser and agent clients. An agent may send H.264 at any quality; the final
encode is the same. No new temporary
audio copy or raw-frame directory is created; frame queues and batches remain
bounded, and failed/cancelled-part cleanup remains in force.

Each batched frame is preceded by its unsigned 32-bit big-endian byte length.
The server validates the entire request's framing, image headers and sequence
before admitting its frames. Frames then enter the same FFmpeg pipe in order,
under the export's existing backpressure and cancellation rules. Batch framing
bytes are excluded from the accepted frame-byte total.

The server records accepted frame bytes, final MP4 bytes, frame count, transport,
elapsed time from job creation, time writing to FFmpeg's stdin, and time finishing
and validating the output. `stdin_seconds` includes encoder backpressure. It is
not a measurement of codec CPU time. FFmpeg processes frames while the browser
works, so server and browser durations overlap and must not be added together.

Raw H.264 is muxed using the export's constant-rate frame clock, not timing
embedded by the browser encoder. FFmpeg's input `-r` replaces that timing;
its demuxer `-framerate` alone does not. See the
[FFmpeg video options](https://ffmpeg.org/ffmpeg.html#Video-Options).

The browser submits `client_reported` durations in seconds:

| Field | Measurement |
| --- | --- |
| `prepare_seconds` | Motion data, canvas/encoder setup and server job creation |
| `draw_seconds` | Renderer calls for the requested video frames |
| `snapshot_seconds` | Creating H.264 `VideoFrame`s from the canvas, or awaiting PNG `createImageBitmap` snapshots |
| `encode_seconds` | Waiting for the browser H.264 encoder to accept a frame, or for PNG output |
| `upload_seconds` | Frame uploads and acknowledgements, including retry waits; overlaps drawing |
| `preview_seconds` | Progress preview drawing and UI updates |
| `yield_seconds` | Yielding browser tasks between acknowledged uploads |
| `before_finish_seconds` | Total browser time before requesting final assembly |

These are client-reported elapsed durations, not trusted billing or CPU/GPU
measurements. Worker messages and scheduling are included in encoding time.
Snapshot and draw timings do not separately measure deferred GPU execution.
The main-thread PNG fallback combines readback and encoding in `encode_seconds`.
Tiny stages may round to zero with browser clock precision.

An agent can optionally supply the same complete set in the finish request's
`timings` object. Values must be finite nonnegative numbers. Omitting it preserves
the existing API and records server measurements with `client_reported: null`.

## Matched benchmark

### Renderer availability and recovery

The artwork renderer falls back to Canvas when WebGL cannot be initialized or
its context is lost. Initial allocation failures retry at most once every five
seconds so a temporary failure does not permanently select CPU rendering for
the page. Context restoration rebuilds shader resources on the original canvas
rather than allocating replacement contexts. Neither recovery requires a page
reload or browser restart.

When profiling frame capture, `VideoFrame` construction can include deferred
Canvas rasterization. A large snapshot duration does not by itself establish
that copying pixels or the video codec is the bottleneck. Compare graphics
availability in the affected page and a fresh page before attributing a failure
to Riff. Recovery cannot enable WebGL while the browser refuses contexts in
both pages; do not describe retry handling as a measured export speedup.

Run from the repository with development dependencies, Chromium, FFmpeg and
ffprobe installed:

```sh
RIFF_DEMO_WAV=/absolute/reference.wav RIFF_BENCH_DURATIONS=2,30 \
  node scripts/benchmark_video.mjs /absolute/fresh-results-directory
```

The default driver attaches CDP only to the page. `RIFF_BENCH_DRIVER=playwright`
selects the diagnostic driver explicitly. Interception, Blob observation,
single/batch comparisons, repeat counts and caller-supplied wait timeouts select
Playwright automatically; forcing CDP with those controls is rejected so a
requested diagnostic is never silently ignored. `RIFF_BENCH_INTERCEPT=1` forces
Playwright interception. Driver selection is the same through the revision wrapper.

For a matched single-frame versus batched comparison, run serially into different
fresh directories with `RIFF_BENCH_UPLOAD=single` and `RIFF_BENCH_UPLOAD=batch`.
Alternatively, `RIFF_BENCH_UPLOAD=compare RIFF_BENCH_REPEATS=2` runs each pair
adjacently in alternating order within one fixture and asserts that their decoded
frame hashes match. Prefer this mode when load varies between separate runs.
`RIFF_BENCH_DIMENSIONS=2160x2160x60` restricts the tested formats;
`RIFF_BENCH_LATENCY_MS=30` adds a disclosed 30 ms delay per frame-upload request.
This is simulated request latency, not a measurement of any deployed network.
Results include the actual frame-upload request count. Keep duration, dimensions,
frame rate, transport, source audio and final encoding settings identical when
comparing modes, and compare their retained decoded frame hashes as well as time.
`RIFF_BENCH_SAMPLE_RESOURCES=0` explicitly disables resource sampling; missing
RSS and CPU measurements are recorded as null. Resource-sampled runs bound each
process-table query to two seconds so a blocked sampler fails the run instead of
silently measuring a stalled benchmark as export latency.

The script creates an isolated test library with fake provider credentials. It
does not connect to the running studio or generate music. The selected source
WAV must be long enough for every requested passage. Without a supplied WAV, the
fixture is twelve seconds long; default benchmark passages are two and ten
seconds. The result directory must be new and its parent must exist.

Each resolution/rate pair (1920×1080/30, 2160×2160/30, 2160×2160/60) exercises
both PNG and H.264 transport serially with motion analysis warmed before timing.
H.264 must actually be available; fallback
is not scored as accelerated encoding. Results include stage receipts, wall
time, throughput, final size, decoded-frame hashes, stream duration/count checks,
sampled process-tree RSS/CPU. The Playwright driver additionally reports relative
video SSIM between transports. The MP4 and frame-hash files
remain beside `results.json`; no raw frames accumulate on disk.

RSS is a sampled sum with shared pages counted in each process, not physical
footprint. CPU percentages come from `ps`, not an integrated CPU-time counter.
GPU utilization samples are system-wide and temporary-disk peaks are scoped to
the isolated run, as described below. SSIM compares against the existing lossy
PNG/libx264 output, not a lossless master. These limitations and host load belong
with any reported results; one run does not establish a universal speedup.

Each run hashes the source and every original-WAV download, in addition to decoded
video frame hashes. Compare software changes at the same encoding settings;
lower quality is not accepted as a throughput optimization. Retain the MP4s,
frame hashes and machine/load metadata with the results.

### September 27 measurement — current settings

Quality is measured against the lossless frames themselves: the same 4-second
passage (240 frames, 3840×2160 at 60 fps) of a real 4-minute Riff song captured
as the PNG frames the browser draws. Scores decode each MP4 by its own colour
tags; VMAF uses the default model. Encoder comparisons on those frames (M4):

| Final encode | Mbit/s | RGB PSNR | VMAF |
| --- | --- | --- | --- |
| Former PNG path: veryfast CRF 16, untagged | 11.7 | 46.8 dB | 92.2 |
| slow, tune animation, CRF 19, BT.709 | 7.6 | 47.8 dB | 94.3 |
| Browser QP 14 intermediate alone | 44.0 | 52.8 dB | 97.4 |
| Browser QP 14, then the final encode | 8.6 | 47.1 dB | 94.4 |
| Browser hardware H.264 alone at QP 30 | 7.5 | 43.0 dB | 91.9 |

The former untagged output also showed visible blocking in the soft shadow. A
browser JPEG transport was rejected: at quality 0.98 it cost the final video
2.5 dB and 1.1 VMAF. Hardware H.264 alone needs far more bits than libx264 for
the same quality, so it remains an intermediate.

End to end, Chrome 153 on the M4 exported to an isolated fixture studio on the
Linux host (64 threads) through Tailscale, with no other export running:

| Version | Frames stage | Whole export | Size | RGB PSNR | VMAF |
| --- | --- | --- | --- | --- | --- |
| 0.6.19 (per-frame flush, copied browser H.264) | 20.4 s | 31.7 s | 15.7 Mbit/s | 46.6 dB | 93.6 |
| This change | 12.6 s | 22.6 s | 9.0 Mbit/s | 47.0 dB | 94.4 |

The whole export includes about 10 s of first-time motion analysis, cached per
recording. libx264 slow encoded 4K60 at 30.4 frames per second on that host, so
longer exports are bounded by the host encoder rather than the browser, which
drew and encoded 4K60 at 48–50 frames per second in isolation. On a single
machine the browser and libx264 share its cores. One passage and one host; load
and content change these figures.

### October 21 measurement — pipelined PNG fallback

The PNG fallback, used by browsers without WebCodecs H.264, previously waited
for a complete PNG encode before taking the next frame's snapshot: one pending
worker request also serialized `createImageBitmap` and the encode. The export now
snapshots the next frame before returning from `submit` and lets the worker
encode the previous frame while the next one is drawn. Two bitmaps may be in
flight; bytes are still admitted through the same bounded queue. Progress and
cancellation task yields are capped to about 10 Hz, and preview copies are
throttled independently of the encoded frames. Cancellation still aborts the
export controller immediately and closes late snapshots.

Matched runs used the isolated fixture server, the same 72-second recording and
already-warmed motion data, Chrome 1208 headless on the 64-thread Linux host,
software WebGL/SwiftShader and FFmpeg 6.1.1. PNG transport was forced so both
versions used the same path; the runs alternated baseline and patched code. Host
load average was roughly 25–40 from other jobs, so absolute times are not
portable.

| Picture / passage | Frames | Version | Wall s | Before finish s | Snapshot s | Encode s | MP4 bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1920×1080/60, 2 s | 120 | baseline | 19.0 | 17.7 | 2.44 | 6.67 | 429,285 |
| 1920×1080/60, 2 s | 120 | pipelined | 12.1 | 10.9 | 0.05 | 10.6 | 429,285 |
| 3840×2160/60, 1 s | 60 | baseline | 34.1 | 29.1 | 4.64 | 10.42 | 625,019 |
| 3840×2160/60, 1 s | 60 | pipelined | 24.5 | 19.0 | 0.16 | 17.95 | 625,019 |

The pipelined rows report more encode time because that stage now measures the
worker encode while drawing and uploading continue; it overlaps the other heavy
stages and is not additive. Wall time improved by about 1.4–1.6× in these runs.
The pair outputs are byte-identical: SHA-256
`de203b01ba274046ab948f11f5e43deb4747e96de320557ba33fb7ea37f6c1f8` for
1080p and `de43f5033945374e76c3e423212ac39eea712281e753964516fd85d5d1af54b1`
for 4K. `ffprobe` reports the exact 120/60 frames, requested dimensions and
frame rate, with audio and video durations equal. The H.264 path already
pipelined its frames; alternating 1080p60/4 s runs were output-identical and
showed no measured regression beyond host-load noise. Longer passages, an idle
host and hardware-GPU browsers remain to be measured.

### Historical September 19 profile trial — rejected approach

These figures document the removed experiment, not current options or accepted
optimization evidence. The profile comparator and lower-quality path were removed.

Two-second passages from a real 45-second recording on the M4, using bundled
FFmpeg and Chromium 145/Metal. Sizes are decimal MB. The Master run overlapped
another native generation and severe memory pressure; wall times are reported
as observed, not a controlled speed comparison. Source WAV SHA-256:
`2ae45d1bc937052731772770a39eafa28f39a424da806032d1cb77a58cc8fa37`.
Every original-WAV download matched it; all decoded frame counts and stream
durations passed. Frame hashes and MP4s are retained in the private acceptance
artifacts, not committed recordings.

| Picture / transport | Master MB / seconds | Share MB / seconds | Relative SSIM |
| --- | --- | --- | --- |
| 1920×1080/30 PNG | 0.995 / 3.546 | 0.404 / 2.456 | 0.995863 |
| 1920×1080/30 H.264 | 0.853 / 8.297 | 0.603 / 1.489 | 0.996707 |
| 2160×2160/30 PNG | 1.803 / 24.478 | 0.765 / 5.942 | 0.996661 |
| 2160×2160/30 H.264 | 1.219 / 9.332 | 1.174 / 1.697 | 0.997627 |
| 2160×2160/60 PNG | 2.058 / 17.260 | 0.790 / 9.537 | 0.995613 |
| 2160×2160/60 H.264 | 2.139 / 3.819 | 1.916 / 3.077 | 0.997917 |

Share was smaller in these cases, but the reduction depends on content and
encoder: the 2160/30 browser case shrank only 3.7%. These short opening passages
do not establish quality across complete pieces. The earlier realtime Share
trial stalled after one frame; it is retained as failed evidence and excluded
from the table. Quality-mode draining completed every case. Independent mobile
players, longer passages and controlled throughput/resource trials remain open.

### Long PNG exports and browser backing storage

The PNG worker can keep browser Blob backing data alive until garbage collection,
including after `image.arrayBuffer()` completes and its ArrayBuffer is transferred.
Clearing the JavaScript variable does not itself collect the Blob. In Chromium
145 incognito, a focused 1024-square noise-frame probe left 200 PNG Blobs and
about 776 MB live after 200 encodes. `FileReaderSync` retained the same number
and was rejected as a fix. A long 2160-square/60 export also accumulated live
PNG Blobs and failed; this is separate from codec throughput.

Workers now retire after their cumulative PNG bytes reach the server's advertised
batch-byte budget. Standalone encoders derive that budget from one raw RGBA frame.
The next captured bitmap continues in a fresh worker, in order. Worker retirement
releases the retained Blobs without depending on browser GC cadence. It does not
limit the number of frames or duration, change PNG pixels, or change the final
encoder. Active worker backing data may exceed the budget by one encoded frame.
The draw/upload loop also removes upload-failure subscriptions as each wait ends;
a shared pending race promise previously retained successful frame results until
the export ended.

Read failures preserve the browser exception name, message and one-based frame
number. The UI suggests checking memory, temporary disk space and permissions;
it does not infer a specific resource failure from `NotReadableError` alone.
`tests/video-storage.mjs` forces Chromium's file transport in an isolated profile
and denies writes only to that profile's Blob directory. This produces a real
browser read error, checks server-job cleanup, and restores permissions before a
fresh export. It requires an unprivileged POSIX user. It is a storage-fault test,
not a recreation of memory pressure on an M4.

Chromium documents [Blob backing storage, GC and quotas](https://chromium.googlesource.com/chromium/src.git/+/refs/heads/main/storage/browser/blob/README.md).
Its [test transport switches](https://chromium.googlesource.com/chromium/src.git/+/refs/heads/main/storage/browser/blob/blob_storage_constants.h)
are browser test controls, not Studio configuration.

### Repeating a revision and collecting resources

The current harness can measure any locally available source commit without
changing the active checkout. Install the current harness's Node dependencies,
then run:

```sh
RIFF_DEMO_WAV=/absolute/reference.wav RIFF_BENCH_DURATIONS=2,30 \
  node scripts/benchmark_video_revision.mjs COMMIT /absolute/new-results-directory
```

The wrapper checks out the exact commit in a detached temporary worktree and
runs the current harness against its server and web assets. It records both the
source and harness revisions (and dirty state), then removes the temporary
checkout. Refusal to remove a modified checkout preserves that work. Receipts
and media remain in `measurements/`. For a software comparison, run revisions
serially in alternating order on the same WAV, dimensions, durations, transport,
upload mode and browser; compare retained decoded hashes. Shared-host observations
must be labelled contended, even with matched inputs. A controlled-load matrix
still requires a reserved quiet interval.

The harness now uses an isolated persistent Chromium profile, like ordinary
Studio browsing, instead of Playwright's default incognito context. All browser,
fixture and export temporary files live below the result directory. It reports
available disk space before running and samples temporary allocated/logical bytes,
owned process RSS/CPU and host load every 500 ms. Linux also counts owned open
unlinked files under that temporary directory, deduplicated by device/inode;
other platforms report that component as unavailable. Inaccessible Linux
descriptors are listed and mark that component unavailable for that sample;
known allocated bytes are then a lower bound. These are sampled peaks,
include fixture WAVs and retained MP4s in the temporary library, and exclude files
outside the directory and in-memory `memfd` allocations.

NVIDIA counters, where available, are **system-wide**, include unrelated work,
and are retained with GPU identity and browser graphics information. Software
WebGL utilization is not inferred from them. Missing GPU samples are unavailable,
not zero. On Apple hardware a separate platform GPU profiler is still required.
The browser samples DOM progress and ordinary timer gaps while exporting without
pointer input; encoder tests also cancel with keyboard input during a pending
encode and during worker replacement.

Normal batched runs observe network request/response events without intercepting
uploads. `RIFF_BENCH_INTERCEPT=1` restores interception of capabilities and every
upload for diagnostics; positive injected latency and single/batch comparisons
also enable the required routes. Results disclose both routing settings.
[Playwright routing](https://playwright.dev/docs/api/class-page#page-route) pauses
matched requests until continued and disables HTTP cache; it is a measurement
variable, not ordinary Studio behavior. Riff already serves these worker assets
with `no-store`, so cache behavior alone does not establish the cause of a stall.
Resource samples also retain cumulative CPU seconds per process and, on Linux,
renderer and dedicated-worker thread CPU time. They exclude the transient `ps`
query itself; older results retained that query in their aggregate percentage.

Additional controls:

- `RIFF_BENCH_TRANSPORTS=png` selects the long PNG reproduction alone.
- `RIFF_BENCH_CONTEXT=incognito` exercises the no-disk-spill Blob context.
- `RIFF_BENCH_BLOB_INTERNALS=1` retains live `chrome://blob-internals` records
  every five seconds. `frames` also samples before each worker frame and after
  each retirement, waiting for the pre-frame sample before posting that bitmap.
  These profiling modes affect timings; use them for retention evidence, not
  throughput comparisons. In particular, `frames` blocks frame submission while
  sampling and can contribute to encoder or backend inactivity deadlines under
  shared load. Prefer ungated `1` for full export validation. JSONL records
  preserve native Blob listings and timestamp sampling errors.
- Worker telemetry records each initialization time, byte budget, encoded-byte
  total and retirement, including when Blob profiling is disabled.
- `RIFF_BENCH_BROWSER_ARGS='["--blob-transport-by-file-trigger=1"]'` forces
  Chromium's test file transport; disclose it with results.
- `RIFF_BENCH_TIMEOUT_MS` optionally supplies a caller-selected wait timeout.
  Export/download waits have no default duration cap. Repeat counts, injected
  latency and streamed frame-hash output have no artificial upper limits.

A supplied WAV must cover the longest passage. The built-in fixture is twelve
seconds; use the existing two/ten-second defaults for a short matrix, or supply
longer audio to exceed the recorded failure around frame 1521. Failures retain
the last server job state, UI error, resource samples and Blob samples in
`failure.json`, rather than being discarded from the evidence. The harness also
retains worker request/post/response clocks, page and harness time origins, frame
upload/acknowledgement events and any available FFmpeg stderr before fixture
cleanup. An unavailable measurement stays unavailable; old captures cannot be
retrofitted with timestamps they did not record.

### October 23 validation — partial issue #20 qualification

The [retained evidence](export-performance-20261023.json) includes media hashes, per-worker startup arrays, receipts, quota samples, resource summaries and all retained negative trials. These are reliability and harness controls on a shared host, not a controlled representative-recording throughput claim. The source was a 32-second stereo 48 kHz PCM sine fixture (SHA-256 `9e14482a72195e59cf099535075dce1f14aa57e7ceeea66b4195029e9817d0ee`). Chromium 145.0.7632.6 used ANGLE/SwiftShader on Linux/AMD EPYC 7551. Full MP4s, decoded frame hashes, time series and logs remain under the evidence artifact root recorded in the JSON; they are not committed recordings.

The focused quota probe failed on baseline frame 554 after 553 completed frames, with 2,144,680,545 live PNG Blob bytes. The final observation was labelled the end of a 600-frame sample window; 600 was not the failure frame. Retirement completed 650 noise frames, with zero live PNG Blobs at each 50-frame boundary. The full 2160-square export observed a roughly 35 MB PNG plateau through repeated retirement instead of unbounded growth. This establishes a Blob lifetime mechanism; it does not prove the original M4 failure had exactly the same resource cause.

All six two-second PNG/H.264 matrix pairs were byte-identical to baseline `d30b139`, including MP4 hashes and decoded-frame hashes; original WAV downloads also matched. The fix rows used `2bccf31`, whose encoder and draw-loop behavior matches the final runtime apart from opt-in phase instrumentation. These initial measurements used Playwright with interception and worker auto-attachment. The startup share is disclosed as a harness-affected observation, not representative Studio overhead.

| Format / transport | Baseline wall s | Fix wall s | Retirements | Replacement init / wall |
| --- | --- | --- | --- | --- |
| 1920x1080x30 png | 22.91 | 24.21 | 1 | 0.80% |
| 1920x1080x30 h264 | 34.37 | 42.96 | 0 | 0.00% |
| 2160x2160x30 png | 43.35 | 48.94 | 2 | 5.81% |
| 2160x2160x30 h264 | 59.28 | 42.71 | 0 | 0.00% |
| 2160x2160x60 png | 85.67 | 70.11 | 5 | 12.13% |
| 2160x2160x60 h264 | 86.12 | 87.29 | 0 | 0.00% |

The four-arm PNG control used 1,800 frames at 1920×1080/60, persistent profiles, the same WAV and non-blocking Blob observation. Every arm completed and produced identical MP4 and decoded hashes. Interception increased harness RSS from roughly 0.27 GB to 1.8–2.0 GB and replacement startup substantially; it is now off by default. Playwright worker auto-attachment remained enabled in all four arms. These sequential contended runs isolate a harness variable but do not establish a software speedup.

| Source | Interception | Wall s | Retirements | Median replacement init ms | Peak harness RSS GB |
| --- | --- | --- | --- | --- | --- |
| 286e2cc | off | 191.79 | 40 | 108.9 | 0.27 |
| 286e2cc | on | 268.55 | 40 | 1112.2 | 1.82 |
| d30b139 | off | 201.87 | 0 | — | 0.28 |
| d30b139 | on | 492.96 | 0 | — | 1.99 |

The first three arms used a sampler version that mislabelled Chromium process roles when Linux flattened their command lines. Their per-PID CPU data remains available, but renderer/worker attribution is unavailable. The final arm and subsequent long no-interception runs use corrected role parsing. Sampled process CPU counters include setup and harness cost; worker thread totals are lower bounds because a short-lived thread may end between samples.

Successful 1,800-frame 2160×2160/60 PNG runs follow. All output hashes match (MP4 SHA-256 `22e052d4882c1c928c455fdc55471a3d7b9ac065de24c154fa347475bb5c61e9`), with exact frame count, dimensions, frame rate and audio/video duration validated. Progress completed without pointer input and every worker was terminated. Each run retired 81 workers.

| Driver / context | Interception | Blob observer | Wall s | Replacement init median / p95 / max ms | Replacement init / wall |
| --- | --- | --- | --- | --- | --- |
| Playwright incognito | on | ungated | 1166.97 | 2287.6 / 26911.6 / 49289.5 | 34.96% |
| Playwright persistent | on | none | 993.15 | 2592.4 / 16631.1 / 32813.7 | 35.57% |
| Playwright persistent | off | ungated | 449.14 | 250.3 / 422.4 / 832.5 | 5.05% |
| Page-only CDP | off | none | 927.71 | 443.3 / 929.6 / 1728.3 | 4.28% |

The page-only CDP export completed all 1,800 frames with 81 retirements. Replacement startup totaled 39.684 s (4.28% of wall time); its p95 was 929.6 ms and maximum 1728.3 ms. The maximum remains far below the existing 60 s wait. We keep one worker at a time and defer prewarming: this measured overhead does not justify another live canvas and concurrent initialization in this reliability fix. The measured fraction includes script loading and scheduling and is not a direct subtraction from overlapped export time. This is one shared-host observation, not a bound for all browsers.

Startup phase summaries and every per-worker initialization are retained in the JSON. Script loading, worker scheduling and delivery dominate the measured initialization; canvas construction and getContext are recorded separately on the worker clock. Worker requests are observed without routing or pauses; no worker debugger attached, and CDP reported zero frame postData characters. Chromium's resource-timing entries can have negative durations in these captures; those entries are retained and counted as invalid rather than used to claim an HTTP duration. CSP, no-store and both runtime deadlines remain unchanged.

| Startup observation | Median ms | p95 ms | Max ms |
| --- | --- | --- | --- |
| construct_ms | 0.3 | 3.7 | 7.9 |
| script_received_after_open_ms | 441.4 | 926.2 | 1712.5 |
| script_to_init_ms | 2.4 | 12.4 | 17.2 |
| canvas_create_ms | 0.1 | 0.7 | 3.0 |
| get_context_ms | 0.5 | 2.9 | 9.0 |
| context_ready_to_reply_ms | 0.1 | 0.3 | 3.4 |

The page-only run peaked at 6,268,694,528 summed RSS bytes and 52,875,264 scoped allocated temporary bytes. The harness peaked at 450,764,800 RSS bytes. The maximum ordinary 100 ms timer gap was 3349.2 ms. Host one-minute load rose from 52.58 to 129.68 on 64 logical CPUs. Progress required no pointer movement, but this contended capture does not establish a 100 ms response bound under load.

The no-interception Playwright run peaked at 6,401,888,256 summed RSS bytes and 54,685,696 scoped allocated temporary bytes; live PNG Blob bytes peaked at 34,910,224. The larger RSS in intercepted runs includes the harness retaining request data and must not be read as Studio footprint. RSS sums count shared pages repeatedly. Temporary samples include the fixture WAV and output MP4 and owned open unlinked files, exclude in-memory memfd allocations, and are lower bounds when descriptor access is unavailable. NVIDIA counters are system-wide and include unrelated work; they do not measure this software-rendered export's GPU cost.

Three earlier trials stopped at 1,216, 1,408 and 1,712 accepted frames. They were interrupted by backend 120 s inactivity cleanup during an observer stall under shared load; causation unresolved. They occurred under Playwright interception and worker auto-attach; harness inflation demonstrated; sole causation not proven. The first two gated each frame on Blob inspection; the third did not. The persistent gated trial peaked at 8,657,354,752 summed RSS bytes and 50,769,920 scoped allocated temporary bytes; available disk stayed above 547,368,624,128 bytes. Its last native sample was at elapsed 1461.205 s and its final resource sample at 1533.906 s; the 30 s observer timeout lacked a start timestamp. Old FFmpeg stderr/stdin totals and exact timeout overlap were not retained. The original baseline's 1,216 count was only a live poll, not a terminal count. The separate diagnostic baseline was aborted by a sampler EACCES error; later samplers preserve that component as unavailable.

Two harness-validation negatives are retained separately: a caller-set 1 ms wait timeout exercised failure capture, and a completed 12-frame single-batch export failed an incorrect intermediate-progress assertion. The latter now requires final progress 1; consecutive exports pass after correction. Neither is evidence of a product export failure.

Regression coverage includes exact pixels and ordering through forced retirement, cancellation during replacement with all queued bitmaps closed, a real Chromium backing-file permissions error and fresh retry, and both actual PNG/H.264 upload paths through temporary retry, fatal upload failure, fresh export and keyboard cancellation. The upload-wait cleanup has its own commit. The Python video suite and resource sampler checks pass. Short harness validation additionally checks consecutive page reloads, zero captured frame bodies and unattached worker targets.

Remaining issue #20 qualification: a reserved quiet interval with representative recordings and repeated alternating baselines, hardware-GPU measurements, and M4 memory-pressure behavior. Neither the benchmark fixture nor the successful homelab runs close those items.

### Page-only CDP validation

`scripts/benchmark_video_cdp.mjs` is the default through both entry points;
`RIFF_BENCH_DRIVER=cdp` selects it explicitly. It requires Node's built-in
WebSocket API. It launches an
isolated Chromium itself, attaches only to the page, and never enables worker
auto-attachment or request interception. Worker target samples must remain
unattached. Native browser fetches carry the frames; the driver retains only
small upload metadata and media validation artifacts. Use
`RIFF_BROWSER_EXECUTABLE` to match the browser binary between driver comparisons.
The executable path and full launch arguments are recorded.
The page-only launcher and Playwright have different browser launch defaults;
their timing comparison does not isolate worker auto-attachment by itself.

```sh
RIFF_BENCH_DRIVER=cdp RIFF_DEMO_WAV=/absolute/reference.wav \
  RIFF_BENCH_DURATIONS=30 RIFF_BENCH_DIMENSIONS=2160x2160x60 \
  node scripts/benchmark_video_revision.mjs COMMIT /absolute/new-results-directory
```

The optional worker profile name enables script-evaluated, canvas-construction,
context-creation and PNG encoding/read phases in the traces. Normal Studio
workers emit no profiling packets. The main handler ignores progress packets;
they do not complete a frame or reset the existing 60-second encoder deadline,
which also covers worker initialization. Neither that deadline, the backend
inactivity deadline, the asset's `no-store` policy, nor the CSP changed.
Profiled timings include the optional packet and observer overhead. Prefer this
driver for ordinary export timing; retain the Playwright driver for explicit
request interception, injected latency and native Blob inspection controls.
