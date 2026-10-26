"""Drive the Ardour compose/render chains for daw jobs on the pinned runtime.

Stages follow the adapter contract §3.1: build (``ardour_session.py``) ->
upgrade-load -> instrument -> tempo/range patch -> reopen/save for
``daw.compose``; resolve -> mix export -> solo-stem snapshots -> per-stem
export for ``daw.render``.  Every stage is fail-closed on evidence: the
expected artifacts exist non-empty and the expected success lines are in the
logs — never the exit status alone (contract I5).  All work runs under an
owned per-job environment with ``JACK_NO_START_SERVER=1``, one process at a
time, using only the pinned runtime's hash-verified binaries and the
repository's committed probe scripts.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import ardour_session
from paths import ROOT


class StageError(RuntimeError):
    """A chain stage failed; the job records the stage and the reason."""

    def __init__(self, stage, error):
        super().__init__(error)
        self.stage = stage


def _environment(job_root):
    home = job_root / "home"
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_RUNTIME_DIR": str(job_root / "run"),
        "TMPDIR": str(job_root / "tmp"),
        "JACK_NO_START_SERVER": "1",
        "PATH": os.environ.get("PATH", ""),
    }
    for directory in (home / ".config", home / ".cache", home / ".local" / "share",
                      home / ".local" / "state", job_root / "run", job_root / "tmp",
                      job_root / "logs", job_root / "export"):
        directory.mkdir(parents=True, exist_ok=True)
    (job_root / "run").chmod(0o700)
    return {**os.environ, **env}


def _run(command, env, log_path, cancelled):
    """Run one chain tool; cancel terminates the child rather than the job."""
    with open(log_path, "w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        while process.poll() is None:
            if cancelled is not None and cancelled.is_set():
                process.terminate()
                process.wait(timeout=10)
                raise StageError("cancelled", "Cancelled.")
            time.sleep(0.05)
    if process.returncode:
        raise StageError("process", f"{Path(command[0]).name} exited {process.returncode}; see the stage log.")


def _lua(runtime, env, script, log_path, cancelled, **probe_env):
    command = [str(Path(runtime["prefix"]) / "ardour9-lua"), str(ROOT / "probes" / "ardour" / script)]
    _run(command, {**env, **probe_env}, log_path, cancelled)


def _expect(log_path, *lines):
    text = Path(log_path).read_text() if Path(log_path).exists() else ""
    for line in lines:
        if line not in text:
            raise StageError("evidence", f"{Path(log_path).name} lacks {line!r}.")


def _expect_file(path):
    if not Path(path).is_file() or not Path(path).stat().st_size:
        raise StageError("evidence", f"{Path(path).name} was not produced.")


def compose(store, job_id, recipe, runtime, cancelled=None, stage_update=None):
    """Build, instrument, patch and reopen the session; record the arrangement.

    Returns the arrangement reference.  ``stage_update(stage, facts)``
    persists progress into the job's recipe for the live receipt.
    """
    daw = recipe["daw"]
    workdir = Path(store.data_root) / "daw" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    env = _environment(workdir)
    logs = workdir / "logs"
    session_dir = workdir / "session"
    name = daw.get("session_name", "arrangement")

    (workdir / "input.abc").write_text(daw["abc"])
    (workdir / "timing.json").write_text(json.dumps(daw["timing"], indent=1))

    def convert(split, out_mid):
        return ardour_session.abc2midi_notes(split, out_mid, runtime["abc2midi"],
                                             ROOT / "probes" / "verify" / "midi_summary.py")

    if stage_update:
        stage_update("session", {"state": "running"})
    session_file = ardour_session.build_session(runtime["template"], workdir / "input.abc",
                                                daw["voice_map"], daw["timing"], session_dir,
                                                name, convert)
    probe = {"ARDOUR_PROBE_SESSION_DIR": str(session_dir), "ARDOUR_PROBE_LOG": ""}
    _lua(runtime, env, "reopen_session.lua", logs / "upgrade.out", cancelled,
         **{**probe, "ARDOUR_PROBE_LOG": str(logs / "upgrade.out"),
            "ARDOUR_PROBE_SNAPSHOT_IN": name, "ARDOUR_PROBE_SNAPSHOT_OUT": "upgraded"})
    _expect(logs / "upgrade.out", "save ok=True")
    if cancelled is not None and cancelled.is_set():
        raise StageError("cancelled", "Cancelled.")
    if stage_update:
        stage_update("session", {"state": "running", "upgraded": True})

    if stage_update:
        stage_update("instrument", {"state": "running"})
    _lua(runtime, env, "instrument_session.lua", logs / "instrument.out", cancelled,
         **{**probe, "ARDOUR_PROBE_LOG": str(logs / "instrument.out"),
            "ARDOUR_PROBE_SNAPSHOT_IN": "upgraded", "ARDOUR_PROBE_SNAPSHOT_OUT": "instrumented"})
    _expect(logs / "instrument.out", "save ok=True")
    if stage_update:
        stage_update("instrument", {"state": "done"})

    if stage_update:
        stage_update("session", {"state": "running", "upgraded": True, "instrumented": True})
    _run([sys.executable, str(ROOT / "probes" / "ardour" / "patch_tempo_range.py"),
          "--session", str(session_dir / "instrumented.ardour"),
          "--timing", str(workdir / "timing.json"),
          "--out", str(session_dir / "tempoed.ardour")], env, logs / "patch.out", cancelled)
    _lua(runtime, env, "reopen_session.lua", logs / "reopen.out", cancelled,
         **{**probe, "ARDOUR_PROBE_LOG": str(logs / "reopen.out"),
            "ARDOUR_PROBE_SNAPSHOT_IN": "tempoed", "ARDOUR_PROBE_SNAPSHOT_OUT": "final"})
    _expect(logs / "reopen.out", "save ok=True")
    _expect_file(session_dir / "final.ardour")
    if stage_update:
        stage_update("session", {"state": "done"})

    recorded = store.arrangements.record(job_id, session_dir, recipe.get("title", ""), daw.get("converter", ""),
                                         composition={"abc": daw.get("abc"), "voice_map": daw.get("voice_map"),
                                                      "timing": daw.get("timing"),
                                                      "session_name": daw.get("session_name")})
    return recorded["reference"]


def render(store, job_id, recipe, runtime, cancelled=None, stage_update=None):
    """Render the mix and per-track stems of a saved arrangement."""
    daw = recipe["daw"]
    workdir = Path(store.data_root) / "daw" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    env = _environment(workdir)
    logs = workdir / "logs"
    exports = workdir / "export"
    source_dir = store.arrangements.path(daw["arrangement_source"])
    session_dir = workdir / "session"
    shutil.copytree(source_dir, session_dir)
    final = "final"

    if stage_update:
        stage_update("render", {"state": "running"})
    mix = exports / "mix.wav"
    _run([str(Path(runtime["prefix"]) / "ardour9-export"), "-o", str(mix), str(session_dir), final],
         env, logs / "mix.out", cancelled)
    _expect_file(mix)
    if stage_update:
        stage_update("render", {"state": "done"})

    stems = []
    if daw.get("stems", True):
        if stage_update:
            stage_update("stems", {"state": "running"})
        _lua(runtime, env, "save_stems.lua", logs / "stems.out", cancelled,
             ARDOUR_PROBE_SESSION_DIR=str(session_dir), ARDOUR_PROBE_SNAPSHOT_IN=final,
             ARDOUR_PROBE_STEM_PREFIX="stem-", ARDOUR_PROBE_LOG=str(logs / "stems.out"))
        _expect(logs / "stems.out", "save ok=True")
        count = sum(1 for line in Path(logs / "stems.out").read_text().splitlines()
                    if line.startswith("stem ") and "save=True" in line)
        for index in range(1, count + 1):
            stem = exports / f"stem-{index}.wav"
            _run([str(Path(runtime["prefix"]) / "ardour9-export"), "-o", str(stem),
                  str(session_dir), f"stem-{index}"], env, logs / f"stem-{index}.out", cancelled)
            _expect_file(stem)
            stems.append({"file": f"export/stem-{index}.wav",
                          "bytes": stem.stat().st_size,
                          "sha256": _sha256(stem)})
        if stage_update:
            stage_update("stems", {"state": "done", "count": count})
    return {"mix": mix, "stems": stems}


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as content:
        for chunk in iter(lambda: content.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
