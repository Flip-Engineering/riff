import http.client
import json
import shutil
import struct
import subprocess
import threading
import unittest
import zlib

from studio import StudioServer
from test_studio import StudioFixture, recipe
from video import motion_envelope


def png(width, height, color):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    pixels = (b"\0" + bytes(color) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class MotionTests(StudioFixture):
    def test_opposite_stereo_channels_retain_energy_and_silence_stays_still(self):
        data = motion_envelope(self.audio)
        self.assertEqual(data["duration"], .1)
        self.assertEqual(len(data["frames"]), 3)
        self.assertTrue(all(frame[0] > 0 for frame in data["frames"]))
        import wave
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((2, 2, 48000, 0, "NONE", ""))
            output.writeframes(bytes(4800 * 4))
        self.assertEqual(motion_envelope(self.audio)["frames"], [[0.0] * 4] * 3)


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

    def test_cached_motion_invalidates_when_the_recording_changes(self):
        first = self.server.video_exports.visualization(self.track)
        self.assertEqual(first, self.server.video_exports.visualization(self.track))
        import os
        os.utime(self.audio, ns=(self.audio.stat().st_atime_ns, self.audio.stat().st_mtime_ns + 1))
        second = self.server.video_exports.visualization(self.track)
        self.assertNotEqual(first["source"], second["source"])
        self.assertEqual(first["frames"], second["frames"])
