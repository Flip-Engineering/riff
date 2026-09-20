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
| PNG/libx264 fallback | veryfast, CRF 16 |
| Browser H.264 target | 0.055 bits/pixel/frame, 8–32 Mbps |
| MP4 audio | AAC 320 kbps |

The browser uses variable bitrate, quality latency mode, and a requested keyframe
every two seconds. Draining each one-frame batch preserves bounded storage without
waiting for codec lookahead. Realtime mode is inappropriate here because it may
drop frames to meet a bitrate, which stalled a low-bitrate trial after its first
frame. Quality mode must not drop frames for that reason
([WebCodecs latency modes](https://www.w3.org/TR/webcodecs/#latency-mode)). A stalled
worker request is also bounded to 60 seconds and remains cancellable.
Browser encoders choose their own internal preset and frame structure; we do not
claim a particular B-frame count or hardware implementation. PNG uses yuv420p;
H.264 output still passes the existing dimension/count/timing validation.

Receipts record the codec, lossy status, requested audio bitrate, and
either the fallback CRF or browser target bitrate. A requested bitrate is not a
measurement or a server-enforced limit on agent-uploaded H.264. Capabilities
advertise the same encoding targets to browser and agent clients. No new temporary
audio copy or raw-frame directory is created; the existing single-frame
backpressure and failed/cancelled-part cleanup remain in force.

The server records accepted frame bytes, final MP4 bytes, frame count, transport,
elapsed time from job creation, time writing to FFmpeg's stdin, and time finishing
and validating the output. `stdin_seconds` includes encoder backpressure. It is
not a measurement of codec CPU time. FFmpeg processes frames while the browser
works, so server and browser durations overlap and must not be added together.

Raw H.264 is muxed using the export's constant-rate frame clock, not timing
embedded by the browser encoder. FFmpeg's input `-r` replaces that timing;
its demuxer `-framerate` alone does not. See the
[FFmpeg video options](https://ffmpeg.org/ffmpeg.html#Video-Options).

Frame production overlaps the previous frame's upload. There is at most one
upload and one next frame, not an unbounded render queue; the last upload is
acknowledged before final assembly. Cancellation aborts the upload and releases
the encoder before the export is cleared. Encoding and upload durations therefore
overlap and must not be added to estimate wall time.

The browser submits `client_reported` durations in seconds:

| Field | Measurement |
| --- | --- |
| `prepare_seconds` | Motion data, canvas/encoder setup and server job creation |
| `draw_seconds` | Renderer calls for the requested video frames |
| `snapshot_seconds` | Awaiting `createImageBitmap` snapshots for a worker |
| `encode_seconds` | Awaiting encoded worker output, or main-thread PNG output |
| `upload_seconds` | Frame uploads and acknowledgements, including retry waits |
| `preview_seconds` | Progress preview drawing and UI updates |
| `yield_seconds` | Yielding browser tasks between frames |
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

Run from the repository with development dependencies, Chromium, FFmpeg and
ffprobe installed:

```sh
RIFF_DEMO_WAV=/absolute/reference.wav RIFF_BENCH_DURATIONS=2,30 \
  node scripts/benchmark_video.mjs /absolute/fresh-results-directory
```

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
sampled process-tree RSS/CPU and relative video SSIM. The MP4 and frame-hash files
remain beside `results.json`; no raw frames accumulate on disk.

RSS is a sampled sum with shared pages counted in each process, not physical
footprint. CPU percentages come from `ps`, not an integrated CPU-time counter.
GPU utilization and peak temporary-disk use require separate profiling and are
not inferred from these numbers. SSIM compares against the existing lossy
PNG/libx264 output, not a lossless master. These limitations and host load belong
with any reported results; one run does not establish a universal speedup.

Each run hashes the source and every original-WAV download, in addition to decoded
video frame hashes. Compare software changes at the same encoding settings;
lower quality is not accepted as a throughput optimization. Retain the MP4s,
frame hashes and machine/load metadata with the results.

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
