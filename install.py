#!/usr/bin/env python3
"""Install Riff from a verified Flip-Engineering GitHub release. Python 3.9+."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

REPOSITORY = "Flip-Engineering/riff"
API = "https://api.github.com/repos/" + REPOSITORY
VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def version_tuple(value):
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("The release has an unsupported version number.")
    return tuple(map(int, match.groups()))


def default_install_root():
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/Riff"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "riff"


def get_release():
    request = urllib.request.Request(API + "/releases/latest", headers={"Accept": "application/vnd.github+json", "User-Agent": "Riff updater"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("No stable Riff release is available yet.") from None
        raise ValueError(f"GitHub could not check releases (HTTP {exc.code}). Try again later.") from None
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("This is not a stable release.")
    tag = release.get("tag_name", "")
    version_tuple(tag)
    name = f"riff-{tag}.tar.gz"
    asset = next((item for item in release.get("assets", []) if item.get("name") == name), None)
    if not asset:
        raise ValueError("This release does not include the Riff installer archive.")
    digest = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("GitHub has not supplied a SHA-256 digest for this release.")
    url = asset.get("browser_download_url", "")
    if url != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}":
        raise ValueError("The update archive is outside the Riff release repository.")
    return {"version": tag.lstrip("v"), "tag": tag, "name": name, "url": url,
            "sha256": digest.split(":", 1)[1], "bytes": asset["size"],
            "notes": release.get("body", ""), "page": f"https://github.com/{REPOSITORY}/releases/tag/{tag}"}


def verify(path, expected):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("The archive checksum does not match the release. Download it again.")


def extract_archive(archive, destination):
    """Only ordinary files/directories; no links, traversal, or special entries."""
    destination = Path(destination)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name or not (member.isfile() or member.isdir()):
                raise ValueError("The update archive contains an unsafe path or file type.")
        if sum(member.size for member in members) > shutil.disk_usage(destination.parent).free:
            raise ValueError("There is not enough disk space to unpack this update.")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    if not all((destination / name).is_file() for name in ("studio.py", "VERSION", "sources.json", "launcher.py")):
        raise ValueError("The release archive is incomplete.")


def stage_release(install_root, release, progress=print, archive=None):
    version_tuple(release["version"])
    if release["version"].startswith("v"):
        raise ValueError("Use the numeric release version when staging an update.")
    install_root = Path(install_root)
    versions = install_root / "releases"
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / release["version"]
    if target.exists():
        receipt = target / ".release.json"
        if receipt.exists() and json.loads(receipt.read_text()).get("sha256") == release["sha256"]:
            return target
        raise ValueError("A different build already exists at this version. Keep it and use a new release version.")
    with tempfile.TemporaryDirectory(prefix=".install-", dir=install_root) as temporary:
        temporary = Path(temporary)
        package = Path(archive) if archive else temporary / release["name"]
        if not archive:
            request = urllib.request.Request(release["url"], headers={"User-Agent": "Riff installer"})
            received = 0
            with urllib.request.urlopen(request, timeout=60) as response, package.open("wb") as output:
                if not response.geturl().startswith("https://"):
                    raise ValueError("The release download did not use HTTPS.")
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(block)
                    received += len(block)
                    progress(f"Downloading Riff {release['version']}: {received / 1e6:.1f} MB")
            if received != release["bytes"]:
                raise ValueError("The release download was incomplete.")
        verify(package, release["sha256"])
        extracted = temporary / "app"
        extract_archive(package, extracted)
        if (extracted / "VERSION").read_text().strip() != release["version"]:
            raise ValueError("The archive version does not match the release.")
        # Syntax/import checks happen before any installation pointer changes.
        progress("Checking the new app")
        subprocess.run([sys.executable, str(extracted / "studio.py"), "--check"], check=True,
                       env={**os.environ, "RIFF_HOME": str(install_root / "workspace")}, capture_output=True)
        (extracted / ".release.json").write_text(json.dumps(release, indent=2) + "\n")
        extracted.rename(target)
    return target


def activate(install_root, target):
    install_root, target = Path(install_root).resolve(), Path(target).resolve()
    if target.parent != install_root / "releases" or not (target / "studio.py").is_file():
        raise ValueError("Choose an installed Riff release.")
    version_tuple(target.name)
    pointer = install_root / "current.json"
    previous = json.loads(pointer.read_text()).get("version") if pointer.exists() else None
    if previous == target.name:
        return
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": target.name, "previous": previous}) + "\n")
    temporary.replace(pointer)


def create_launchers(install_root, target):
    install_root = Path(install_root).resolve()
    shutil.copy2(Path(target) / "launcher.py", install_root / "launcher.py")
    import shlex
    content = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(install_root / "launcher.py")) + ' "$@"\n'
    launcher = install_root / "Riff.command"
    launcher.write_text(content)
    launcher.chmod(0o755)
    if platform.system() == "Darwin":
        applications = Path.home() / "Applications"
        applications.mkdir(exist_ok=True)
        shutil.copy2(launcher, applications / "Riff.command")
    elif platform.system() == "Linux":
        applications = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "applications"
        applications.mkdir(parents=True, exist_ok=True)
        # Desktop Entry Exec uses double-quote escaping, not shell quoting.
        executable = str(launcher).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
        (applications / "riff.desktop").write_text('[Desktop Entry]\nType=Application\nName=Riff\nComment=Generative music studio\nExec="' + executable + '"\nTerminal=false\nCategories=AudioVideo;Audio;\n')
    return launcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_install_root())
    parser.add_argument("--archive", type=Path, help="Install a downloaded release archive")
    parser.add_argument("--sha256", help="SHA-256 for a downloaded archive")
    parser.add_argument("--version", help="Version of a downloaded archive")
    parser.add_argument("--no-open", action="store_true", help="Install without starting Riff")
    args = parser.parse_args()
    if args.archive:
        if not args.sha256 or not re.fullmatch(r"[a-f0-9]{64}", args.sha256) or not args.version:
            parser.error("--archive requires --sha256 and --version")
        version_tuple(args.version)
        release = {"version": args.version.lstrip("v"), "name": args.archive.name, "sha256": args.sha256}
    else:
        release = get_release()
    target = stage_release(args.root, release, archive=args.archive)
    activate(args.root, target)
    launcher = create_launchers(args.root, target)
    print(f"Riff is installed. Open {launcher}")
    if not args.no_open:
        subprocess.Popen([sys.executable, str(args.root / "launcher.py")], start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
