"""Shared audio motion data and bounded PNG-to-MP4 exports."""
import array
import json
import math
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import wave

MOTION_VERSION = 3
WAVEFORM_POINTS = 64  # Half the contour's 128 vertices; detail remains legible at cover size.


def lowpass_coefficients(frequency, sample_rate):
    k = math.tan(math.pi * frequency / sample_rate)
    scale = 1 / (1 + math.sqrt(2) * k + k * k)
    b0 = k * k * scale
    return b0, 2 * b0, b0, 2 * (k * k - 1) * scale, (1 - math.sqrt(2) * k + k * k) * scale


def motion_envelope(path, fps=24):
    """Stream energy, stereo image and transients; retain no decoded audio."""
    result, waveforms = [], []
    with wave.open(str(path), "rb") as source:
        rate, channels, width, total = source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getnframes()
        if width not in (1, 2, 3, 4) or not rate or not channels:
            raise ValueError("This recording's PCM format cannot be visualized.")
        stride = max(1, round(rate / 12000))
        sample_rate = rate / stride
        bass_filter = lowpass_coefficients(min(180, sample_rate * .08), sample_rate)
        mid_filter = lowpass_coefficients(min(1800, sample_rate * .3), sample_rate)
        # Band-limit before reducing each visual frame to its contour samples.
        trace_filter = lowpass_coefficients(min(WAVEFORM_POINTS * fps * .4, sample_rate * .3), sample_rate)
        low, middle = [[0.0, 0.0] for _ in range(channels)], [[0.0, 0.0] for _ in range(channels)]
        trace_states = [[0.0, 0.0] for _ in range(channels)]
        position, previous_level, transient = 0, 0.0, 0.0
        release = math.exp(-1 / (fps * .18))
        for frame in range(math.ceil(total / rate * fps)):
            end = min(total, round((frame + 1) * rate / fps))
            raw = source.readframes(end - position)
            position = end
            if width in (2, 4):
                samples = array.array("h" if width == 2 else "i", raw)
                if sys.byteorder != "little": samples.byteswap()
            elif width == 1:
                samples = [value - 128 for value in raw]
            else:
                samples = [int.from_bytes(raw[i:i + 3], "little", signed=True) for i in range(0, len(raw), 3)]
            sums, count = [0.0] * 4, 0
            channel_energy, cross, peak = [0.0] * channels, 0.0, 0.0
            traces = [[] for _ in range(channels)]
            normalization = 2 ** (width * 8 - 1)
            for i in range(0, len(samples), channels * stride):
                if channels >= 2:
                    cross += (samples[i] / normalization) * (samples[i + 1] / normalization)
                for channel in range(channels):
                    sample = samples[i + channel] / normalization
                    channel_energy[channel] += sample * sample
                    peak = max(peak, abs(sample))
                    values = []
                    for coefficients, states in ((bass_filter, low), (mid_filter, middle), (trace_filter, trace_states)):
                        b0, b1, b2, a1, a2 = coefficients
                        value = b0 * sample + states[channel][0]
                        states[channel][0] = b1 * sample - a1 * value + states[channel][1]
                        states[channel][1] = b2 * sample - a2 * value
                        values.append(value)
                    traces[channel].append(values[2])
                    for band, value in enumerate((sample, values[0], values[1] - values[0], sample - values[1])):
                        sums[band] += value * value
                    count += 1
            # Fixed transfer curve preserves the relative dynamics of the recording.
            energy = [min(1, math.sqrt(value / max(1, count)) ** .6 * 1.8) for value in sums]
            rms = math.sqrt(sums[0] / max(1, count))
            balance, spread = 0.0, 0.0
            if channels >= 2 and sum(channel_energy[:2]) > 0:
                left, right = (math.sqrt(value) for value in channel_energy[:2])
                balance = (right - left) / (right + left)
                spread = math.sqrt(max(0, min(1, (left * left + right * right - 2 * cross)
                                                  / (2 * (left * left + right * right)))))
            transient = max(min(1, max(0, energy[0] - previous_level) * 3.8), transient * release)
            previous_level = energy[0]
            crest = min(1, max(0, (peak / rms - 1) / 4)) if rms else 0.0
            result.append([round(value, 5) for value in
                           [*energy, balance * energy[0], spread * energy[0], transient, crest * energy[3]]])
            # Use the more audible channel so opposite-phase stereo cannot erase
            # the trace. Keep absolute dynamics instead of normalizing silence.
            trace = traces[max(range(channels), key=lambda channel: channel_energy[channel])]
            waveform = []
            for point in range(WAVEFORM_POINTS):
                at = point * max(0, len(trace) - 1) / (WAVEFORM_POINTS - 1)
                before, blend = int(at), at % 1
                value = (trace[before] * (1 - blend) + trace[min(before + 1, len(trace) - 1)] * blend) if trace else 0
                waveform.append(round(math.tanh(value * 4), 4))
            waveforms.append(waveform)
    return {"version": MOTION_VERSION, "fps": fps, "duration": total / rate,
            "features": ["level", "bass", "middle", "air", "balance", "spread", "attack", "crest"],
            "frames": result, "waveforms": waveforms}


class VideoExports:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.active = {}
        self.cache = store.data_root / "visualizations"
        self.cache.mkdir(exist_ok=True)
        self.output = store.outputs / "video"
        self.output.mkdir(exist_ok=True)
        with store.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS video_exports (
                id TEXT PRIMARY KEY, track_id TEXT NOT NULL, created REAL NOT NULL,
                status TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                fps REAL NOT NULL, duration REAL NOT NULL, frames INTEGER NOT NULL,
                received INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(video_exports)")}
            if "source_start" not in columns:
                db.execute("ALTER TABLE video_exports ADD COLUMN source_start REAL NOT NULL DEFAULT 0")
            if "source_end" not in columns:
                db.execute("ALTER TABLE video_exports ADD COLUMN source_end REAL")
            db.execute("UPDATE video_exports SET source_end=source_start+duration WHERE source_end IS NULL")
            db.execute("UPDATE video_exports SET status='interrupted' WHERE status IN ('rendering','encoding')")
        self.stop = threading.Event()
        self.cleaner = threading.Thread(target=self.cleanup, daemon=True)
        self.cleaner.start()

    def visualization(self, track_id):
        path = self.store.audio_path(track_id)
        stamp = [path.stat().st_size, path.stat().st_mtime_ns]
        target = self.cache / (track_id + ".json")
        with self.lock:
            if target.exists():
                value = json.loads(target.read_text())
                if value.get("source") == stamp and value.get("version") == MOTION_VERSION: return value
            value = {**motion_envelope(path), "source": stamp,
                     "seed": self.store.track(track_id)["recipe"].get("seed", "")}
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, separators=(",", ":")))
            temporary.replace(target)
            return value

    def get(self, export_id):
        with self.store.db() as db:
            row = db.execute("SELECT * FROM video_exports WHERE id=?", (export_id,)).fetchone()
        if row is None: raise KeyError("Video export not found.")
        result = dict(row)
        if result["status"] == "done": result["download_url"] = f"/api/video-exports/{export_id}/download"
        return result

    def busy(self):
        with self.lock: return bool(self.active)

    def start(self, track_id, options):
        if not shutil.which("ffmpeg"):
            raise ValueError("MP4 export needs FFmpeg. Install FFmpeg, then export again.")
        width, height, fps = options.get("width", 1280), options.get("height", 990), options.get("fps", 24)
        if any(type(v) is not int or v < 2 or v % 2 for v in (width, height)):
            raise ValueError("Video width and height must be positive even pixel counts.")
        if type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0:
            raise ValueError("Choose a positive video frame rate.")
        audio = self.store.audio_path(track_id)
        with wave.open(str(audio), "rb") as source:
            rate, total = source.getframerate(), source.getnframes()
        start, end = options.get("start_seconds", 0), options.get("end_seconds", total / rate)
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end)):
            raise ValueError("Choose valid start and end times for the passage.")
        if not 0 <= start < end <= (total + .5) / rate:
            raise ValueError("The passage must start before it ends and stay within this recording.")
        first, last = round(start * rate), round(end * rate)
        if not 0 <= first < last <= total:
            raise ValueError("The passage must start before it ends and stay within this recording.")
        start, end, duration = first / rate, last / rate, (last - first) / rate
        export_id = uuid.uuid4().hex
        log = (self.cache / (export_id + ".log")).open("w+")
        try:
            process = subprocess.Popen([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "image2pipe", "-framerate", str(fps), "-i", "pipe:0",
                "-ss", str(start), "-t", str(duration), "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast",
                "-threads", "2", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(self.output / (export_id + ".part.mp4")),
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        except Exception:
            log.close()
            raise
        with self.lock, self.store.db() as db:
            self.active[export_id] = {"process": process, "log": log, "updated": time.monotonic(), "lock": threading.Lock()}
            db.execute("INSERT INTO video_exports(id,track_id,created,status,width,height,fps,duration,frames,source_start,source_end) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (export_id, track_id, time.time(), "rendering", width, height, fps, duration,
                        math.ceil(duration * fps), start, end))
        return self.get(export_id)

    def frame(self, export_id, index, data):
        job = self.get(export_id)
        with self.lock: active = self.active.get(export_id)
        if not active or job["status"] != "rendering": raise ValueError("This export is no longer accepting frames.")
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("Send a PNG visualization frame.")
        if struct.unpack(">II", data[16:24]) != (job["width"], job["height"]):
            raise ValueError("The frame size differs from this export.")
        try:
            with active["lock"]:
                current = self.get(export_id)
                if index != current["received"] or index >= current["frames"]:
                    raise ValueError("The video frame sequence is incomplete or out of order.")
                if current["status"] != "rendering": raise ValueError("This video export has stopped.")
                active["process"].stdin.write(data)
                active["process"].stdin.flush()
                active["updated"] = time.monotonic()
                with self.store.db() as db:
                    db.execute("UPDATE video_exports SET received=received+1 WHERE id=?", (export_id,))
        except (BrokenPipeError, OSError):
            self.cancel(export_id, "failed", "Video encoding stopped. Check that FFmpeg includes H.264 encoding.")
            raise ValueError("Video encoding stopped. Check that FFmpeg includes H.264 encoding.") from None
        return self.get(export_id)

    def finish(self, export_id):
        with self.lock: active = self.active.get(export_id)
        if not active: raise ValueError("This video export has stopped.")
        with active["lock"]:
            job = self.get(export_id)
            if job["status"] != "rendering": raise ValueError("This video is already finishing.")
            if job["received"] != job["frames"]: raise ValueError("Finish drawing the video frames before downloading.")
            with self.store.db() as db: db.execute("UPDATE video_exports SET status='encoding' WHERE id=?", (export_id,))
            try: active["process"].stdin.close()
            except (BrokenPipeError, OSError): pass
        result = active["process"].wait()
        with self.lock:
            if export_id not in self.active: return self.get(export_id)
            if result:
                self.cancel(export_id, "failed", "Video encoding could not finish.")
                raise ValueError("Video encoding could not finish.")
            (self.output / (export_id + ".part.mp4")).replace(self.output / (export_id + ".mp4"))
            self.active.pop(export_id)
            active["log"].close()
            with self.store.db() as db: db.execute("UPDATE video_exports SET status='done' WHERE id=?", (export_id,))
        return self.get(export_id)

    def cancel(self, export_id, status="cancelled", error=""):
        self.get(export_id)
        with self.lock:
            active = self.active.pop(export_id, None)
            if active:
                active["process"].terminate()
                try: active["process"].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    active["process"].kill(); active["process"].wait()
                try:
                    if not active["process"].stdin.closed: active["process"].stdin.close()
                except (BrokenPipeError, OSError): pass
                active["log"].close()
                (self.output / (export_id + ".part.mp4")).unlink(missing_ok=True)
                with self.store.db() as db:
                    db.execute("UPDATE video_exports SET status=?,error=? WHERE id=?", (status, error, export_id))
        return self.get(export_id)

    def download(self, export_id):
        job = self.get(export_id)
        if job["status"] != "done": raise ValueError("The video is not ready to download.")
        track = self.store.track(job["track_id"])
        passage = job["source_start"] > 0 or job["source_end"] < track["audio"]["duration"]
        suffix = " — passage.mp4" if passage else ".mp4"
        return self.output / (export_id + ".mp4"), track["title"] + suffix

    def cleanup(self):
        while not self.stop.wait(15):
            with self.lock:
                expired = [key for key, value in self.active.items()
                           if self.get(key)["status"] == "rendering" and time.monotonic() - value["updated"] > 120]
            for key in expired: self.cancel(key, "interrupted", "The browser stopped sending video frames.")

    def close(self):
        self.stop.set()
        with self.lock: active = list(self.active)
        for key in active: self.cancel(key, "interrupted")
        self.cleaner.join()
