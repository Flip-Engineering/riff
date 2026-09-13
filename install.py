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
import signal
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid

REPOSITORY = "Flip-Engineering/riff"
API = "https://api.github.com/repos/" + REPOSITORY
VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
DESKTOP_PROTOCOL = 1


class UpdateCancelled(ValueError):
    pass


def check_cancelled(cancelled):
    if cancelled and cancelled():
        raise UpdateCancelled("Update preparation was stopped. Your current Riff is unchanged.")


def version_tuple(value):
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("The release has an unsupported version number.")
    return tuple(map(int, match.groups()))


def default_install_root():
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/Riff"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "riff"


def get_release(desktop_platform=None):
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
    if desktop_platform and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9_]+)+", desktop_platform):
        raise ValueError("The installed desktop platform is invalid. Reopen Riff Setup.")
    name = f"riff-{tag}-{desktop_platform}.payload.tar.gz" if desktop_platform else f"riff-{tag}.tar.gz"
    assets = [item for item in release.get("assets", []) if item.get("name") == name]
    if len(assets) > 1:
        raise ValueError("The release contains conflicting update archives. Try again later.")
    asset = assets[0] if assets else None
    if not asset:
        if desktop_platform:
            raise ValueError("A compatible desktop update is not available yet. Your current Riff is unchanged; check again later.")
        raise ValueError("This release does not include the Riff installer archive.")
    digest = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("GitHub has not supplied a SHA-256 digest for this release.")
    url = asset.get("browser_download_url", "")
    if url != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}":
        raise ValueError("The update archive is outside the Riff release repository.")
    if type(asset.get("size")) is not int or asset["size"] <= 0:
        raise ValueError("The release archive does not have a valid size.")
    result = {"version": tag.lstrip("v"), "tag": tag, "name": name, "url": url,
            "sha256": digest.split(":", 1)[1], "bytes": asset["size"],
            "notes": release.get("body", ""), "page": f"https://github.com/{REPOSITORY}/releases/tag/{tag}"}
    if desktop_platform:
        result.update(kind="desktop", platform=desktop_platform)
    return result


def verify(path, expected, cancelled=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check_cancelled(cancelled)
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


def _relative_file(value):
    if not isinstance(value, str) or not value or any(part in ("", ".", "..") for part in value.split("/")) or any(char in value for char in "\\\0:"):
        raise ValueError("The desktop package contains an unsafe path.")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("The desktop package contains an unsafe path.")
    return path


def _owned_path(root, relative, directory=False):
    root = Path(root).resolve()
    parts = _relative_file(relative).parts
    current = root
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink() or (index < len(parts) - 1 and not current.is_dir()):
            raise ValueError("An installed desktop component is missing or linked. Reopen Riff Setup.")
    if not (current.is_dir() if directory else current.is_file()):
        raise ValueError("An installed desktop component is missing. Reopen Riff Setup.")
    return current


def desktop_runtime(install_root):
    """A desktop receipt is authoritative; an invalid one never enables source builds."""
    if not install_root:
        return None
    root = Path(install_root).resolve()
    receipt = root / "runtime.json"
    if not receipt.exists() and not receipt.is_symlink():
        return None
    value = json.loads(_owned_path(root, "runtime.json").read_text())
    if not isinstance(value, dict) or value.get("format_version") != DESKTOP_PROTOCOL or not re.fullmatch(r"[a-f0-9]{24}", value.get("runtime_id", "")):
        raise ValueError("The installed desktop runtime needs repair. Reopen Riff Setup.")
    runtime = _owned_path(root, "runtimes/" + value["runtime_id"], directory=True)
    manifest = json.loads(_owned_path(runtime, ".payload-manifest.json").read_text())
    platform_name = manifest.get("platform", "")
    if manifest.get("runtime_id") != value["runtime_id"] or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9_]+)+", platform_name):
        raise ValueError("The installed desktop runtime identity is inconsistent. Reopen Riff Setup.")
    return {"root": runtime, "platform": platform_name,
            "control": _owned_path(runtime, value.get("control")),
            "media": _owned_path(runtime, value.get("media"), directory=True),
            "lock_helper": _owned_path(runtime, value.get("lock_helper"))}


def external_installer_handoff(runtime, install_root, pending):
    if pending.get("kind") != "external-installer" or not isinstance(pending.get("installer_id"), str) or not pending["installer_id"]:
        return False
    root = Path(install_root).resolve()
    gate = json.loads(_owned_path(root, ".installer-activation.json").read_text())
    if any(gate.get(key) != pending.get(key) for key in ("kind", "installer_id", "version")):
        return False
    with subprocess.Popen([str(runtime["lock_helper"]), str(root / ".riff-installer.lock")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as helper:
        output, _ = helper.communicate("release")
        return helper.returncode == 0 and output.splitlines()[:1] == ["BUSY"]


def validate_desktop_payload(payload, release, cancelled=None):
    """Check every extracted byte before asking a bundled executable to use it."""
    payload = Path(payload)
    manifest = json.loads(_owned_path(payload, "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("format_version") != DESKTOP_PROTOCOL or manifest.get("version") != release["version"] or manifest.get("platform") != release["platform"]:
        raise ValueError("The desktop package does not match this release and platform.")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("The desktop package is incomplete.")
    paths = set()
    for record in records:
        check_cancelled(cancelled)
        if not isinstance(record, dict):
            raise ValueError("The desktop package has an invalid file manifest.")
        relative = record.get("path")
        _relative_file(relative)
        if not relative.startswith(("app/", "runtime/")) or relative in paths or type(record.get("bytes")) is not int or record["bytes"] < 0 or type(record.get("executable")) is not bool or not re.fullmatch(r"[a-f0-9]{64}", record.get("sha256", "")):
            raise ValueError("The desktop package has an invalid file manifest.")
        paths.add(relative)
        path = _owned_path(payload, relative)
        if path.stat().st_size != record["bytes"]:
            raise ValueError("A desktop component did not pass verification. Download the update again.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                check_cancelled(cancelled)
                digest.update(block)
        if digest.hexdigest() != record["sha256"] or bool(path.stat().st_mode & 0o111) != record["executable"]:
            raise ValueError("A desktop component did not pass verification. Download the update again.")
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or any(entries.get(key) not in paths for key in ("python", "studio", "launcher", "engine", "ffmpeg", "ffprobe", "control")):
        raise ValueError("The desktop package is missing an entry point.")
    runtime_records = [record for record in records if record["path"].startswith("runtime/")]
    # Build-time Elixir encodes these four-field maps in key order. Keep the
    # same canonical UTF-8 representation in the published format-1 contract.
    encoded = json.dumps(runtime_records, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    runtime_digest = hashlib.sha256(encoded).hexdigest()
    if manifest.get("runtime_sha256") != runtime_digest or manifest.get("runtime_id") != runtime_digest[:24]:
        raise ValueError("The desktop runtime identity did not pass verification.")
    actual = set()
    for path in payload.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("The desktop package contains a linked or special file.")
        if path.is_file():
            actual.add(path.relative_to(payload).as_posix())
    if actual != paths | {"manifest.json"} or _owned_path(payload, "app/VERSION").read_text().strip() != release["version"]:
        raise ValueError("The desktop package contains unexpected or incomplete files.")
    return manifest


def extract_desktop_archive(archive, destination, cancelled=None):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Desktop updates must be unpacked into a fresh folder.")
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        names = set()
        for member in members:
            name = member.name.rstrip("/") if member.isdir() else member.name
            if member.isdir() and name == ".":
                continue
            _relative_file(name)
            if name in names or not (member.isfile() or member.isdir()) or not (name in ("manifest.json", "app", "runtime") or name.startswith(("app/", "runtime/"))):
                raise ValueError("The desktop archive contains an unsafe or repeated file.")
            names.add(name)
        if sum(member.size for member in members) > shutil.disk_usage(destination.parent).free:
            raise ValueError("There is not enough space to prepare this desktop update.")
        destination.mkdir()
        for member in members:
            check_cancelled(cancelled)
            if member.isdir() and member.name.rstrip("/") == ".":
                continue
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("xb") as output:
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        check_cancelled(cancelled)
                        output.write(block)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def _matches_archived_manifest(archive, payload):
    with tarfile.open(archive, "r:gz") as source:
        member = source.getmember("manifest.json")
        return member.isfile() and source.extractfile(member).read() == _owned_path(payload, "manifest.json").read_bytes()


def validate_prepared_desktop(install_root, payload, release):
    """Activation performs verification only; any repair goes through preparation."""
    root = Path(install_root).resolve()
    relative = ".desktop-updates/" + release["version"] + "-" + release["sha256"]
    target = _owned_path(root, relative, directory=True)
    if target != Path(payload).resolve() or Path(payload).is_symlink():
        raise ValueError("The prepared desktop update has an invalid location.")
    archive = _owned_path(root, ".desktop-updates/" + release["sha256"] + ".payload.tar.gz")
    if archive.stat().st_size != release["bytes"]:
        raise ValueError("The prepared desktop archive is incomplete. Prepare the update again.")
    verify(archive, release["sha256"])
    if not _matches_archived_manifest(archive, payload):
        raise ValueError("The prepared desktop manifest changed. Prepare the update again.")
    return validate_desktop_payload(payload, release)


def stage_desktop_release(install_root, release, progress=print, cancelled=None, archive=None):
    version_tuple(release["version"])
    if release["version"].startswith("v") or release.get("kind") != "desktop" or not re.fullmatch(r"[a-f0-9]{64}", release.get("sha256", "")):
        raise ValueError("Choose a verified desktop release.")
    root = Path(install_root).resolve()
    updates = root / ".desktop-updates"
    if updates.is_symlink():
        raise ValueError("The desktop update folder is linked. Reopen Riff Setup.")
    updates.mkdir(parents=True, exist_ok=True)
    target = updates / (release["version"] + "-" + release["sha256"])
    cached_archive = updates / (release["sha256"] + ".payload.tar.gz")
    with tempfile.TemporaryDirectory(prefix=".preparing-", dir=updates) as temporary:
        temporary = Path(temporary)
        package = Path(archive) if archive else temporary / "payload.tar.gz"
        if not archive and (cached_archive.exists() or cached_archive.is_symlink()):
            _owned_path(updates, cached_archive.name)
            try:
                verify(cached_archive, release["sha256"], cancelled)
                if cached_archive.stat().st_size != release["bytes"]:
                    raise ValueError("Incorrect cached archive size")
                package = cached_archive
            except UpdateCancelled:
                raise
            except ValueError:
                cached_archive.rename(cached_archive.with_name(cached_archive.name + ".unverified-" + uuid.uuid4().hex))
        if not archive and package != cached_archive:
            request = urllib.request.Request(release["url"], headers={"User-Agent": "Riff desktop updater"})
            received = 0
            with urllib.request.urlopen(request, timeout=60) as response, package.open("xb") as output:
                if not response.geturl().startswith("https://"):
                    raise ValueError("The desktop update download did not use HTTPS.")
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    check_cancelled(cancelled)
                    received += len(block)
                    if received > release["bytes"]:
                        raise ValueError("The desktop update is larger than the verified release.")
                    output.write(block)
                    progress(f"Downloading Riff {release['version']}: {received / 1e6:.1f} MB")
            if received != release["bytes"]:
                raise ValueError("The desktop update download was incomplete. Try again.")
        check_cancelled(cancelled)
        if package.stat().st_size != release["bytes"]:
            raise ValueError("The desktop update size does not match the release.")
        verify(package, release["sha256"], cancelled)
        progress("Checking the desktop update")
        if target.exists() or target.is_symlink():
            _owned_path(updates, target.name, directory=True)
            try:
                # A mutable cache manifest is not its own authority. Bind it
                # back to the archived bytes covered by GitHub's release hash.
                if not _matches_archived_manifest(package, target):
                    raise ValueError("Cached desktop manifest differs from the release")
                validate_desktop_payload(target, release, cancelled)
                return target
            except UpdateCancelled:
                raise
            except (ValueError, KeyError):
                target.rename(target.with_name(target.name + ".unverified-" + uuid.uuid4().hex))
        extracted = temporary / "payload"
        extract_desktop_archive(package, extracted, cancelled)
        validate_desktop_payload(extracted, release, cancelled)
        check_cancelled(cancelled)
        if package != cached_archive:
            saved = temporary / "verified-archive.tar.gz"
            shutil.copyfile(package, saved)
            saved.replace(cached_archive)
        extracted.rename(target)
    return target


def stop_desktop_control(process):
    """Only the updater's newly created process group is eligible for stopping."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


def desktop_control(runtime, action, install_root, payload, progress=print, cancelled=None, process_changed=None, log=None):
    if action not in ("prepare", "activate"):
        raise ValueError("Unknown desktop update operation.")
    check_cancelled(cancelled)
    request = {"protocol": DESKTOP_PROTOCOL, "action": action, "payload": str(Path(payload).resolve()), "root": str(Path(install_root).resolve())}
    environment = {**os.environ, "RIFF_INSTALLER_OPEN_BROWSER": "false", "RELEASE_DISTRIBUTION": "none",
                   "PATH": str(runtime["media"]) + ":/usr/bin:/bin:/usr/sbin:/sbin"}
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    result = None
    process = subprocess.Popen([str(runtime["control"]), "eval", "Riff.Installer.Update.main()"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", env=environment, start_new_session=True)
    try:
        if process_changed:
            process_changed(process)
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.close()
        for line in process.stdout:
            if log:
                log.write(line)
                log.flush()
            check_cancelled(cancelled)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("protocol") != DESKTOP_PROTOCOL:
                continue
            if event.get("type") == "progress":
                if isinstance(event.get("message"), str):
                    progress(event["message"])
                else:
                    stage = {"checking": "Checking the update", "hashing": "Checking saved downloads",
                             "downloading": "Downloading music models", "verified": "Checking music models",
                             "installing": "Preparing the updated studio", "starting": "Opening the updated studio"}.get(event.get("stage"))
                    if stage:
                        done, total = event.get("downloaded_bytes"), event.get("total_bytes")
                        suffix = f": {done / 1e6:.1f} of {total / 1e6:.1f} MB" if type(done) is int and type(total) is int and total > 0 else ""
                        progress(stage + suffix)
            elif event.get("type") == "result":
                if result is not None:
                    raise ValueError("The desktop installer returned conflicting results.")
                result = event
        code = process.wait()
        check_cancelled(cancelled)
        expected = "prepared" if action == "prepare" else "activated"
        if code or not result or result.get("status") != expected:
            message = result.get("message") if isinstance(result, dict) else None
            raise ValueError(message if isinstance(message, str) and message else "Desktop setup could not finish. Your current Riff is kept; try again.")
        version = result.get("version", "")
        version_tuple(version)
        expected_path = Path(install_root).resolve() / "releases" / version
        if Path(result.get("path", "")).resolve() != expected_path or not re.fullmatch(r"[a-f0-9]{24}", result.get("runtime_id", "")):
            raise ValueError("The desktop installer returned an inconsistent installation receipt.")
        return result
    except (OSError, ValueError):
        check_cancelled(cancelled)
        raise
    finally:
        stop_desktop_control(process)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        if process_changed:
            process_changed(None)


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
