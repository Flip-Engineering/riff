#!/usr/bin/env python3
"""Stable managed-install launcher; keep data outside versioned application files."""
import json
import os
from pathlib import Path
import subprocess
import sys


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
        temporary = settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings) + "\n")
        temporary.replace(settings_path)
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(activations) + "\n")
        temporary.replace(receipt)


def main():
    root = Path(__file__).resolve().parent
    pointer = root / "current.json"
    selection = json.loads(pointer.read_text())
    version = selection["version"]
    # The pointer is data, never an executable path supplied by the network.
    if not version or any(c not in "0123456789." for c in version):
        raise ValueError("Invalid installed version.")
    app = root / "releases" / version
    rollback_from = None
    environment = {**os.environ, "RIFF_HOME": str(root / "workspace"), "RIFF_INSTALL_ROOT": str(root)}
    check = subprocess.run([sys.executable, str(app / "studio.py"), "--check"], env=environment, capture_output=True)
    if check.returncode:
        previous = selection.get("previous")
        if previous and all(c in "0123456789." for c in previous) and (root / "releases" / previous / "studio.py").is_file():
            from_path = root / "releases" / previous
            old_check = subprocess.run([sys.executable, str(from_path / "studio.py"), "--check"], env=environment, capture_output=True)
            if old_check.returncode == 0:
                temporary = pointer.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": previous, "previous": None}) + "\n")
                temporary.replace(pointer)
                app = from_path
                rollback_from = version
                print("The update could not start. Riff restored the previous version.", file=sys.stderr)
            else:
                raise RuntimeError("Riff could not start either installed version. Run the installer again.")
        else:
            raise RuntimeError("Riff could not start. Run the installer again.")
    activate_engine(root, app, rollback_from)
    os.execve(sys.executable, [sys.executable, str(app / "studio.py"), *sys.argv[1:]], environment)


if __name__ == "__main__":
    main()
