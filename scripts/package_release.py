#!/usr/bin/env python3
"""Build a curated, reproducible application archive without workspace data."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from install import version_tuple

ROOT_FILES = '''.gitattributes .gitignore VERSION LICENSE NOTICE.md README.md DESIGN.md CONTRIBUTING.md
Riff.command build.sh fetch-models.py inspiration.py install.py keychain.py launcher.py
maintenance.py model_options.py paths.py platform_support.py review_client.py reviews.py
run.py setup_engine.py setup-writer.sh sources.json studio.py studio_core.py symbolic.py
writer.py lyrics.txt requirements-writer.lock package.json package-lock.json design-seed.txt'''.split()
PUBLIC_DOCS = '''adapters.md adapter-research.json install.md capabilities.md branding.md validation.md
studio.png composition.png studio-mobile.png'''.split()


def public_files():
    files = [ROOT / name for name in ROOT_FILES] + [ROOT / "docs" / name for name in PUBLIC_DOCS]
    for directory in ("web", "patches", "scripts", "tests", ".github", "site"):
        files += [path for path in (ROOT / directory).rglob("*") if path.is_file()
                  and "__pycache__" not in path.parts and path.name != ".DS_Store"]
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Expected a regular release file: {path.relative_to(ROOT)}")
    return sorted(set(files))


def build():
    version = (ROOT / "VERSION").read_text().strip(); version_tuple(version)
    if json.loads((ROOT / "package.json").read_text())["version"] != version:
        raise ValueError("VERSION and package.json disagree")
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or subprocess.check_output(["git", "log", "-1", "--format=%ct"], cwd=ROOT))
    destination = ROOT / "dist"; destination.mkdir(exist_ok=True)
    archive = destination / f"riff-v{version}.tar.gz"
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=epoch) as compressed, tarfile.open(fileobj=compressed, mode="w") as tar:
        for path in public_files():
            item = tar.gettarinfo(str(path), arcname=path.relative_to(ROOT).as_posix())
            item.mtime, item.uid, item.gid, item.uname, item.gname = epoch, 0, 0, "", ""
            item.mode = 0o755 if os.access(path, os.X_OK) else 0o644
            with path.open("rb") as stream: tar.addfile(item, stream)
    installer = destination / "install.py"; shutil.copyfile(ROOT / "install.py", installer)
    lines = [hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name for path in (archive, installer)]
    (destination / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print(archive)
    return archive


if __name__ == "__main__": build()
