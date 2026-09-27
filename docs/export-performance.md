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
uploads stay in frame order. An encoder may hold frames until more arrive, so the
export drains it whenever its upload queue is full (one second of H.264 frames;
one PNG uploads while the next encodes) and at the end. A drain is bounded to 60
seconds and remains cancellable. Every output passes the dimension, frame-count
and frame-rate validation before it can be downloaded.

Receipts record the final encoder, CRF, preset, tune, colour, frame source
(`png` or `browser_h264`), lossy status and audio bitrate. Capabilities advertise
the same settings, including the quantizer and fallback bitrate targets, to
browser and agent clients. An agent may send H.264 at any quality; the final
encode is the same. No new temporary
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

The browser submits `client_reported` durations in seconds:

| Field | Measurement |
| --- | --- |
| `prepare_seconds` | Motion data, canvas/encoder setup and server job creation |
| `draw_seconds` | Renderer calls for the requested video frames |
| `snapshot_seconds` | Creating H.264 `VideoFrame`s from the canvas, or awaiting PNG `createImageBitmap` snapshots |
| `encode_seconds` | Waiting for the browser H.264 encoder to accept a frame, or for PNG output |
| `upload_seconds` | Frame uploads and acknowledgements, including retry waits; overlaps drawing |
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
