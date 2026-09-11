#!/usr/bin/env python3
"""Exercise a release install, real HTTP startup, and fallback without model downloads."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import install


def check(archive):
    archive = Path(archive).resolve()
    version = (ROOT / "VERSION").read_text().strip()
    release = {"version": version, "name": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix="riff-install-check-") as directory:
        root = Path(directory)
        target = install.stage_release(root, release, archive=archive)
        install.activate(root, target)
        shutil.copyfile(target / "launcher.py", root / "launcher.py")
        library = root / "workspace/data"; library.mkdir(parents=True)
        sentinel = library / "keep.txt"; sentinel.write_text("existing library")
        # A broken candidate must restore the working release before opening it.
        candidate = root / "releases/999.0.0"; candidate.mkdir()
        (candidate / "studio.py").write_text("raise RuntimeError('incomplete candidate')\n")
        install.activate(root, candidate)
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0)); port = address.getsockname()[1]
        log_path = root / "startup.log"
        with log_path.open("w") as log:
            child = subprocess.Popen([sys.executable, str(root / "launcher.py"), "--no-open", "--port", str(port)], stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while True:
                    if child.poll() is not None: raise RuntimeError(log_path.read_text())
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response: health = json.load(response)
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() > deadline: raise RuntimeError(log_path.read_text())
                        time.sleep(.1)
                assert health["app"] == "Riff", health
                assert json.loads((root / "current.json").read_text())["version"] == version
                assert sentinel.read_text() == "existing library"
                print("PASS verified archive, installed HTTP startup, fallback, persistent workspace")
            finally:
                child.terminate(); child.wait(timeout=30)


if __name__ == "__main__": check(sys.argv[1])
