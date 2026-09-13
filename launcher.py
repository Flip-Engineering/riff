#!/usr/bin/env python3
"""Stable managed-install launcher; keep data outside versioned application files."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time


def configured_runtime(root):
    """Resolve only files inside the installer-owned, immutable runtime set."""
    receipt = root / "runtime.json"
    if receipt.is_symlink():
        raise ValueError("The installed runtime receipt must be a regular file.")
    if not receipt.exists():
        return None
    value = json.loads(receipt.read_text())
    return resolve_runtime(root, value)


def resolve_runtime(root, value):
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("format_version") != 1 or
            not isinstance(value.get("runtime_id"), str) or
            not re.fullmatch(r"[a-f0-9]{24}", value["runtime_id"])):
        raise ValueError("The installed runtime receipt is invalid. Reopen the Riff installer.")
    runtime_root = owned_path(root, "runtimes/" + value["runtime_id"], directory=True)
    resolved = {}
    for key in ("python", "media", "control", "lock_helper"):
        resolved[key] = owned_path(runtime_root, value.get(key), directory=key == "media")
    supplied_environment = value.get("environment", {})
    allowed = {"SSL_CERT_FILE": "/etc/ssl/cert.pem", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if not isinstance(supplied_environment, dict) or any(allowed.get(key) != item for key, item in supplied_environment.items()):
        raise ValueError("The installed runtime environment is invalid.")
    resolved["environment"] = {key: supplied_environment[key] for key in
                               ("SSL_CERT_FILE", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE")
                               if key in supplied_environment}
    if value.get("writer_model") is not None:
        model = value["writer_model"]
        if not isinstance(model, str) or not model.startswith("workspace/models/"):
            raise ValueError("The installed writer model path is invalid.")
        resolved["writer_model"] = owned_path(root, model, directory=True)
    return resolved


def owned_path(root, relative, *, directory=False):
    """Reject symlinks throughout an installer-owned path, including directories."""
    if not isinstance(relative, str) or not relative or "\0" in relative:
        raise ValueError("The installed runtime contains an invalid path.")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts) or Path(relative).is_absolute():
        raise ValueError("The installed runtime contains an invalid path.")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("The installed runtime contains a linked path.")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("The installed runtime is incomplete. Reopen the Riff installer.")
    return path.resolve()


def wait_for_installer(root, runtime):
    """Keep launchd restarts behind the install transaction, with crash recovery."""
    gate = root / ".installer-activation.json"
    while gate.exists():
        if not runtime:
            raise ValueError("The installer runtime is missing. Reopen the Riff installer.")
        # The OS releases this advisory lock even if the installer crashes. Hold
        # it while removing an abandoned gate so a new install cannot race us.
        with subprocess.Popen([str(runtime["lock_helper"]), str(root / ".riff-installer.lock")],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as helper:
            response = helper.stdout.readline().strip()
            if response == b"LOCKED":
                try:
                    recover_activation(root)
                finally:
                    helper.communicate(b"release")
                return
            helper.communicate()
            if response != b"BUSY":
                raise RuntimeError("Riff could not check whether setup has finished.")
        time.sleep(0.25)


def recover_activation(root):
    """Called only while the launcher owns the same OS lock as the installer."""
    gate = root / ".installer-activation.json"
    if not gate.exists():
        return
    if gate.is_symlink():
        raise ValueError("The update recovery journal is not a regular file.")
    journal = json.loads(gate.read_text())
    transaction = journal.get("transaction") if isinstance(journal, dict) else None
    if transaction is not None:
        if not isinstance(transaction, dict) or transaction.get("protocol") != 1:
            raise ValueError("The interrupted update has an unsupported recovery journal.")
        if transaction.get("phase") == "prepared":
            identity, backups = transaction.get("id"), transaction.get("backups")
            launch_names = {"runtime.json", "launcher.py", "current.json"}
            full_names = launch_names | {"workspace/data/engine.json", "workspace/data/engine-activations.json"}
            if (not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identity) or
                    not isinstance(backups, dict) or set(backups) not in (launch_names, full_names)):
                raise ValueError("The interrupted update has an invalid recovery journal.")
            for name in sorted(backups):
                prepare_owned_parent(root, name)
                target = root / name
                if target.is_symlink():
                    raise ValueError("A launch setting is linked and cannot be recovered.")
                relative = backups[name]
                if relative is None:
                    target.unlink(missing_ok=True)
                    continue
                if relative != f".activation/{identity}/{name}":
                    raise ValueError("The update backup is outside its recovery journal.")
                source = owned_path(root, relative)
                temporary = root / (name + ".recovering-" + str(time.time_ns()))
                with temporary.open("xb") as output:
                    output.write(source.read_bytes())
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(target)
                sync_directory(target.parent)
        elif transaction.get("phase") != "committed":
            raise ValueError("The interrupted update has an unsupported recovery phase.")
    sync_directory(root)
    gate.unlink(missing_ok=True)
    sync_directory(root)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_ancestors(path, root):
    path.relative_to(root)
    while True:
        sync_directory(path)
        if path == root:
            return
        path = path.parent


def prepare_owned_parent(root, relative):
    directory = root
    for part in Path(relative).parts[:-1]:
        directory = directory / part
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("An update recovery folder is not an owned directory.")
        directory.mkdir(exist_ok=True)


def write_json_atomic(path, value):
    temporary = path.with_name(path.name + "." + secrets.token_urlsafe(12) + ".tmp")
    with temporary.open("x") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    sync_directory(path.parent)


def runtime_environment(root, runtime):
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "RIFF_RUNTIME_CONTROL", "RIFF_WRITER_PYTHON", "RIFF_WRITER_MODEL"):
        environment.pop(key, None)
    environment.update(RIFF_HOME=str(root / "workspace"), RIFF_INSTALL_ROOT=str(root))
    if runtime:
        environment.update(runtime["environment"])
        environment.update(PATH=os.pathsep.join([str(runtime["media"]), str(runtime["python"].parent),
                                                 "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
                           RIFF_RUNTIME_CONTROL=str(runtime["control"]), PYTHONNOUSERSITE="1")
        if runtime.get("writer_model"):
            environment.update(RIFF_WRITER_PYTHON=str(runtime["python"]), RIFF_WRITER_MODEL=str(runtime["writer_model"]))
    return environment


def app_check(python, app, environment):
    try:
        return subprocess.run([str(python), str(app / "studio.py"), "--check"],
                              env=environment, capture_output=True).returncode == 0
    except OSError:
        return False


def activation_transaction(root, lock_runtime, version, operation):
    """Protect launcher and engine metadata with the same durable transaction."""
    if lock_runtime is None:
        operation()
        return
    with subprocess.Popen([str(lock_runtime["lock_helper"]), str(root / ".riff-installer.lock")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as helper:
        response = helper.stdout.readline().strip()
        if response != b"LOCKED":
            helper.communicate()
            raise RuntimeError("Riff setup is selecting a version. Riff will reopen when it finishes.")
        try:
            recover_activation(root)
            identity = secrets.token_urlsafe(24)
            directory = root / ".activation" / identity
            directory.mkdir(parents=True)
            backups = {}
            for name in ("runtime.json", "launcher.py", "current.json", "workspace/data/engine.json", "workspace/data/engine-activations.json"):
                path = root / name
                if path.is_symlink():
                    raise ValueError("A launch setting is linked and cannot be recovered.")
                if path.exists():
                    owned_path(root, name)
                    backup = directory / name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    with backup.open("xb") as output:
                        output.write(path.read_bytes())
                        output.flush()
                        os.fsync(output.fileno())
                    backup.chmod(0o600)
                    sync_ancestors(backup.parent, root)
                    backups[name] = str(backup.relative_to(root))
                else:
                    backups[name] = None
            sync_directory(directory)
            sync_directory(directory.parent)
            gate = root / ".installer-activation.json"
            journal = {"version": version, "transaction": {"protocol": 1, "id": identity,
                       "phase": "prepared", "backups": backups}}
            write_json_atomic(gate, journal)
            try:
                operation()
                journal["transaction"]["phase"] = "committed"
                write_json_atomic(gate, journal)
                gate.unlink()
                sync_directory(root)
            except Exception:
                recover_activation(root)
                raise
        finally:
            helper.communicate(b"release")


def select_fallback(root, failing_version, previous, previous_value, lock_runtime):
    def restore():
        if json.loads((root / "current.json").read_text()).get("version") != failing_version:
            raise RuntimeError("Riff's selected version changed while opening. Open Riff again.")
        if previous_value is None:
            (root / "runtime.json").unlink(missing_ok=True)
            sync_directory(root)
        else:
            write_json_atomic(root / "runtime.json", previous_value)
        activate_engine(root, root / "releases" / previous, failing_version)
        write_json_atomic(root / "current.json", {"version": previous, "previous": None, "previous_runtime": None})
    activation_transaction(root, lock_runtime, previous, restore)


def select_engine(root, app, runtime):
    def select():
        if json.loads((root / "current.json").read_text()).get("version") != app.name:
            raise RuntimeError("Riff's selected version changed while opening. Open Riff again.")
        activate_engine(root, app)
    activation_transaction(root, runtime, app.name, select)


def activate_engine(root, app, rollback_from=None):
    data = root / "workspace/data"
    data.mkdir(parents=True, exist_ok=True)
    settings_path, receipt = data / "engine.json", data / "engine-activations.json"
    activations = json.loads(receipt.read_text()) if receipt.exists() else {}
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    changed = False
    if rollback_from in activations:
        settings = activations.pop(rollback_from)["before"]
        changed = True
    candidate = app / ".engine.json"
    if candidate.exists() and app.name not in activations:
        activations[app.name] = {"before": dict(settings)}
        settings.update(json.loads(candidate.read_text()))
        changed = True
    if changed:
        write_json_atomic(settings_path, settings)
        write_json_atomic(receipt, activations)


def main():
    root = Path(__file__).resolve().parent
    runtime = configured_runtime(root)
    wait_for_installer(root, runtime)
    # Crash recovery may have restored the previous runtime receipt.
    runtime = configured_runtime(root)
    environment = runtime_environment(root, runtime)
    python = runtime["python"] if runtime else Path(sys.executable)
    pointer = root / "current.json"
    selection = json.loads(pointer.read_text())
    version = selection["version"]
    # The pointer is data, never an executable path supplied by the network.
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError("Invalid installed version.")
    app = root / "releases" / version
    engine_selected = False
    if not app_check(python, app, environment):
        previous = selection.get("previous")
        if isinstance(previous, str) and re.fullmatch(r"\d+(?:\.\d+)*", previous) and (root / "releases" / previous / "studio.py").is_file():
            from_path = root / "releases" / previous
            receipt = from_path / ".runtime.json"
            if receipt.is_symlink():
                raise ValueError("The previous runtime receipt is not a regular file.")
            previous_value = json.loads(receipt.read_text()) if receipt.exists() else selection.get("previous_runtime")
            old_runtime = resolve_runtime(root, previous_value)
            old_environment = runtime_environment(root, old_runtime)
            old_python = old_runtime["python"] if old_runtime else Path(sys.executable)
            if app_check(old_python, from_path, old_environment):
                select_fallback(root, version, previous, previous_value, old_runtime or runtime)
                app = from_path
                environment, python = old_environment, old_python
                engine_selected = True
                print("The update could not start. Riff restored the previous version.", file=sys.stderr)
            else:
                raise RuntimeError("Riff could not start either installed version. Run the installer again.")
        else:
            raise RuntimeError("Riff could not start. Run the installer again.")
    if not engine_selected:
        select_engine(root, app, runtime)
    os.execve(str(python), [str(python), str(app / "studio.py"), *sys.argv[1:]], environment)


if __name__ == "__main__":
    if sys.argv[1:] == ["--installer-protocol"]:
        print(json.dumps({"protocol": 1}))
    else:
        main()
