import array
import http.client
import json
import math
import shutil
import struct
import subprocess
import sys
import threading
import unittest
import zlib

from studio import StudioServer
from test_studio import StudioFixture, recipe
from video import MOTION_VERSION, WAVEFORM_POINTS, VideoExports, motion_envelope


def png(width, height, color):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    pixels = (b"\0" + bytes(color) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def h264_frame(width, height, color):
    value = "".join(f"{channel:02x}" for channel in color)
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        f"color=c=0x{value}:s={width}x{height}:r=24:d=0.04",
        "-frames:v", "1", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-f", "h264", "pipe:1",
    ])


class MotionTests(StudioFixture):
    def test_opposite_stereo_channels_retain_energy_and_silence_stays_still(self):
        data = motion_envelope(self.audio)
        self.assertEqual(data["duration"], .1)
        self.assertEqual(data["fps"], 60)
        self.assertEqual(len(data["frames"]), 6)
        self.assertTrue(all(frame[0] > 0 for frame in data["frames"]))
        self.assertTrue(all(frame[5] > 0 for frame in data["frames"]))
        self.assertTrue(any(abs(point) > .01 for frame in data["waveforms"] for point in frame))
        import wave
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((2, 2, 48000, 0, "NONE", ""))
            output.writeframes(bytes(4800 * 4))
        self.assertEqual(motion_envelope(self.audio)["frames"], [[0.0] * 8] * 6)
        self.assertEqual(motion_envelope(self.audio)["waveforms"], [[0.0] * WAVEFORM_POINTS] * 6)

    def test_waveform_retains_signed_signal_and_relative_dynamics(self):
        import wave
        def trace(amplitude):
            samples = [round(amplitude * math.sin(2 * math.pi * 96 * sample / 48000)) for sample in range(4800)]
            with wave.open(str(self.audio), "wb") as output:
                output.setparams((1, 2, 48000, 0, "NONE", ""))
                output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
            return motion_envelope(self.audio)["waveforms"][-1]
        quiet, loud = trace(1000), trace(8000)
        self.assertEqual(len(loud), WAVEFORM_POINTS)
        self.assertLess(min(loud), -.5)
        self.assertGreater(max(loud), .5)
        self.assertGreater(max(loud), max(quiet) * 4)
        self.assertTrue(all(-1 <= value <= 1 for value in loud))

    def test_frequency_and_stereo_changes_have_distinct_motion(self):
        import wave
        def measure(frequency, left=1, right=1):
            data = bytearray()
            for sample in range(24000):
                value = 6000 * math.sin(2 * math.pi * frequency * sample / 48000)
                data.extend(struct.pack("<hh", round(value * left), round(value * right)))
            with wave.open(str(self.audio), "wb") as output:
                output.setparams((2, 2, 48000, 0, "NONE", ""))
                output.writeframes(data)
            return motion_envelope(self.audio)["frames"][-1]
        bass, middle, air = measure(70), measure(700), measure(4500)
        self.assertGreater(bass[1], bass[3])
        self.assertGreater(middle[2], middle[1])
        self.assertGreater(air[3], air[1])
        self.assertGreater(air[3], air[2])
        left, right = measure(700, 1, 0), measure(700, 0, 1)
        self.assertLess(left[4], 0)
        self.assertGreater(right[4], 0)
        self.assertAlmostEqual(left[0], right[0])
        self.assertEqual(middle[5], 0)

    def test_percussive_attack_decays_and_stays_finite(self):
        import wave
        data = bytearray()
        for sample in range(24000):
            value = round(10000 * math.sin(2 * math.pi * 100 * sample / 48000)) if sample < 3000 else 0
            data.extend(struct.pack("<hh", value, value))
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((2, 2, 48000, 0, "NONE", ""))
            output.writeframes(data)
        frames = motion_envelope(self.audio)["frames"]
        self.assertGreater(frames[0][6], .5)
        self.assertGreater(frames[2][6], frames[-1][6])
        self.assertTrue(all(math.isfinite(value) for frame in frames for value in frame))


class VideoMigrationTests(StudioFixture):
    def test_existing_export_history_keeps_full_audio_bounds_after_upgrade(self):
        with self.store.db() as db:
            db.execute("""CREATE TABLE video_exports (
                id TEXT PRIMARY KEY, track_id TEXT NOT NULL, created REAL NOT NULL,
                status TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                fps REAL NOT NULL, duration REAL NOT NULL, frames INTEGER NOT NULL,
                received INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')""")
            db.execute("INSERT INTO video_exports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("a" * 32, "b" * 32, 1, "done", 1280, 990, 24, 31.998666, 768, 768, ""))
        for _ in range(2):
            exports = VideoExports(self.store)
            try:
                row = exports.get("a" * 32)
                self.assertEqual((row["status"], row["source_start"], row["source_end"], row["received"]),
                                 ("done", 0, 31.998666, 768))
                self.assertEqual(row["download_url"], "/api/video-exports/" + "a" * 32 + "/download")
            finally:
                exports.close()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
class VideoTests(StudioFixture):
    def setUp(self):
        super().setUp()
        self.track = self.store.add_track(self.audio, recipe(), {})
        self.server = StudioServer(("127.0.0.1", 0), self.store, None)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        defaults = {"Content-Type": "application/json", "X-Riff-Request": "1"}
        defaults.update(headers or {})
        if body is not None and not isinstance(body, bytes): body = json.dumps(body).encode()
        connection.request(method, path, body, defaults)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, json.loads(payload) if response.getheader("Content-Type", "").startswith("application/json") else payload)
        connection.close()
        return result

    def start(self):
        status, job = self.request("POST", f"/api/tracks/{self.track}/video-exports", {"width": 32, "height": 24, "fps": 24})
        self.assertEqual(status, 201, job)
        return job

    def test_real_mp4_preserves_audio_duration_and_frame_sequence(self):
        job = self.start()
        base = f"/api/video-exports/{job['id']}"
        self.assertTrue(self.server.video_exports.busy())
        self.assertEqual(self.request("POST", base + "/finish", {})[0], 400)
        for index in range(job["frames"]):
            status, result = self.request("POST", base + "/frames", png(32, 24, (index * 80, 40, 90)),
                                          {"Content-Type": "image/png", "X-Riff-Frame": str(index)})
            self.assertEqual(status, 200, result)
        status, result = self.request("POST", base + "/finish", {})
        self.assertEqual((status, result["status"]), (200, "done"))
        self.assertFalse(self.server.video_exports.busy())
        path, title = self.server.video_exports.download(job["id"])
        self.assertEqual(title, "A test take.mp4")
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]))
        streams = {stream["codec_type"]: stream for stream in probe["streams"]}
        self.assertEqual(streams["video"]["codec_name"], "h264")
        self.assertEqual(streams["audio"]["codec_name"], "aac")
        self.assertEqual(int(streams["video"]["nb_frames"]), job["frames"])
        self.assertAlmostEqual(float(streams["audio"]["duration"]), .1, delta=.024)
        status, content = self.request("GET", result["download_url"], headers={"Range": "bytes=0-31"})
        self.assertEqual((status, len(content)), (206, 32))
        self.assertEqual(self.request("POST", base + "/finish", {})[0], 400)

    def test_h264_frame_transport_muxes_without_a_second_video_encode(self):
        status, job = self.request("POST", f"/api/tracks/{self.track}/video-exports",
                                   {"width": 32, "height": 24, "fps": 24, "transport": "h264"})
        self.assertEqual(status, 201, job)
        self.assertEqual(job["transport"], "h264")
        command = self.server.video_exports.active[job["id"]]["process"].args
        self.assertEqual(command[command.index("-r") + 1], "24")
        self.assertLess(command.index("-r"), command.index("-i"))
        base = f"/api/video-exports/{job['id']}"
        for index, color in enumerate(((180, 40, 80), (40, 180, 80), (40, 80, 180))):
            status, result = self.request("POST", base + "/frames", h264_frame(32, 24, color), {
                "Content-Type": "video/h264", "X-Riff-Frame": str(index),
            })
            self.assertEqual(status, 200, result)
        status, result = self.request("POST", base + "/finish", {})
        self.assertEqual((status, result["status"]), (200, "done"))
        path, _ = self.server.video_exports.download(job["id"])
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]))
        streams = {stream["codec_type"]: stream for stream in probe["streams"]}
        self.assertEqual(streams["video"]["codec_name"], "h264")
        self.assertEqual(int(streams["video"]["nb_frames"]), job["frames"])
        self.assertEqual(streams["audio"]["codec_name"], "aac")

    def test_export_receipt_survives_reopening_and_rejects_invalid_client_timings(self):
        job = self.start()
        base = f"/api/video-exports/{job['id']}"
        timings = dict.fromkeys(("prepare_seconds", "draw_seconds", "snapshot_seconds", "encode_seconds",
                                "upload_seconds", "preview_seconds", "yield_seconds", "before_finish_seconds"), .1)
        for invalid in ([], {}, {**timings, "draw_seconds": True}, {**timings, "draw_seconds": -1},
                        {**timings, "draw_seconds": float("inf")}, {**timings, "draw_seconds": float("nan")},
                        {**timings, "extra": 1}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.request("POST", base + "/finish", {"timings": invalid})[0], 400)
                self.assertEqual(self.server.video_exports.get(job["id"])["status"], "rendering")
        frames = [png(32, 24, (index * 80, 40, 90)) for index in range(job["frames"])]
        for index, frame in enumerate(frames):
            self.server.video_exports.frame(job["id"], index, frame)
        status, result = self.request("POST", base + "/finish", {"timings": timings})
        self.assertEqual(status, 200)
        receipt = result["receipt"]
        self.assertEqual(receipt["received_bytes"], sum(map(len, frames)))
        path, _ = self.server.video_exports.download(job["id"])
        self.assertEqual(receipt["output_bytes"], path.stat().st_size)
        self.assertEqual(receipt["client_reported"], timings)
        self.assertGreater(receipt["server"]["wall_seconds"], 0)
        self.assertLessEqual(receipt["server"]["finalize_seconds"], receipt["server"]["wall_seconds"])
        reopened = VideoExports(self.store)
        try:
            self.assertEqual(reopened.get(job["id"])["receipt"], receipt)
        finally:
            reopened.close()

    def test_delivery_profiles_keep_dimensions_and_link_bit_exact_original_audio(self):
        import hashlib
        original = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        for profile, crf, bitrate in (("share", "23", "192k"), ("master", "16", "320k")):
            with self.subTest(profile=profile):
                status, job = self.request("POST", f"/api/tracks/{self.track}/video-exports",
                    {"width": 32, "height": 24, "fps": 24, "end_seconds": .05, "profile": profile})
                self.assertEqual(status, 201, job)
                self.assertEqual(job["profile"], profile)
                command = self.server.video_exports.active[job["id"]]["process"].args
                self.assertEqual(command[command.index("-crf") + 1], crf)
                self.assertEqual(command[command.index("-b:a") + 1], bitrate)
                for index in range(job["frames"]):
                    self.server.video_exports.frame(job["id"], index, png(32, 24, (index * 80, 40, 90)))
                completed = self.server.video_exports.finish(job["id"])
                self.assertEqual(completed["receipt"]["profile"], profile)
                self.assertFalse(completed["receipt"]["audio"]["lossless"])
                self.assertFalse(completed["receipt"]["video"]["lossless"])
                self.assertEqual(completed["original_audio"]["scope"], "full_recording")
                status, data = self.request("GET", completed["original_audio"]["download_url"])
                self.assertEqual(status, 200)
                self.assertEqual(hashlib.sha256(data).hexdigest(), original)
                self.assertEqual((completed["width"], completed["height"], completed["fps"]), (32, 24, 24))
        for invalid in ("small", None, True, {}, []):
            status, _ = self.request("POST", f"/api/tracks/{self.track}/video-exports", {"profile": invalid})
            self.assertEqual(status, 400)
        self.assertFalse(self.server.video_exports.busy())

    def test_h264_mismatched_dimensions_or_frame_count_cannot_finish(self):
        for width, copies in ((16, 1), (32, 2)):
            with self.subTest(width=width, copies=copies):
                exports = self.server.video_exports
                job = exports.start(self.track, {"width": 32, "height": 24, "fps": 24, "transport": "h264"})
                frame = h264_frame(width, 24, (180, 40, 80)) * copies
                for index in range(job["frames"]):
                    exports.frame(job["id"], index, frame)
                with self.assertRaisesRegex(ValueError, "requested dimensions and frame count"):
                    exports.finish(job["id"])
                self.assertEqual(exports.get(job["id"])["status"], "failed")
                self.assertNotIn("download_url", exports.get(job["id"]))
                self.assertFalse((exports.output / (job["id"] + ".part.mp4")).exists())

    def test_frame_origin_order_size_and_cancel_are_enforced(self):
        job = self.start()
        exports = self.server.video_exports
        process = exports.active[job["id"]]["process"]
        base = f"/api/video-exports/{job['id']}"
        headers = {"Content-Type": "image/png", "X-Riff-Frame": "0"}
        self.assertEqual(self.request("POST", base + "/frames", png(32, 24, (0, 0, 0)),
                                     {**headers, "Origin": "https://unrelated.invalid"})[0], 403)
        self.assertEqual(self.request("POST", base + "/frames", png(32, 24, (0, 0, 0)),
                                     {**headers, "X-Riff-Frame": "1"})[0], 400)
        self.assertEqual(self.request("POST", base + "/frames", png(16, 24, (0, 0, 0)), headers)[0], 400)
        self.assertEqual(exports.get(job["id"])["received"], 0)
        self.assertEqual(self.request("POST", base + "/cancel", {})[1]["status"], "cancelled")
        self.assertIsNotNone(process.poll())
        self.assertFalse(exports.busy())
        self.assertFalse((exports.output / (job["id"] + ".part.mp4")).exists())
        self.assertEqual(self.request("POST", base + "/frames", png(32, 24, (0, 0, 0)), headers)[0], 400)

    def test_passage_exports_the_selected_audio_and_keeps_the_recording(self):
        import hashlib
        import wave
        # Distinct seconds reveal a wrong seek even if the exported duration is right.
        samples = [round(7000 * math.sin(2 * math.pi * (220 * 2 ** (i // 48000)) * i / 48000))
                   for i in range(48000 * 4)]
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 48000, 0, "NONE", ""))
            output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        original = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        status, job = self.request("POST", f"/api/tracks/{self.track}/video-exports",
                                   {"width": 32, "height": 24, "fps": 12, "start_seconds": 1.25, "end_seconds": 2.75})
        self.assertEqual(status, 201, job)
        self.assertEqual((job["source_start"], job["source_end"], job["duration"], job["frames"]), (1.25, 2.75, 1.5, 18))
        exports = self.server.video_exports
        for index in range(job["frames"]): exports.frame(job["id"], index, png(32, 24, (80, 40, 90)))
        exports.finish(job["id"])
        path, title = exports.download(job["id"])
        self.assertEqual(title, "A test take — passage.mp4")
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]))
        streams = {stream["codec_type"]: stream for stream in probe["streams"]}
        self.assertAlmostEqual(float(streams["audio"]["duration"]), 1.5, delta=1 / 48000)
        self.assertEqual(int(streams["video"]["nb_frames"]), 18)
        decoded = array.array("f", subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]))
        if sys.byteorder != "little": decoded.byteswap()
        for start, frequency in ((.2, 440), (1.1, 880)):
            part = decoded[round(start * 48000):round((start + .2) * 48000)]
            crossings = sum(a <= 0 < b for a, b in zip(part, part[1:]))
            self.assertAlmostEqual(crossings / .2, frequency, delta=10)
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), original)

    def test_invalid_passages_do_not_start_an_encoder(self):
        for start, end in ((-.01, .05), (.05, .05), (.08, .04), (0, .2), (True, .08), ("0", .08),
                           (float("nan"), .08), (0, float("inf")), (0, 1e308), (0, 1e-9)):
            status, _ = self.request("POST", f"/api/tracks/{self.track}/video-exports",
                                     {"start_seconds": start, "end_seconds": end})
            self.assertEqual(status, 400, (start, end))
            self.assertFalse(self.server.video_exports.busy())

    def test_cached_motion_invalidates_when_the_recording_changes(self):
        first = self.server.video_exports.visualization(self.track)
        self.assertEqual(first, self.server.video_exports.visualization(self.track))
        import os
        os.utime(self.audio, ns=(self.audio.stat().st_atime_ns, self.audio.stat().st_mtime_ns + 1))
        second = self.server.video_exports.visualization(self.track)
        self.assertNotEqual(first["source"], second["source"])
        self.assertEqual(first["frames"], second["frames"])

    def test_old_motion_cache_is_recomputed_for_new_features(self):
        first = self.server.video_exports.visualization(self.track)
        target = self.server.video_exports.cache / (self.track + ".json")
        target.write_text(json.dumps({"source": first["source"], "fps": 24, "frames": [[0] * 4]}))
        updated = self.server.video_exports.visualization(self.track)
        self.assertEqual(updated["version"], MOTION_VERSION)
        self.assertEqual(updated["features"][4], "balance")
        self.assertEqual(len(updated["frames"][0]), 8)
