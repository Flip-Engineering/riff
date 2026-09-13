"""The updater may transfer only archives it downloaded into its own staging area."""
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
from test_desktop_updates import Download, payload_fixture


class DesktopArchiveOwnershipTests(unittest.TestCase):
    def setUp(self):
        keeper = os.environ.get("RIFF_ARCHIVE_TEST_FIXTURES")
        self.root = Path(tempfile.mkdtemp(prefix="archive-ownership-", dir=keeper)).resolve()
        if not keeper:
            self.addCleanup(install.shutil.rmtree, self.root)
        self.payload, self.archive, self.release = payload_fixture(self.root)
        self.original_bytes = self.archive.read_bytes()
        self.original_inode = self.archive.stat().st_ino
        self.install_root = self.root / "installation"
        self.install_root.mkdir()
        self.sentinels = {
            "current.json": b'{"version":"0.6.0","previous":"0.5.3"}',
            "runtime.json": b'{"runtime_id":"previous-runtime"}',
            "workspace/data/library.json": b'{"tracks":["keep this music"]}',
            "workspace/data/engine.json": b'{"binary":"previous-engine"}',
            "workspace/models/model.bin": b"existing verified model",
        }
        for relative, data in self.sentinels.items():
            path = self.install_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.download_identity = None
        self.messages = []

    def tearDown(self):
        for relative, data in self.sentinels.items():
            self.assertEqual((self.install_root / relative).read_bytes(), data)
        self.assertEqual(self.archive.read_bytes(), self.original_bytes)
        self.assertEqual(self.archive.stat().st_ino, self.original_inode)

    @property
    def cache(self):
        return self.install_root / ".desktop-updates" / (self.release["sha256"] + ".payload.tar.gz")

    def progress(self, message):
        self.messages.append(message)
        if message.startswith("Downloading Riff "):
            [download] = list((self.install_root / ".desktop-updates").glob(".preparing-*/payload.tar.gz"))
            stat = download.stat()
            self.download_identity = (stat.st_dev, stat.st_ino)

    def response(self, body=None):
        return Download(self.original_bytes if body is None else body, self.release["url"])

    def validate(self, target):
        install.validate_prepared_desktop(self.install_root, target, self.release)
        self.assertEqual(self.cache.read_bytes(), self.original_bytes)

    def test_owned_download_becomes_cache_without_copying_archive_bytes(self):
        with patch.object(install.urllib.request, "urlopen", return_value=self.response()) as network, \
                patch.object(install.shutil, "copyfile", side_effect=AssertionError("Owned archive was copied")):
            target = install.stage_desktop_release(self.install_root, self.release, self.progress)
        self.assertEqual(network.call_count, 1)
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), self.download_identity)
        self.assertEqual(list(self.cache.parent.glob(".preparing-*")), [])
        self.validate(target)

    def test_caller_supplied_archive_keeps_its_original_file_and_gets_a_separate_cache(self):
        with patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network")):
            target = install.stage_desktop_release(self.install_root, self.release, archive=self.archive)
        self.assertNotEqual(self.cache.stat().st_ino, self.original_inode)
        self.validate(target)

    def test_verified_existing_archive_is_reused_without_network_or_another_copy(self):
        self.cache.parent.mkdir()
        self.cache.write_bytes(self.original_bytes)
        identity = (self.cache.stat().st_dev, self.cache.stat().st_ino)
        with patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network")), \
                patch.object(install.shutil, "copyfile", side_effect=AssertionError("Cached archive was copied")):
            target = install.stage_desktop_release(self.install_root, self.release)
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), identity)
        self.validate(target)

    def test_corrupt_cached_bytes_are_preserved_before_a_new_owned_download(self):
        self.cache.parent.mkdir()
        self.cache.write_bytes(b"retain the unverified old archive")
        with patch.object(install.urllib.request, "urlopen", return_value=self.response()):
            target = install.stage_desktop_release(self.install_root, self.release, self.progress)
        [preserved] = list(self.cache.parent.glob(self.cache.name + ".unverified-*"))
        self.assertEqual(preserved.read_bytes(), b"retain the unverified old archive")
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), self.download_identity)
        self.validate(target)

    def test_size_and_checksum_failures_never_publish_the_download(self):
        for body, changes, error in (
                (self.original_bytes[:-1], {}, "incomplete"),
                (self.original_bytes + b"extra", {}, "larger"),
                (self.original_bytes, {"sha256": "0" * 64}, "checksum")):
            with self.subTest(error=error), \
                    patch.object(install.urllib.request, "urlopen", return_value=self.response(body)):
                with self.assertRaisesRegex(ValueError, error):
                    install.stage_desktop_release(self.install_root, {**self.release, **changes})
            self.assertEqual(list((self.install_root / ".desktop-updates").iterdir()), [])

    def test_cancellation_after_payload_verification_still_precedes_archive_promotion(self):
        stopped = threading.Event()
        original = install.validate_desktop_payload

        def checked(*args, **kwargs):
            result = original(*args, **kwargs)
            stopped.set()
            return result

        with patch.object(install.urllib.request, "urlopen", return_value=self.response()), \
                patch.object(install, "validate_desktop_payload", side_effect=checked):
            with self.assertRaises(install.UpdateCancelled):
                install.stage_desktop_release(self.install_root, self.release, cancelled=stopped.is_set)
        self.assertEqual(list(self.cache.parent.iterdir()), [])

    def test_failed_owned_archive_promotion_does_not_fall_back_to_a_large_copy(self):
        original = Path.replace

        def fail_commit(path, target):
            if path.name == "payload.tar.gz" and Path(target) == self.cache:
                raise OSError(errno.ENOSPC, "simulated cache metadata failure")
            return original(path, target)

        with patch.object(install.urllib.request, "urlopen", return_value=self.response()), \
                patch.object(Path, "replace", fail_commit), \
                patch.object(install.shutil, "copyfile", side_effect=AssertionError("Unexpected copy fallback")):
            with self.assertRaisesRegex(OSError, "simulated cache metadata failure"):
                install.stage_desktop_release(self.install_root, self.release)
        self.assertEqual(list(self.cache.parent.iterdir()), [])

    def test_failure_after_cache_promotion_leaves_verified_bytes_reusable(self):
        original = Path.rename

        def fail_publish(path, target):
            if path.name == "payload" and path.parent.name.startswith(".preparing-"):
                raise OSError(errno.EINTR, "simulated payload publication failure")
            return original(path, target)

        with patch.object(install.urllib.request, "urlopen", return_value=self.response()), \
                patch.object(Path, "rename", fail_publish):
            with self.assertRaisesRegex(OSError, "simulated payload publication failure"):
                install.stage_desktop_release(self.install_root, self.release, self.progress)
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), self.download_identity)
        self.assertEqual(self.cache.read_bytes(), self.original_bytes)
        with patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Retry downloaded again")):
            target = install.stage_desktop_release(self.install_root, self.release)
        self.validate(target)

    def test_process_exit_after_cache_promotion_keeps_archive_and_preserves_orphan_for_retry(self):
        descriptor = self.root / "release.json"
        descriptor.write_text(json.dumps(self.release))
        code = r'''
import importlib.util, io, json, os, pathlib, sys
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("installer_fixture", sys.argv[1])
install = importlib.util.module_from_spec(spec); spec.loader.exec_module(install)
root, archive, descriptor = map(pathlib.Path, sys.argv[2:])
release = json.loads(descriptor.read_text())
class Response(io.BytesIO):
    def geturl(self): return release["url"]
original = pathlib.Path.rename
def stop_after_cache(path, target):
    if path.name == "payload" and path.parent.name.startswith(".preparing-"):
        cache = root / ".desktop-updates" / (release["sha256"] + ".payload.tar.gz")
        assert cache.read_bytes() == archive.read_bytes()
        os._exit(73)
    return original(path, target)
with patch.object(install.urllib.request, "urlopen", return_value=Response(archive.read_bytes())), patch.object(pathlib.Path, "rename", stop_after_cache):
    install.stage_desktop_release(root, release, lambda message: None)
'''
        result = subprocess.run([sys.executable, "-B", "-c", code, str(Path(install.__file__).resolve()),
                                 str(self.install_root), str(self.archive), str(descriptor)],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 73, result.stderr)
        [orphan] = list(self.cache.parent.glob(".preparing-*/payload"))
        before = {path.relative_to(orphan).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in orphan.rglob("*") if path.is_file()}
        identity = (self.cache.stat().st_dev, self.cache.stat().st_ino)
        with patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Retry downloaded again")), \
                patch.object(install.shutil, "copyfile", side_effect=AssertionError("Cached archive copied")):
            target = install.stage_desktop_release(self.install_root, self.release)
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), identity)
        self.assertEqual({path.relative_to(orphan).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in orphan.rglob("*") if path.is_file()}, before)
        self.validate(target)

    def existing_target(self):
        self.cache.parent.mkdir()
        target = self.cache.parent / (self.release["version"] + "-" + self.release["sha256"])
        install.shutil.copytree(self.payload, target)
        return target, self.target_snapshot(target)

    def target_snapshot(self, target):
        return {path.relative_to(target).as_posix():
                (path.stat().st_dev, path.stat().st_ino, path.stat().st_mode,
                 path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in target.rglob("*") if path.is_file()}

    def assert_target_kept(self, target, snapshot):
        self.assertEqual(self.target_snapshot(target), snapshot)
        self.assertEqual(list(target.parent.glob(target.name + ".unverified-*")), [])

    def test_existing_target_repairs_missing_and_invalid_cache_from_owned_download(self):
        target, snapshot = self.existing_target()
        for invalid in (False, True):
            if invalid:
                self.cache.write_bytes(b"retain invalid downloaded-cache bytes")
            with self.subTest(invalid_cache=invalid), \
                    patch.object(install.urllib.request, "urlopen", return_value=self.response()), \
                    patch.object(install.shutil, "copyfile", side_effect=AssertionError("Owned archive copied")), \
                    patch.object(install, "extract_desktop_archive", side_effect=AssertionError("Valid target unpacked again")):
                result = install.stage_desktop_release(self.install_root, self.release, self.progress)
            self.assertEqual(result, target)
            self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), self.download_identity)
            self.validate(target)
            self.assert_target_kept(target, snapshot)
        [preserved] = list(self.cache.parent.glob(self.cache.name + ".unverified-*"))
        self.assertEqual(preserved.read_bytes(), b"retain invalid downloaded-cache bytes")

    def test_existing_target_repairs_missing_and_invalid_cache_from_caller_archive(self):
        target, snapshot = self.existing_target()
        for invalid in (False, True):
            if invalid:
                self.cache.write_bytes(b"retain invalid caller-cache bytes")
            with self.subTest(invalid_cache=invalid), \
                    patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network")), \
                    patch.object(install, "extract_desktop_archive", side_effect=AssertionError("Valid target unpacked again")):
                result = install.stage_desktop_release(self.install_root, self.release, archive=self.archive)
            self.assertEqual(result, target)
            self.assertNotEqual(self.cache.stat().st_ino, self.original_inode)
            self.validate(target)
            self.assert_target_kept(target, snapshot)
        [preserved] = list(self.cache.parent.glob(self.cache.name + ".unverified-*"))
        self.assertEqual(preserved.read_bytes(), b"retain invalid caller-cache bytes")

    def test_existing_target_and_verified_cache_need_neither_copy_nor_republication(self):
        target, snapshot = self.existing_target()
        self.cache.write_bytes(self.original_bytes)
        identity = (self.cache.stat().st_dev, self.cache.stat().st_ino)
        for supplied in (None, self.archive, self.cache):
            with self.subTest(supplied=str(supplied)), \
                    patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network")), \
                    patch.object(install.shutil, "copyfile", side_effect=AssertionError("Verified archive copied")), \
                    patch.object(install, "extract_desktop_archive", side_effect=AssertionError("Valid target unpacked again")), \
                    patch.object(Path, "replace", side_effect=AssertionError("Verified archive republished")):
                result = install.stage_desktop_release(self.install_root, self.release, archive=supplied)
            self.assertEqual(result, target)
            self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), identity)
            self.validate(target)
            self.assert_target_kept(target, snapshot)

    def test_existing_target_cancellation_keeps_invalid_cache_in_place_before_publication(self):
        target, snapshot = self.existing_target()
        self.cache.write_bytes(b"invalid cache retained on cancellation")
        identity = (self.cache.stat().st_dev, self.cache.stat().st_ino)
        original = install.validate_desktop_payload
        for supplied in (None, self.archive):
            stopped = threading.Event()

            def checked(*args, **kwargs):
                result = original(*args, **kwargs)
                stopped.set()
                return result

            with self.subTest(supplied=bool(supplied)), \
                    patch.object(install.urllib.request, "urlopen", return_value=self.response()), \
                    patch.object(install, "validate_desktop_payload", side_effect=checked):
                with self.assertRaises(install.UpdateCancelled):
                    install.stage_desktop_release(self.install_root, self.release, archive=supplied, cancelled=stopped.is_set)
            self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), identity)
            self.assertEqual(self.cache.read_bytes(), b"invalid cache retained on cancellation")
            self.assertEqual(list(self.cache.parent.glob(self.cache.name + ".unverified-*")), [])
            self.assert_target_kept(target, snapshot)

    def test_existing_target_survives_owned_and_caller_archive_publication_failure(self):
        target, snapshot = self.existing_target()
        original = Path.replace

        def fail_commit(path, destination):
            if Path(destination) == self.cache:
                raise OSError(errno.ENOSPC, "simulated cache publication failure")
            return original(path, destination)

        for supplied in (None, self.archive):
            with self.subTest(supplied=bool(supplied)), \
                    patch.object(install.urllib.request, "urlopen", return_value=self.response()), \
                    patch.object(Path, "replace", fail_commit):
                with self.assertRaisesRegex(OSError, "simulated cache publication failure"):
                    install.stage_desktop_release(self.install_root, self.release, archive=supplied)
            self.assertFalse(self.cache.exists())
            self.assertEqual(list(self.cache.parent.glob(".preparing-*")), [])
            self.assert_target_kept(target, snapshot)

    def test_invalid_caller_archive_at_the_cache_path_is_not_moved_or_replaced(self):
        target, snapshot = self.existing_target()
        self.cache.write_bytes(b"invalid caller archive")
        identity = (self.cache.stat().st_dev, self.cache.stat().st_ino)
        with patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network")):
            with self.assertRaises(ValueError):
                install.stage_desktop_release(self.install_root, self.release, archive=self.cache)
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), identity)
        self.assertEqual(self.cache.read_bytes(), b"invalid caller archive")
        self.assertEqual(list(self.cache.parent.glob(self.cache.name + ".unverified-*")), [])
        self.assert_target_kept(target, snapshot)

    def test_cancellation_after_copying_caller_archive_precedes_invalid_cache_replacement(self):
        target, snapshot = self.existing_target()
        self.cache.write_bytes(b"invalid cache retained after caller copy")
        identity = (self.cache.stat().st_dev, self.cache.stat().st_ino)
        stopped = threading.Event()
        original = install.shutil.copyfile

        def copied(*args, **kwargs):
            result = original(*args, **kwargs)
            stopped.set()
            return result

        with patch.object(install.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network")), \
                patch.object(install.shutil, "copyfile", side_effect=copied):
            with self.assertRaises(install.UpdateCancelled):
                install.stage_desktop_release(self.install_root, self.release, archive=self.archive, cancelled=stopped.is_set)
        self.assertEqual((self.cache.stat().st_dev, self.cache.stat().st_ino), identity)
        self.assertEqual(self.cache.read_bytes(), b"invalid cache retained after caller copy")
        self.assertEqual(list(self.cache.parent.glob(self.cache.name + ".unverified-*")), [])
        self.assert_target_kept(target, snapshot)


if __name__ == "__main__":
    unittest.main()
