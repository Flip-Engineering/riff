import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import install
import launcher
import writer
from scripts import package_release
import model_options
import platform_support
import run
import setup_engine
from symbolic import read_plan
from studio_core import Generator, validate_recipe
from test_studio import StudioFixture, wait_until


class PlatformTests(unittest.TestCase):
    def test_fresh_workspace_starts_without_weights_on_current_python(self):
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run([sys.executable, "studio.py", "--check"], capture_output=True, text=True,
                                     env={**os.environ, "RIFF_HOME": directory})
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["app"], "Riff")
            self.assertFalse((Path(directory) / "data").exists())

    def test_cuda_device_threads_models_and_controls_reach_native_runtime(self):
        settings = {"backend": "cuda", "device": 2, "binary": "/engine/audiocpp_cli", "threads": 6,
                    "model_root": "/weights", "model_file": "custom-q8.gguf", "vae_file": "decoder.gguf"}
        with patch("platform_support.settings", return_value=settings):
            command = run.build_command(lyrics="", style="", max_seconds=12, steps=19, cot="full", seed=9,
                                        threads=6, output=Path("/tmp/score.wav"), render_mode="plan",
                                        refinement={"abc_top_p": .8, "semantic_repetition_penalty": 1.1})
        self.assertIn("cuda", command)
        self.assertEqual(command[command.index("--device") + 1], "2")
        self.assertIn("yue2.model_gguf=custom-q8.gguf", command)
        self.assertIn("abc_top_p=0.8", command)
        self.assertIn("plan_only=true", command)
        self.assertIn("score_tokens_out=/tmp/score.plan.json", command)

    def test_backend_build_flags_are_exclusive(self):
        for backend in ("metal", "cuda", "cpu"):
            command = setup_engine.cmake_command("source", "build", backend, "75;86")
            self.assertIn("-DAUDIOCPP_MODELS=yue2", command)
            self.assertIn("-DENGINE_ENABLE_METAL=" + ("ON" if backend == "metal" else "OFF"), command)
            self.assertIn("-DENGINE_ENABLE_CUDA=" + ("ON" if backend == "cuda" else "OFF"), command)
            if backend == "cuda": self.assertIn("-DCMAKE_CUDA_ARCHITECTURES=75;86", command)

    def test_numeric_controls_obey_runtime_ranges_and_cross_field_lengths(self):
        for values in ({"abc_top_p": 1.1}, {"abc_top_k": 2.5}, {"semantic_top_p": True},
                       {"abc_repetition_penalty": 0}, {"abc_temperature": float("nan")},
                       {"abc_min_tokens": 99, "abc_max_tokens": 20}, {"semantic_min_tokens": 301},
                       {"not_a_control": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError): model_options.validate(values, 12)
        self.assertEqual(model_options.validate({"abc_max_tokens": 12000, "semantic_top_k": 2048}, 12)["abc_max_tokens"], 12000)


class SymbolicTests(StudioFixture):
    def test_plan_tokens_decode_to_actual_abc_without_reading_audio(self):
        vocabulary = self.root / "vocab"
        vocabulary.write_text(base64.b64encode(b"X:1\nK:Dm\nD2 F2").decode() + " 42\n")
        path = self.root / "take.plan.json"
        path.write_text(json.dumps({"tokens": [42, 151671], "truncated": True}))
        result = read_plan(path, vocabulary)
        self.assertEqual(result["abc"], "X:1\nK:Dm\nD2 F2")
        self.assertTrue(result["truncated"])
        self.assertTrue(path.with_suffix(".abc").exists())

    def test_plan_job_saves_a_reusable_score_without_an_audio_track(self):
        def command(recipe, output):
            return [sys.executable, "-c", "print('score complete')"]
        generator = Generator(self.store, command)
        result = {"abc": "X:1\nK:Dm\nD2 F2", "truncated": False, "token_count": 9}
        try:
            with patch("studio_core.read_plan", return_value=result):
                job = generator.submit({"mode": "free", "render_mode": "plan", "cot": "melody"})
                wait_until(lambda: any(p["id"] == job["id"] for p in self.store.snapshot()["plans"]))
            saved = self.store.snapshot()["plans"][0]
            self.assertEqual(saved["recipe"]["symbolic_plan"], result)
            self.assertFalse(self.store.snapshot()["tracks"])
            with self.assertRaises(ValueError): validate_recipe({"render_mode": "plan", "cot": "off"})
        finally: generator.close()


class InstallerTests(unittest.TestCase):
    def test_packaging_refuses_an_untracked_file_before_writing_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.2.0")
            (root / "package.json").write_text('{"version":"0.2.0"}')
            (root / "web").mkdir()
            private = root / "web/connection.env"; private.write_text("private draft settings")
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "VERSION", "package.json"], check=True)
            with patch.object(package_release, "ROOT", root), patch.object(package_release, "public_files", return_value=[private]):
                with self.assertRaisesRegex(ValueError, "Untracked"):
                    package_release.build()
            self.assertFalse((root / "dist").exists())

    def test_engine_update_applies_once_and_rollback_restores_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); data = root / "workspace/data"; data.mkdir(parents=True)
            settings = data / "engine.json"; settings.write_text('{"binary":"old","threads":4}')
            old = root / "releases/0.1.0"; old.mkdir(parents=True)
            new = root / "releases/0.2.0"; new.mkdir()
            (new / ".engine.json").write_text('{"binary":"new"}')
            launcher.activate_engine(root, new)
            self.assertEqual(json.loads(settings.read_text())["binary"], "new")
            settings.write_text('{"binary":"custom","threads":6}')
            launcher.activate_engine(root, new)
            self.assertEqual(json.loads(settings.read_text())["binary"], "custom")
            launcher.activate_engine(root, old, rollback_from="0.2.0")
            self.assertEqual(json.loads(settings.read_text()), {"binary": "old", "threads": 4})

    def test_new_model_pins_stage_separately_from_existing_models(self):
        with tempfile.TemporaryDirectory() as directory, patch("setup_engine.MODELS", Path(directory)):
            root = Path(directory); old = {"revision": "old", "files": {}}
            (root / "manifest.json").write_text(json.dumps(old))
            self.assertEqual(setup_engine.model_directory(old), root)
            new = setup_engine.model_directory({"revision": "new", "files": {}})
            self.assertEqual(new.parent, root / "sets")
            self.assertEqual(json.loads((root / "manifest.json").read_text()), old)

    def archive(self, root, entries):
        path = root / "test.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, data, kind in entries:
                info = tarfile.TarInfo(name); info.type = kind
                if kind == tarfile.SYMTYPE: info.linkname = "../../outside"
                info.size = len(data)
                archive.addfile(info, BytesIO(data) if kind == tarfile.REGTYPE else None)
        return path

    def test_archive_rejects_traversal_links_and_special_files(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("/tmp/outside", tarfile.REGTYPE),
                           ("link", tarfile.SYMTYPE), ("device", tarfile.CHRTYPE), ("..\\outside", tarfile.REGTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = self.archive(root, [(name, b"", kind)])
                with self.assertRaises(ValueError): install.extract_archive(path, root / "new")
                self.assertFalse((root / "outside").exists())

    def test_checksum_failure_does_not_change_installation_or_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "current.json").write_text('{"version":"0.1.0"}')
            library = root / "workspace/data"; library.mkdir(parents=True); (library / "song").write_text("keep")
            path = root / "bad.tar.gz"; path.write_bytes(b"bad download")
            with self.assertRaises(ValueError): install.stage_release(root, {"version": "0.2.0", "name": path.name, "sha256": "0" * 64}, archive=path)
            self.assertEqual(json.loads((root / "current.json").read_text())["version"], "0.1.0")
            self.assertEqual((library / "song").read_text(), "keep")

    def test_activation_preserves_previous_version_and_rejects_external_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "current.json").write_text('{"version":"0.1.0"}')
            target = root / "releases/0.2.0"; target.mkdir(parents=True); (target / "studio.py").write_text("pass")
            install.activate(root, target)
            self.assertEqual(json.loads((root / "current.json").read_text()), {"version": "0.2.0", "previous": "0.1.0"})
            with self.assertRaises(ValueError): install.activate(root, root)

    def test_release_requires_official_asset_and_digest(self):
        data = {"tag_name": "v0.2.0", "assets": [{"name": "riff-v0.2.0.tar.gz", "digest": "sha256:" + "a" * 64,
                "size": 42, "browser_download_url": "https://attacker.invalid/app.tar.gz"}]}
        with patch("install.urllib.request.urlopen", return_value=BytesIO(json.dumps(data).encode())):
            with self.assertRaises(ValueError): install.get_release()
        data["assets"][0]["browser_download_url"] = "https://github.com/Flip-Engineering/riff/releases/download/v0.2.0/riff-v0.2.0.tar.gz"
        with patch("install.urllib.request.urlopen", return_value=BytesIO(json.dumps(data).encode())):
            self.assertEqual(install.get_release()["version"], "0.2.0")


class ComposerTests(unittest.TestCase):
    def test_score_editor_sends_actual_notation_and_selected_model_and_returns_no_key(self):
        score = "X:1\nK:Dm\nD2 F2|"
        payload = {"task": "score", "abc": score, "brief": "Change the harmony", "lyrics": "Words",
                   "style": "Brass", "cot": "full", "model": "google/gemini-3.8-flash",
                   "api_key": "test-composer-credential", "writer_tokens": 8192}
        reply = {"choices": [{"message": {"content": json.dumps({"abc": score, "summary": "New harmony"})}}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(reply).encode())) as send:
            result = writer.write(payload)
        request = send.call_args.args[0]; body = json.loads(request.data)
        self.assertEqual(body["model"], payload["model"])
        self.assertEqual(body["max_tokens"], 8192)
        self.assertEqual(json.loads(body["messages"][1]["content"])["abc"], score)
        self.assertNotIn(payload["api_key"], json.dumps(result))
        self.assertEqual(result["abc"], score)

    def test_budget_exhaustion_keeps_partial_score_from_being_applied(self):
        reply = {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(reply).encode())):
            with self.assertRaisesRegex(ValueError, "budget"):
                writer.openrouter_text({"model": "selected", "api_key": "test"}, [])


if __name__ == "__main__": unittest.main()
