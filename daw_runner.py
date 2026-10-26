"""Drive the Ardour compose/render chains for daw jobs on the pinned runtime.

Stages follow the adapter contract §3.1: build (``ardour_session.py``) ->
upgrade-load -> instrument -> tempo/range patch -> reopen/save for
``daw.compose``; resolve -> mix export -> solo-stem snapshots -> per-stem
export for ``daw.render``.  Every stage is fail-closed on evidence: the
expected artifacts exist non-empty and the committed Lua scripts' own success
lines are in the logs — never the exit status alone (contract I5).  All work
runs under an owned per-job environment with ``JACK_NO_START_SERVER=1``, one
process at a time, using only the pinned runtime's hash-verified binaries and
the maintained production script set in ``scripts/daw/``.

Every child process runs in its own process group under the job's admission
custody: cancellation terminates the group, escalating to a kill when a child
ignores TERM, and a cancellation never publishes a cancelled result.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import ardour_session
from paths import ROOT


SCRIPTS = ROOT / "scripts" / "daw"
SUMMARY_TOOL = SCRIPTS / "midi_summary.py"


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


def _stop_process_group(process):
    """TERM the child's group, escalate to KILL when the child ignores it."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait(timeout=10)


def _run(command, env, log_path, cancelled, attach=None, capture=False):
    """Run one chain tool under the job's custody; cancel stops the group."""
    with open(log_path, "w") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE if capture else log,
                                   stderr=subprocess.STDOUT if not capture else subprocess.PIPE,
                                   env=env, start_new_session=True)
        if attach:
            attach(process)
        while process.poll() is None:
            if cancelled is not None and cancelled.is_set():
                _stop_process_group(process)
                raise StageError("cancelled", "Cancelled.")
            time.sleep(0.05)
    if process.returncode:
        raise StageError("process", f"{Path(command[0]).name} exited {process.returncode}; see the stage log.")
    if capture:
        return process.stdout.read()


def _lua(runtime, env, script, log_path, cancelled, attach=None, **probe_env):
    command = [str(Path(runtime["prefix"]) / "ardour9-lua"), str(SCRIPTS / script)]
    _run(command, {**env, **probe_env}, log_path, cancelled, attach=attach)


def _expect_file(path):
    if not Path(path).is_file() or not Path(path).stat().st_size:
        raise StageError("evidence", f"{Path(path).name} was not produced.")


def _tracks_logged(log_path):
    text = Path(log_path).read_text() if Path(log_path).exists() else ""
    match = re.search(r"^tracks=(\d+)$", text, flags=re.M)
    return int(match.group(1)) if match else 0


def _expect_save(log_path, snapshot):
    """The committed scripts write 'save ok=true result=0' (Lua tostring)."""
    _expect_file(snapshot)
    text = Path(log_path).read_text() if Path(log_path).exists() else ""
    if "save ok=true" not in text:
        raise StageError("evidence", f"{Path(log_path).name} lacks 'save ok=true'.")


def compose(store, job_id, recipe, runtime, cancelled=None, stage_update=None, attach=None):
    """Build, instrument, patch and reopen the session; record the arrangement.

    Returns the arrangement reference.  ``stage_update(stage, facts)``
    persists progress into the job's recipe for the live receipt.
    """
    daw = recipe["daw"]
    if cancelled is not None and cancelled.is_set():
        raise StageError("cancelled", "Cancelled.")
    workdir = Path(store.data_root) / "daw" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    env = _environment(workdir)
    logs = workdir / "logs"
    session_dir = workdir / "session"
    name = daw.get("session_name", "arrangement")

    (workdir / "input.abc").write_text(daw["abc"])
    (workdir / "timing.json").write_text(json.dumps(daw["timing"], indent=1))
    (workdir / "voice-map.json").write_text(json.dumps(daw["voice_map"], indent=1))

    def convert(split, out_mid):
        tmp = Path(out_mid).with_suffix('.tmp.mid')
        _run([str(runtime["abc2midi"]), str(split), '-o', str(tmp), '-NGUI'],
             env, logs / f"convert-{Path(split).stem}.out", cancelled, attach=attach)
        out = _run([sys.executable, str(SUMMARY_TOOL), str(tmp)],
                   env, logs / f"summarize-{Path(split).stem}.out", cancelled, attach=attach,
                   capture=True)
        tmp.unlink(missing_ok=True)
        j = json.loads(out)
        notes = []
        for ti, t in enumerate(j['tracks']):
            if ti == 0:
                continue
            for n in t['notes']:
                notes.append((max(0, n['start'] - 1), n['dur'], n['pitch'], n['vel'], n['ch']))
        return notes

    if stage_update:
        stage_update("session", {"state": "running"})
    session_file = ardour_session.build_session(runtime["template"], workdir / "input.abc",
                                                daw["voice_map"], daw["timing"], session_dir,
                                                name, convert)
    _expect_file(session_file)
    groups = ardour_session.group_tracks(daw["voice_map"])
    for g in groups:
        _expect_file(session_dir / "interchange" / name / "midifiles" / f"{g['track']}.mid")
    tracks = len(groups)

    probe = {"ARDOUR_PROBE_SESSION_DIR": str(session_dir)}
    _lua(runtime, env, "reopen_session.lua", logs / "upgrade.out", cancelled, attach=attach,
         **probe, ARDOUR_PROBE_LOG=str(logs / "upgrade.out"),
         ARDOUR_PROBE_SNAPSHOT_IN=name, ARDOUR_PROBE_SNAPSHOT_OUT="upgraded")
    _expect_save(logs / "upgrade.out", session_dir / "upgraded.ardour")
    if _tracks_logged(logs / "upgrade.out") != tracks:
        raise StageError("evidence", "upgrade-load track count does not match the session.")
    if cancelled is not None and cancelled.is_set():
        raise StageError("cancelled", "Cancelled.")
    if stage_update:
        stage_update("session", {"state": "running", "upgraded": True})

    if stage_update:
        stage_update("instrument", {"state": "running"})
    _lua(runtime, env, "instrument_session.lua", logs / "instrument.out", cancelled, attach=attach,
         **probe, ARDOUR_PROBE_LOG=str(logs / "instrument.out"),
         ARDOUR_PROBE_SNAPSHOT_IN="upgraded", ARDOUR_PROBE_SNAPSHOT_OUT="instrumented")
    _expect_save(logs / "instrument.out", session_dir / "instrumented.ardour")
    instrument_log = (logs / "instrument.out").read_text()
    loaded = re.findall(r"^track=(.+) plugin_isnil=(\w+)$", instrument_log, flags=re.M)
    if len(loaded) != tracks or any(isnil != "false" for _, isnil in loaded):
        raise StageError("evidence", "gmsynth did not load on every track.")
    if len(re.findall(r"^add ok=true", instrument_log, flags=re.M)) != tracks:
        raise StageError("evidence", "instrument insertion did not succeed on every track.")
    if stage_update:
        stage_update("instrument", {"state": "done"})

    if stage_update:
        stage_update("session", {"state": "running", "upgraded": True, "instrumented": True})
    _run([sys.executable, str(SCRIPTS / "patch_tempo_range.py"),
          "--session", str(session_dir / "instrumented.ardour"),
          "--timing", str(workdir / "timing.json"),
          "--out", str(session_dir / "tempoed.ardour")], env, logs / "patch.out", cancelled,
         attach=attach)
    _lua(runtime, env, "reopen_session.lua", logs / "reopen.out", cancelled, attach=attach,
         **probe, ARDOUR_PROBE_LOG=str(logs / "reopen.out"),
         ARDOUR_PROBE_SNAPSHOT_IN="tempoed", ARDOUR_PROBE_SNAPSHOT_OUT="final")
    _expect_save(logs / "reopen.out", session_dir / "final.ardour")
    if stage_update:
        stage_update("session", {"state": "done"})

    recorded = store.arrangements.record(job_id, session_dir, recipe.get("title", ""), daw.get("converter", ""),
                                         composition={"abc": daw.get("abc"), "voice_map": daw.get("voice_map"),
                                                      "timing": daw.get("timing"),
                                                      "session_name": daw.get("session_name")})
    return recorded["reference"]


def render(store, job_id, recipe, runtime, cancelled=None, stage_update=None, attach=None):
    """Render the mix and per-track stems of a saved arrangement.

    Stems are per-track snapshots: a shared-instrument track (piano hands on
    one instrument) renders ONE stem, and the manifest names tracks, never
    voices.
    """
    daw = recipe["daw"]
    if cancelled is not None and cancelled.is_set():
        raise StageError("cancelled", "Cancelled.")
    workdir = Path(store.data_root) / "daw" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    env = _environment(workdir)
    logs = workdir / "logs"
    exports = workdir / "export"
    source_dir = store.arrangements.path(daw["arrangement_source"])
    session_dir = workdir / "session"
    shutil.copytree(source_dir, session_dir)
    final = "final"

    export = [str(Path(runtime["prefix"]) / "ardour9-export")]
    if daw.get("sample_rate"):
        export += ["--samplerate", str(daw["sample_rate"])]
    if daw.get("bit_depth"):
        export += ["--bitdepth", str(daw["bit_depth"])]

    if stage_update:
        stage_update("render", {"state": "running"})
    mix = exports / "mix.wav"
    _run(export + ["-o", str(mix), str(session_dir), final], env, logs / "mix.out", cancelled,
         attach=attach)
    _expect_file(mix)
    if stage_update:
        stage_update("render", {"state": "done"})

    stems = []
    if daw.get("stems", True):
        if stage_update:
            stage_update("stems", {"state": "running"})
        _lua(runtime, env, "save_stems.lua", logs / "stems.out", cancelled, attach=attach,
             ARDOUR_PROBE_SESSION_DIR=str(session_dir), ARDOUR_PROBE_SNAPSHOT_IN=final,
             ARDOUR_PROBE_STEM_PREFIX="stem-", ARDOUR_PROBE_LOG=str(logs / "stems.out"))
        text = (logs / "stems.out").read_text()
        saved = re.findall(r"^stem (\d+) (.+) save=(\w+)", text, flags=re.M)
        expected = _tracks_logged(logs / "stems.out")
        indices = [int(i) for i, _, _ in saved]
        if not expected or indices != list(range(1, expected + 1)) or any(ok != "true" for _, _, ok in saved):
            raise StageError("evidence", "stem snapshot evidence is missing, partial or duplicate.")
        for index, track, _ in saved:
            snapshot = session_dir / f"stem-{index}.ardour"
            _expect_file(snapshot)
            stem = exports / f"stem-{index}.wav"
            _run(export + ["-o", str(stem), str(session_dir), f"stem-{index}"],
                 env, logs / f"stem-{index}.out", cancelled, attach=attach)
            _expect_file(stem)
            stems.append({"track": track, "file": f"export/stem-{index}.wav",
                          "bytes": stem.stat().st_size,
                          "sha256": _sha256(stem)})
        if stage_update:
            stage_update("stems", {"state": "done", "count": len(stems)})
    return {"mix": mix, "stems": stems}


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as content:
        for chunk in iter(lambda: content.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
