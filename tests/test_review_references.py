"""Reference clips for producer reviews: mocked providers only, no paid calls."""
import array
import base64
import contextlib
import hashlib
import io
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
import wave
from unittest.mock import patch
from urllib.parse import quote

from review_client import listen
from review_recipe import (GENERATED_TAKE, GEMINI_INLINE_REQUEST_BYTES, MP3_BITRATE, attribute_observations,
                           send_estimate)
from reviews import REFERENCE_DEFAULTS, Reviews
from studio import StudioServer
from studio_core import Generator, validate_recipe
import test_studio
from test_reviews import FakeKeychain, fake_listener
from test_studio import StudioFixture, recipe, wait_until

MEDIA = unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
HEADERS = {"Content-Type": "application/json", "X-Riff-Request": "1"}


def write_clip(path, seconds=1.0, rate=8000):
    with wave.open(str(path), "wb") as clip:
        clip.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        clip.writeframes(array.array("h", [300, -300] * int(seconds * rate / 2)).tobytes())
    return Path(path)


class ReferenceFixture(StudioFixture):
    def setUp(self):
        super().setUp()
        self.track_id = self.store.add_track(self.audio, recipe(steps=19, max_seconds=420), {})
        self.keys = FakeKeychain()
        self.reviews = Reviews(self.store, self.keys, fake_listener)
        self.reviews.configure({"enabled": True, "api_key": "test-secret-value", "model": "custom/audio-model"})

    def tearDown(self):
        self.reviews.close()
        super().tearDown()

    def clip(self, name="clip.wav", seconds=1.0, **details):
        return self.reviews.add_reference(write_clip(self.root / name, seconds), details, name)

    def finish(self, review):
        wait_until(lambda: self.reviews.get(review["id"])["status"] in ("done", "failed", "cancelled"))
        return self.reviews.get(review["id"])

    def review(self, references=(), focus="Make it stranger."):
        return self.finish(self.reviews.submit(self.track_id, {"focus": focus, "references": list(references)}))

    def recording(self):
        return self.store.track(self.track_id), self.audio.read_bytes()


@MEDIA
class AttachmentTests(ReferenceFixture):
    def test_clips_are_hashed_opaque_attachments_outside_the_repository(self):
        first = self.clip("first take.wav", label="Tape hiss", link="https://example.org/session", note="Side A")
        self.assertEqual(set(first), {"id", "sha256", "label", "link", "note", "duration", "bytes", "format"})
        self.assertEqual((first["label"], first["link"], first["note"], first["format"]),
                         ("Tape hiss", "https://example.org/session", "Side A", "wav"))
        stored = self.store.data_root / "review-references" / first["sha256"]
        self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), first["sha256"])
        self.assertEqual(first["bytes"], stored.stat().st_size)
        self.assertAlmostEqual(first["duration"], 1.0, places=2)
        self.assertTrue(stored.resolve().is_relative_to(self.store.data_root))
        self.assertFalse(stored.resolve().is_relative_to(Path(__file__).resolve().parent.parent))
        # The label defaults to the file name, and nothing returned names a local path.
        second = self.clip("Room tone.wav", seconds=2.0)
        self.assertEqual(second["label"], "Room tone")
        listing = self.reviews.references()
        self.assertEqual([item["id"] for item in listing["references"]], [first["id"], second["id"]])
        self.assertNotIn(str(self.root), json.dumps([first, second, listing]))

    def test_unsupported_empty_and_unreadable_clips_are_rejected_before_storage(self):
        text = self.root / "notes.wav"
        text.write_text("not audio at all")
        empty = self.root / "empty.wav"
        empty.write_bytes(b"")
        # A playlist can point FFmpeg at other files; only real audio containers are kept.
        playlist = self.root / "list.wav"
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nfile:///etc/hostname\n#EXT-X-ENDLIST\n")
        for path, message in ((text, "Riff could not read|not a supported"), (empty, "empty"),
                              (playlist, "Riff could not read|not a supported")):
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, message):
                self.reviews.add_reference(path, {}, path.name)
        with self.assertRaises(OSError):
            self.reviews.add_reference(self.root / "missing.wav", {}, "missing.wav")
        video = self.root / "picture.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=1",
                        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1", "-shortest", str(video)], check=True)
        with self.assertRaisesRegex(ValueError, "not a supported audio clip"):
            self.reviews.add_reference(video, {}, video.name)
        self.assertEqual(self.reviews.references()["references"], [])
        self.assertFalse((self.store.data_root / "review-references").exists())

    def test_size_duration_and_count_are_reported_never_enforced(self):
        # Four clips, one of them long: all attach, and the review would send them all.
        clips = [self.clip(f"clip-{number}.wav", seconds=1.0 + number) for number in range(4)]
        listing = self.reviews.references(self.track_id)
        self.assertEqual(len(listing["references"]), 4)
        estimate = listing["estimate"]
        take = self.store.track(self.track_id)["audio"]["duration"]
        seconds = take + sum(item["duration"] for item in clips)
        self.assertEqual(estimate["clips"], 4)
        self.assertAlmostEqual(estimate["audio_seconds"], seconds)
        self.assertEqual(estimate["source_bytes"], sum(item["bytes"] for item in clips))
        self.assertEqual(estimate["estimated_sent_bytes"], int(seconds * MP3_BITRATE / 8 * 4 / 3))
        self.assertEqual(estimate["notices"], [])
        review = self.review([item["id"] for item in clips])
        self.assertEqual(review["status"], "done")
        self.assertEqual(len(review["references"]), 4)

    def test_notices_cite_documented_gemini_limits_only_for_google_models_and_never_block(self):
        long_clip = [{"duration": 700.0, "bytes": 1}]
        google = send_estimate(long_clip, 30, "google/gemini-3.8-flash")
        self.assertGreater(google["estimated_sent_bytes"], GEMINI_INLINE_REQUEST_BYTES)
        self.assertEqual(len(google["notices"]), 1)
        self.assertIn("ai.google.dev/gemini-api/docs/audio", google["notices"][0])
        self.assertIn("You can still submit", google["notices"][0])
        self.assertEqual(send_estimate(long_clip, 30, "custom/audio-model")["notices"], [])
        hours = send_estimate([{"duration": 10 * 3600.0, "bytes": 1}], 0, "google/gemini-3.8-flash")
        self.assertEqual(len(hours["notices"]), 2)
        self.assertIn("9.5 hours", hours["notices"][1])
        # The studio reports the same notice for the connected model and still accepts the review.
        self.reviews.configure({"model": "google/gemini-3.8-flash"})
        with patch("reviews.send_estimate", wraps=send_estimate) as spy:
            self.clip("short.wav")
            self.reviews.references(self.track_id)
        self.assertEqual(spy.call_args.args[2], "google/gemini-3.8-flash")

    def test_probe_timeout_is_a_documented_setting_with_validation(self):
        self.assertEqual({key: self.reviews.settings()[key] for key in REFERENCE_DEFAULTS}, REFERENCE_DEFAULTS)
        docs = (Path(__file__).resolve().parent.parent / "docs/capabilities.md").read_text()
        for key, value in REFERENCE_DEFAULTS.items():
            self.assertRegex(docs, rf"\| `{key}` \| {value}\b")
        self.assertEqual(self.reviews.configure({"reference_probe_seconds": 2.5})["reference_probe_seconds"], 2.5)
        self.assertEqual(self.reviews.connection()["reference_probe_seconds"], 2.5)
        for bad in (0, -1, True, "9", float("nan"), float("inf"), None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.reviews.configure({"reference_probe_seconds": bad})
        self.assertEqual(self.reviews.settings()["reference_probe_seconds"], 2.5)
        # The removed gates are gone from the settings.
        self.assertFalse({"reference_max_clips", "reference_max_seconds", "reference_max_bytes"} & set(self.reviews.settings()))

    def hanging_probe(self):
        """Replace ffprobe with a process that never finishes, to model a stalled read."""
        real, started = subprocess.Popen, []
        def hang(command, *args, **kwargs):
            process = real(["sleep", "300"], *args, **kwargs)
            started.append(process)
            return process
        return patch("reviews.subprocess.Popen", side_effect=hang), started

    def test_a_hung_probe_times_out_clearly_and_is_killed(self):
        self.reviews.configure({"reference_probe_seconds": 0.3})
        patcher, started = self.hanging_probe()
        began = time.monotonic()
        with patcher, self.assertRaisesRegex(ValueError, r"stopped checking this clip after 0\.3 seconds.*reference_probe_seconds"):
            self.clip("stalled.wav")
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll(), "the probe process is gone")
        self.assertEqual(self.reviews.references()["references"], [])
        self.assertEqual(self.reviews.probes, set())

    def test_a_check_in_progress_can_be_cancelled(self):
        patcher, started = self.hanging_probe()
        outcome = []
        def upload():
            try:
                self.clip("slow.wav")
            except ValueError as error:
                outcome.append(str(error))
        with patcher:
            thread = threading.Thread(target=upload)
            thread.start()
            wait_until(lambda: self.reviews.probes)
            self.assertEqual(self.reviews.cancel_probes(), {"cancelled": 1})
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, ["Checking this clip was cancelled."])
        self.assertIsNotNone(started[0].poll())
        self.assertEqual(self.reviews.references()["references"], [])
        self.assertEqual(self.reviews.cancel_probes(), {"cancelled": 0})
        # Nothing is left over: the next clip checks normally.
        self.assertEqual(self.clip("fine.wav")["format"], "wav")

    def test_details_are_edited_and_removed_until_a_review_uses_them(self):
        item = self.clip(label="Original label")
        changed = self.reviews.update_reference(item["id"], {"label": "Better label", "link": "https://example.org/a", "note": "Note"})
        self.assertEqual((changed["label"], changed["link"], changed["note"]), ("Better label", "https://example.org/a", "Note"))
        self.assertEqual((changed["sha256"], changed["bytes"]), (item["sha256"], item["bytes"]))
        for bad in ({"sha256": "0" * 64}, {"label": ""}, {"link": "file:///etc/passwd"}, {"link": "javascript:alert(1)"}, {"note": 4}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.reviews.update_reference(item["id"], bad)
        stored = self.store.data_root / "review-references" / item["sha256"]
        self.reviews.remove_reference(item["id"])
        self.assertEqual(self.reviews.references()["references"], [])
        self.assertFalse(stored.exists())
        for action in (lambda: self.reviews.remove_reference(item["id"]),
                       lambda: self.reviews.update_reference(item["id"], {"label": "x"})):
            with self.assertRaises(KeyError):
                action()
        with self.assertRaisesRegex(ValueError, "removed or already belongs"):
            self.reviews.submit(self.track_id, {"references": [item["id"]]})


@MEDIA
class BlobLifetimeTests(ReferenceFixture):
    def blob(self, item):
        return self.store.data_root / "review-references" / item["sha256"]

    def test_a_shared_file_is_deleted_only_when_no_record_or_receipt_needs_it(self):
        path = write_clip(self.root / "shared.wav", 1.0)
        one = self.reviews.add_reference(path, {"label": "Same clip, first label"}, "a.wav")
        two = self.reviews.add_reference(path, {"label": "Same clip, second label"}, "b.wav")
        self.assertEqual(one["sha256"], two["sha256"])
        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual(len(list((self.store.data_root / "review-references").iterdir())), 1)
        self.reviews.remove_reference(one["id"])
        self.assertTrue(self.blob(two).is_file(), "another pending record still uses the file")
        self.assertEqual([item["label"] for item in self.reviews.references()["references"]], ["Same clip, second label"])
        self.reviews.remove_reference(two["id"])
        self.assertFalse(self.blob(two).exists())
        self.assertEqual(list((self.store.data_root / "review-references").iterdir()), [])

    def test_a_receipt_or_a_consumed_record_keeps_the_file_for_every_other_record(self):
        path = write_clip(self.root / "kept.wav", 1.0)
        used = self.reviews.add_reference(path, {"label": "Used"}, "a.wav")
        review = self.review([used["id"]])
        self.assertEqual(review["status"], "done")
        spare = self.reviews.add_reference(path, {"label": "Unconsumed twin"}, "b.wav")
        self.reviews.remove_reference(spare["id"])
        self.assertTrue(self.blob(used).is_file(), "the review receipt still names it")
        # Removing a re-attached copy of the receipt's clip also leaves the file.
        copy = self.reviews.reuse_references(review["id"])["references"][0]
        self.reviews.remove_reference(copy["id"])
        self.assertTrue(self.blob(used).is_file())
        retry = self.reviews.reuse_references(review["id"])["references"][0]
        self.assertEqual(self.review([retry["id"]])["usage"]["seen"], [used["sha256"]])

    def test_removing_an_unconsumed_clip_deletes_a_file_no_receipt_names(self):
        first = self.clip("one.wav", seconds=1.0)
        second = self.clip("two.wav", seconds=2.0)
        self.review([first["id"]])
        self.reviews.remove_reference(second["id"])
        self.assertFalse(self.blob(second).exists())
        self.assertTrue(self.blob(first).is_file())


@MEDIA
class MediaToolTests(ReferenceFixture):
    def test_ffprobe_runs_from_an_argument_list_without_a_shell(self):
        calls = []
        real = subprocess.Popen
        def record(command, *args, **kwargs):
            calls.append((command, kwargs))
            return real(command, *args, **kwargs)
        with patch("reviews.subprocess.Popen", side_effect=record):
            self.clip("probe.wav; echo unsafe.wav", label="Odd name")
        self.assertEqual([command[0] for command, _ in calls], ["ffprobe"])
        for command, kwargs in calls:
            self.assertIsInstance(command, list)
            self.assertFalse(kwargs.get("shell"))
            # Shell metacharacters in a name stay inside one argument.
            self.assertTrue(command[-1].endswith("probe.wav; echo unsafe.wav"))
            self.assertEqual(sum("unsafe" in part for part in command), 1)


@MEDIA
class ReviewWithReferencesTests(ReferenceFixture):
    def test_receipt_keeps_labels_hashes_provenance_model_and_an_executable_recipe_without_paths(self):
        one = self.clip("one.wav", label="Broadside", link="https://example.org/one")
        two = self.clip("two.wav", seconds=2.0, label="Hall mix", note="Recorded live in 1974")
        review = self.review([one["id"], two["id"]])
        self.assertEqual(review["status"], "done", review)
        self.assertEqual(review["model"], "custom/audio-model")
        self.assertEqual(review["references"], [one, two])
        # The worker handed the client the stored files, in listening order.
        self.assertEqual(review["usage"]["seen"], [one["sha256"], two["sha256"]])
        self.assertEqual([(item["source"], item["format"]) for item in review["sent_audio"]],
                         [(GENERATED_TAKE, "mp3"), (one["id"], "mp3"), (two["id"], "mp3")])
        self.assertEqual(validate_recipe(review["generation"])["seed"], "42")
        self.assertEqual(review["generation"]["seed"], "42")
        by_source = {item["source"]: item for item in review["observations"]}
        self.assertEqual(set(by_source), {GENERATED_TAKE, one["id"], two["id"]})
        self.assertEqual(review["warnings"], [])
        self.assertNotIn(str(self.root), json.dumps(review))
        self.assertNotIn("path", json.dumps(review["references"]))
        self.assertEqual(self.reviews.references()["references"], [])
        # A saved receipt survives a restart and reads the same.
        again = Reviews(self.store, self.keys, fake_listener)
        try:
            self.assertEqual(again.get(review["id"])["references"], [one, two])
        finally:
            again.close()
        with self.assertRaisesRegex(ValueError, "removed or already belongs"):
            self.reviews.submit(self.track_id, {"references": [one["id"]]})

    def test_reviews_without_references_are_unchanged(self):
        review = self.review()
        self.assertEqual(review["status"], "done")
        self.assertEqual((review["references"], review["observations"], review["warnings"], review["sent_audio"]), ([], [], [], []))
        self.assertEqual(review["usage"], {"cost": .01})
        for payload in ({"references": "abc"}, {"references": [3]}, {"references": ["a", "a"]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.reviews.submit(self.track_id, payload)

    def test_partial_attribution_is_reported_as_warnings_not_hidden(self):
        one, two = self.clip("one.wav", label="First"), self.clip("two.wav", label="Second")
        partial = self.review([one["id"], two["id"]], focus="partial")
        self.assertEqual(partial["status"], "done")
        self.assertEqual({item["source"] for item in partial["observations"]}, {GENERATED_TAKE, one["id"]})
        self.assertEqual(partial["warnings"], ["The model gave no observations about reference Second."])
        reused = self.reviews.reuse_references(partial["id"])["references"]
        unattributed = self.review([item["id"] for item in reused], focus="unattributed")
        self.assertNotIn("invented", {item["source"] for item in unattributed["observations"]})
        self.assertIn("The model returned an observation the studio could not attribute and left it out.", unattributed["warnings"])

    def test_attribution_rules_cover_every_shape_of_answer(self):
        references = [{"id": "a" * 32, "label": "One"}, {"id": "b" * 32, "label": "Two"}]
        self.assertEqual(attribute_observations(None, references)[0], [])
        self.assertEqual(len(attribute_observations("text", references)[1]), 1)
        full = [{"source": GENERATED_TAKE, "timestamp": "", "note": " Tight. "},
                {"source": "a" * 32, "timestamp": "0:03", "note": "Wide"},
                {"source": "b" * 32, "timestamp": "", "note": "Dark"}]
        self.assertEqual(attribute_observations(full, references), (
            [{**full[0], "note": "Tight."}, full[1], full[2]], []))
        kept, warnings = attribute_observations([full[0], {"source": "a" * 32, "note": ""}, "bad", {"source": "c" * 32, "note": "x"}], references)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(warnings), 3)

    def test_provider_failure_cancellation_and_retry_leave_the_recording_unchanged(self):
        before = self.recording()
        clip = self.clip(label="Calibration")
        failed = self.review([clip["id"]], focus="fail")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Provider rejected [redacted]", failed["error"])
        self.assertNotIn("test-secret-value", json.dumps(failed))
        self.assertNotIn(str(self.root), failed["error"])
        self.assertEqual(failed["references"], [clip])
        self.assertEqual(self.recording(), before)
        # The failed receipt keeps the clip, so a retry attaches it again and finishes.
        retry = self.reviews.reuse_references(failed["id"])["references"]
        self.assertEqual([{k: v for k, v in item.items() if k != "id"} for item in retry],
                         [{k: v for k, v in clip.items() if k != "id"}])
        self.assertNotEqual(retry[0]["id"], clip["id"])
        waiting = self.reviews.submit(self.track_id, {"focus": "wait", "references": [retry[0]["id"]]})
        wait_until(lambda: self.reviews.get(waiting["id"])["status"] == "running")
        self.reviews.cancel(waiting["id"])
        cancelled = self.finish(waiting)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.recording(), before)
        again = self.reviews.reuse_references(cancelled["id"])["references"]
        done = self.review([again[0]["id"]])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["usage"]["seen"], [clip["sha256"]])
        self.assertEqual(self.recording(), before)

    def test_clip_removed_or_changed_after_queueing_fails_the_review_clearly(self):
        clip = self.clip(label="Fragile")
        stored = self.store.data_root / "review-references" / clip["sha256"]
        with patch.object(self.reviews, "wake"):
            queued = self.reviews.submit(self.track_id, {"references": [clip["id"]]})
        stored.write_bytes(b"changed")
        self.reviews.wake.set()
        failed = self.finish(queued)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("'Fragile' is missing or has changed", failed["error"])
        self.assertNotIn(str(self.root), failed["error"])
        stored.unlink()
        with self.assertRaisesRegex(ValueError, "no longer stored"):
            self.reviews.reuse_references(failed["id"])
        # Submitting a clip whose file vanished is refused up front.
        other = self.clip("other.wav", seconds=3.0, label="Other")
        (self.store.data_root / "review-references" / other["sha256"]).unlink()
        with self.assertRaisesRegex(ValueError, "no longer stored"):
            self.reviews.submit(self.track_id, {"references": [other["id"]]})

    def test_references_are_reused_edited_and_removed_while_receipts_keep_their_copy(self):
        one = self.clip("one.wav", label="Keep", link="https://example.org/keep")
        two = self.clip("two.wav", seconds=2.0, label="Drop")
        first = self.review([one["id"], two["id"]])
        reused = self.reviews.reuse_references(first["id"])["references"]
        self.assertEqual([item["label"] for item in reused], ["Keep", "Drop"])
        self.reviews.update_reference(reused[0]["id"], {"label": "Keep, relabelled", "note": "Second pass"})
        self.reviews.remove_reference(reused[1]["id"])
        # The removed clip's file stays because the first receipt names it.
        self.assertTrue((self.store.data_root / "review-references" / two["sha256"]).is_file())
        second = self.review([reused[0]["id"]])
        self.assertEqual([item["label"] for item in second["references"]], ["Keep, relabelled"])
        self.assertEqual(second["references"][0]["note"], "Second pass")
        self.assertEqual(self.reviews.get(first["id"])["references"], [one, two])
        with self.assertRaisesRegex(ValueError, "no reference clips"):
            self.reviews.reuse_references(self.review()["id"])

    def test_variation_keeps_seed_score_and_review_lineage_with_the_references(self):
        source = self.store.track(self.track_id)["recipe"]
        clip = self.clip(label="Calibration", link="https://example.org/ref")
        review = self.review([clip["id"]])
        self.assertEqual(review["status"], "done")
        self.assertEqual(review["generation"]["seed"], source["seed"])
        generator = Generator(self.store, lambda recipe, output: ["/usr/bin/false"])
        try:
            job = generator.submit({**review["generation"], "parent_track_id": self.track_id, "review_id": review["id"]})
            self.assertEqual((job["recipe"]["seed"], job["recipe"]["parent_track_id"], job["recipe"]["review_id"]),
                             (source["seed"], self.track_id, review["id"]))
            self.assertEqual((job["recipe"]["abc"], job["recipe"]["score_source"], job["recipe"]["cot"]),
                             (review["generation"]["abc"], review["generation"]["score_source"], review["generation"]["cot"]))
            # The clips are recorded as provenance on the take, derived from the saved review. A claim from the caller is ignored.
            expected = [{"id": clip["id"], "sha256": clip["sha256"], "label": "Calibration"}]
            self.assertEqual(job["recipe"]["review_references"], expected)
            claimed = generator.submit({**review["generation"], "parent_track_id": self.track_id, "review_id": review["id"],
                                        "review_references": [{"id": "x", "sha256": "0" * 64, "label": "forged"}]})
            self.assertEqual(claimed["recipe"]["review_references"], expected)
            bare = generator.submit({**review["generation"], "parent_track_id": self.track_id})
            self.assertNotIn("review_references", bare["recipe"])
            plain = self.review()
            self.assertNotIn("review_references", generator.submit({**plain["generation"], "review_id": plain["id"]})["recipe"])
            # Informing context only: neither generation nor the next review's prompt reads it.
            from review_recipe import generation_context
            finished = self.store.outputs / "variation.wav"
            shutil.copyfile(self.audio, finished)
            taken = self.store.add_track(finished, job["recipe"], {})
            saved = self.store.track(taken)["recipe"]
            self.assertEqual(saved["review_references"], expected)
            self.assertEqual((saved["seed"], saved["parent_track_id"], saved["review_id"]),
                             (source["seed"], self.track_id, review["id"]))
            self.assertNotIn("review_references", generation_context(saved))
            self.assertNotIn("review_references", validate_recipe(saved))
            # Writers building a variation see the clips' provenance, never the files.
            context = generator.writing_settings({**review["generation"], "parent_track_id": self.track_id,
                                                  "review_id": review["id"]})["reference"]["review"]
            self.assertEqual(context["references"], [{key: clip[key] for key in
                             ("id", "sha256", "label", "link", "note", "duration", "format")}])
            self.assertEqual({item["source"] for item in context["observations"]}, {GENERATED_TAKE, clip["id"]})
            self.assertNotIn(str(self.root), json.dumps(context))
        finally:
            generator.close()


def encode_capturing(calls):
    def encode(command, **kwargs):
        calls.append(list(command))
        Path(command[-1]).write_bytes(b"mp3:" + Path(command[command.index("-i") + 1]).read_bytes())
    return encode


class OperationSurfaceTests(ReferenceFixture):
    """A take made from a review records its reference clips on every route that queues it."""
    request = test_studio.HttpTests.request

    def setUp(self):
        super().setUp()
        self.generator = Generator(self.store, lambda recipe, output: ["/usr/bin/false"])
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator, self.reviews)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        ready = patch("studio.platform_support.readiness", return_value={"ready": True})
        ready.start()
        self.addCleanup(ready.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.generator.close()
        self.thread.join()
        super().tearDown()

    def post(self, path, body):
        status, _, data = self.request("POST", path, json.dumps(body), HEADERS)
        return status, json.loads(data)

    def test_studio_route_and_take_develop_both_record_the_reviews_reference_clips(self):
        clip = self.clip(label="Calibration", link="https://example.org/ref")
        review = self.review([clip["id"]])
        expected = [{"id": clip["id"], "sha256": clip["sha256"], "label": "Calibration"}]
        status, studio = self.post("/api/generations", {**review["generation"], "parent_track_id": self.track_id, "review_id": review["id"]})
        self.assertEqual(status, 201, studio)
        self.assertEqual(studio["recipe"]["review_references"], expected)
        status, developed = self.post("/api/v1/operations/take.develop", {"source": self.track_id, "review_id": review["id"]})
        self.assertEqual(status, 201, developed)
        effective = developed["receipt"]["effective_recipe"]
        self.assertEqual((effective["review_id"], effective["review_references"]), (review["id"], expected))
        self.assertEqual(self.store.job(developed["job_id"])["recipe"]["review_references"], expected)
        self.assertEqual(developed["receipt"]["derivation"]["from"]["track_id"], self.track_id)
        # The draft shows the same provenance before anything is queued, and study.create agrees.
        status, draft = self.post("/api/v1/operations/take.develop", {"source": self.track_id, "review_id": review["id"], "enqueue": False})
        self.assertEqual((status, draft["validation"]["effective_recipe"]["review_references"]), (200, expected))
        status, created = self.post("/api/v1/operations/study.create", {"recipe": {**review["generation"], "review_id": review["id"]}})
        self.assertEqual((status, created["receipt"]["effective_recipe"]["review_references"]), (201, expected))
        # A claim from the caller is ignored, and a review with no clips adds nothing.
        status, claimed = self.post("/api/v1/operations/study.create", {"recipe": {
            **review["generation"], "review_id": review["id"], "review_references": [{"id": "x", "sha256": "0" * 64, "label": "forged"}]}})
        self.assertEqual(claimed["receipt"]["effective_recipe"]["review_references"], expected)
        plain = self.review()
        status, none = self.post("/api/v1/operations/take.develop", {"source": self.track_id, "review_id": plain["id"]})
        self.assertEqual(status, 201, none)
        self.assertNotIn("review_references", none["receipt"]["effective_recipe"])

    def test_take_develop_refuses_a_review_that_is_unknown_malformed_or_of_another_take(self):
        second = self.store.outputs / "other.wav"
        shutil.copyfile(self.audio, second)
        other = self.store.add_track(second, recipe(steps=7), {})
        review = self.review()
        for value, expected in (("0" * 32, 404), ("nope", 400), ([1], 400), (5, 400)):
            with self.subTest(review=value):
                self.assertEqual(self.post("/api/v1/operations/take.develop", {"source": self.track_id, "review_id": value})[0], expected)
        status, body = self.post("/api/v1/operations/take.develop", {"source": other, "review_id": review["id"]})
        self.assertEqual((status, body["error"]), (400, "That review is of a different take."))


class ProviderRequestTests(unittest.TestCase):
    """The client sends one direct multimodal request; nothing else transcribes or listens."""

    def setUp(self):
        import tempfile
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        (self.root / "take.wav").write_bytes(b"the generated take")
        self.references = []
        for number, (label, link, note) in enumerate((("Broadside", "https://example.org/one", ""),
                                                      ("Hall mix", "", "Recorded live"))):
            path = self.root / f"reference-{number}.wav"
            path.write_bytes(f"reference {number}".encode())
            self.references.append({"id": f"{number + 1}" * 32, "sha256": "f" * 64, "label": label, "link": link,
                                    "note": note, "duration": 41.5 + number, "bytes": path.stat().st_size,
                                    "format": "wav", "path": str(path)})
        self.settings = {"recipe": recipe(steps=19, max_seconds=28), "keep_lyrics": True, "focus": "Match the room.",
                         "model": "google/gemini-3.8-flash", "api_key": "test-secret",
                         "audio": str(self.root / "take.wav"), "duration": 27.52}

    def run_listen(self, settings=None, response=None, verbose=False):
        calls, requests, encodes = [], [], []
        proposal = {"notes": "0:04 Brighter than the take.", "summary": "Open the top end.",
                    "generation": {key: validate_recipe(self.settings["recipe"])[key]
                                   for key in __import__("review_recipe").FIELDS},
                    "observations": [{"source": GENERATED_TAKE, "timestamp": "0:01", "note": "Steady."}]}

        def respond(request, **kwargs):
            requests.append((request.full_url, json.loads(request.data)))
            return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(response or proposal)},
                                                    "finish_reason": "stop"}], "usage": {"cost": 0}}).encode())

        def run(command, **kwargs):
            self.assertIsInstance(command, list, "media tools take an argument list, never a shell string")
            self.assertFalse(kwargs.get("shell"))
            calls.append(command[0])
            encode_capturing(encodes)(command, **kwargs)
        with patch("review_client.shutil.which", return_value="ffmpeg"), \
                patch("review_client.subprocess.run", side_effect=run), \
                patch("review_client.urllib.request.urlopen", side_effect=respond):
            result = listen(settings or {**self.settings, "references": self.references})
        return result, requests, calls

    def test_take_and_both_references_travel_in_one_request(self):
        result, requests, calls = self.run_listen()
        self.assertEqual(len(requests), 1)
        url, request = requests[0]
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request["model"], "google/gemini-3.8-flash")
        parts = request["messages"][0]["content"]
        self.assertEqual([part["type"] for part in parts],
                         ["text", "input_audio", "text", "input_audio", "text", "input_audio"])
        heard = [base64.b64decode(part["input_audio"]["data"]) for part in parts if part["type"] == "input_audio"]
        self.assertEqual(heard, [b"mp3:the generated take", b"mp3:reference 0", b"mp3:reference 1"])
        self.assertEqual({part["input_audio"]["format"] for part in parts if part["type"] == "input_audio"}, {"mp3"})
        prompt = parts[0]["text"]
        for text in ("REFERENCE RECORDINGS", "Broadside", "https://example.org/one", "Hall mix", "Recorded live",
                     "41.5", "ARTIST'S FOCUS", "PRIOR GENERATION INPUTS", "SYMBOLIC REPRESENTATION"):
            self.assertIn(text, prompt)
        self.assertIn(self.references[0]["id"], parts[2]["text"])
        # The model sees identity and provenance, never a file location or hash.
        self.assertNotIn(str(self.root), json.dumps(request))
        self.assertNotIn("f" * 64, json.dumps(request))
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["notes", "summary", "observations", "generation"])
        self.assertEqual(schema["properties"]["observations"]["items"]["properties"]["source"]["enum"],
                         [GENERATED_TAKE, self.references[0]["id"], self.references[1]["id"]])
        self.assertEqual(result["observations"], [{"source": GENERATED_TAKE, "timestamp": "0:01", "note": "Steady."}])
        # The format and hash actually sent are recorded; the original's hash stays in the clip receipt.
        sent = [(GENERATED_TAKE, b"mp3:the generated take"), (self.references[0]["id"], b"mp3:reference 0"),
                (self.references[1]["id"], b"mp3:reference 1")]
        self.assertEqual(result["sent_audio"], [{"source": source, "format": "mp3", "bytes": len(data),
                                                 "sha256": hashlib.sha256(data).hexdigest()} for source, data in sent])
        self.assertEqual(len(result["generation"]), len(__import__("review_recipe").FIELDS))

    def test_no_transcription_service_or_second_model_is_called(self):
        _, requests, calls = self.run_listen()
        self.assertEqual(calls, ["ffmpeg"] * 3)
        self.assertEqual(len(requests), 1)
        self.assertEqual({key for key in requests[0][1]}, {"model", "provider", "response_format", "messages"})
        self.assertFalse([name for name in sys.modules if re.search(r"whisper|speech_recognition|vosk|asr", name, re.I)])
        root = Path(__file__).resolve().parent.parent
        for name in ("reviews.py", "review_client.py", "review_recipe.py"):
            self.assertIsNone(re.search(r"whisper|transcriptions|speech[-_]to|\basr\b|/audio/", (root / name).read_text(), re.I), name)

    def test_without_references_the_request_is_the_ordinary_review(self):
        _, requests, calls = self.run_listen({**self.settings, "references": []},
                                             response={"notes": "n", "summary": "s",
                                                       "generation": {key: validate_recipe(self.settings["recipe"])[key]
                                                                      for key in __import__("review_recipe").FIELDS}})
        parts = requests[0][1]["messages"][0]["content"]
        self.assertEqual([part["type"] for part in parts], ["text", "input_audio"])
        self.assertNotIn("REFERENCE RECORDINGS", parts[0]["text"])
        schema = requests[0][1]["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["notes", "summary", "generation"])
        self.assertNotIn("observations", schema["properties"])
        self.assertEqual(calls, ["ffmpeg"])

    def test_reference_encoding_failure_names_the_clip_not_the_file(self):
        def fail(command, **kwargs):
            if "reference-2.mp3" in command[-1]:
                raise subprocess.CalledProcessError(1, command, stderr=b"decoder failed")
            Path(command[-1]).write_bytes(b"mp3")
        with patch("review_client.shutil.which", return_value="ffmpeg"), \
                patch("review_client.subprocess.run", side_effect=fail), \
                patch("review_client.urllib.request.urlopen") as network, \
                self.assertRaises(ValueError) as caught:
            listen({**self.settings, "references": self.references})
        self.assertIn("'Hall mix'", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))
        network.assert_not_called()


@MEDIA
class LiveCheckScriptTests(unittest.TestCase):
    """The opt-in live check is exercised here against a mocked provider, never the network."""

    def test_script_sends_three_audio_parts_in_one_request_and_reports_attribution(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("live_check", Path(__file__).resolve().parent.parent / "scripts/check_reference_audio_live.py")
        live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live)
        seen = []
        def respond(request, **kwargs):
            body = json.loads(request.data)
            seen.append((request.full_url, body))
            ids = [part["text"].split("id ")[1][:32] for part in body["messages"][0]["content"] if part["type"] == "text"][1:]
            take = validate_recipe({"title": "Live check", "lyrics": "", "style": "A single sustained tone", "mode": "instrumental",
                                    "max_seconds": 3, "steps": 8, "seed": "1", "cot": "off", "abc": ""})
            answer = {"notes": "n", "summary": "s", "generation": {key: take[key] for key in __import__("review_recipe").FIELDS},
                      "observations": [{"source": source, "timestamp": "", "note": "heard"} for source in [GENERATED_TAKE, *ids]]}
            return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}]}).encode())
        with patch("review_client.urllib.request.urlopen", side_effect=respond), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(live.check("custom/audio-model", "key"))
        self.assertEqual(len(seen), 1)
        parts = seen[0][1]["messages"][0]["content"]
        self.assertEqual(sum(part["type"] == "input_audio" for part in parts), 3)


@MEDIA
class ReferenceHttpTests(ReferenceFixture):
    request = test_studio.HttpTests.request

    def setUp(self):
        super().setUp()
        self.server = StudioServer(("127.0.0.1", 0), self.store, None, self.reviews)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def upload(self, data, name="clip.wav", metadata=None, headers=None):
        headers = {"Content-Type": "application/octet-stream", "X-Riff-Request": "1",
                   "X-Riff-Reference-Metadata": json.dumps(metadata or {}), **(headers or {})}
        status, _, body = self.request("POST", "/api/review/references?name=" + quote(name), data, headers)
        return status, json.loads(body)

    def test_upload_list_edit_remove_and_review_through_the_same_routes_agents_use(self):
        data = write_clip(self.root / "http.wav", 1.5).read_bytes()
        status, item = self.upload(data, "Reference mix.wav", {"label": "Mix", "link": "https://example.org/mix"})
        self.assertEqual(status, 201, item)
        self.assertEqual((item["label"], item["link"], item["sha256"]), ("Mix", "https://example.org/mix", hashlib.sha256(data).hexdigest()))
        listing = json.loads(self.request("GET", "/api/review/references")[2])
        self.assertEqual(listing["references"], [item])
        self.assertEqual(listing["settings"], REFERENCE_DEFAULTS)
        self.assertEqual(listing["estimate"]["clips"], 1)
        with_take = json.loads(self.request("GET", f"/api/review/references?track_id={self.track_id}")[2])["estimate"]
        self.assertGreater(with_take["audio_seconds"], listing["estimate"]["audio_seconds"])
        self.assertEqual(self.request("GET", "/api/review/references?track_id=nope")[0], 400)
        status, _, body = self.request("POST", "/api/review/references/cancel", "{}", HEADERS)
        self.assertEqual((status, json.loads(body)), (200, {"cancelled": 0}))
        self.assertEqual(self.request("POST", "/api/review/references/cancel", "{}", {"Content-Type": "application/json"})[0], 403)
        patched = json.loads(self.request("PATCH", f"/api/review/references/{item['id']}",
                                          json.dumps({"label": "Mix v2", "note": "Take two"}), HEADERS)[2])
        self.assertEqual((patched["label"], patched["note"]), ("Mix v2", "Take two"))
        status, _, body = self.request("POST", f"/api/tracks/{self.track_id}/reviews",
                                       json.dumps({"focus": "", "references": [item["id"]]}), HEADERS)
        self.assertEqual(status, 201, body)
        review = json.loads(body)
        self.assertEqual(review["references"][0]["label"], "Mix v2")
        done = self.finish(review)
        self.assertEqual(done["status"], "done")
        fetched = json.loads(self.request("GET", f"/api/reviews/{review['id']}")[2])
        self.assertNotIn(str(self.root), json.dumps(fetched))
        self.assertEqual(fetched["observations"][1]["source"], item["id"])
        status, _, body = self.request("POST", "/api/review/references/reuse", json.dumps({"review_id": review["id"]}), HEADERS)
        self.assertEqual(status, 201, body)
        copy = json.loads(body)["references"][0]
        self.assertEqual(self.request("DELETE", f"/api/review/references/{copy['id']}", "{}", HEADERS)[0], 200)
        self.assertEqual(self.request("DELETE", f"/api/review/references/{copy['id']}", "{}", HEADERS)[0], 404)
        self.assertEqual(self.request("POST", "/api/review/references/reuse", json.dumps({"review_id": 7}), HEADERS)[0], 400)
        self.assertEqual(self.request("POST", "/api/review/references/reuse", json.dumps({"review_id": "0" * 32}), HEADERS)[0], 404)

    def test_upload_errors_are_clear_and_nothing_is_stored(self):
        status, error = self.upload(b"")
        self.assertEqual(status, 400)
        status, error = self.upload(b"plain text, not audio")
        self.assertEqual(status, 400)
        self.assertIn("could not read", error["error"])
        status, error = self.upload(b"x", metadata={"link": "ftp://example.org"})
        self.assertEqual(status, 400)
        self.assertEqual(self.reviews.references()["references"], [])
        self.assertFalse((self.store.data_root / "review-references").exists())

    def test_uploads_and_edits_must_come_from_the_studio(self):
        data = write_clip(self.root / "guard.wav", .2).read_bytes()
        self.assertEqual(self.upload(data, headers={"Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(self.upload(data, headers={"X-Riff-Request": "0"})[0], 403)
        status, _, _ = self.request("POST", "/api/review/references?name=x.wav", data, {"X-Riff-Request": "1", "Content-Type": "text/plain"})
        self.assertEqual(status, 403)
        status, item = self.upload(data)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("PATCH", f"/api/review/references/{item['id']}", "{}", {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.request("DELETE", f"/api/review/references/{item['id']}", "{}",
                                      {**HEADERS, "Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(len(self.reviews.references()["references"]), 1)

    def test_capabilities_describe_the_reference_operations_and_the_review_state_shows_limits(self):
        status, _, body = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200)
        operations = json.loads(body)["operations"]
        for name in ("review_references", "review_reference_add", "review_reference_edit",
                     "review_reference_remove", "review_reference_reuse", "review_reference_cancel"):
            self.assertIn(name, operations)
        self.assertIn("references", operations["review"]["body"])
        settings = json.loads(self.request("GET", "/api/review/settings")[2])
        self.assertEqual({key: settings[key] for key in REFERENCE_DEFAULTS}, REFERENCE_DEFAULTS)


if __name__ == "__main__":
    unittest.main()
