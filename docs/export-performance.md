# Export performance receipts

Completed exports expose a versioned `receipt` from
`GET /api/video-exports/{id}`. It is stored with the export and remains available
after the app restarts. Historical, unfinished and cancelled exports have no
receipt; missing measurements are not reported as zero.

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
