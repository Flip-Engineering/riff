"""Per-stage timings from a take's engine log.

audio.cpp prints each timing as ``[TIMING ts=YYYYMMDD-HHMMSS] <name> <value>`` (the name and value
are separated by one space and the value is a number; milliseconds for every ``*_ms`` name). The
names below are the ones the pinned YuE2 pipeline emits (``src/models/yue2/pipeline.cpp``,
``ar_runtime.cpp``, ``nar_runtime.cpp``, ``session.cpp``). A name that is not in the log is left
out of the summary; it is never reported as zero, because a stage that did not run and a stage that
was too fast to measure are different facts.
"""
import re

# Stage names match the receipt stages: plan, performance (semantic codes), sound (acoustic
# synthesis) and audio (decoded waveform).
STAGE_KEYS = {"plan": "yue2.plan_ms", "performance": "yue2.semantic_ms", "sound": "yue2.nar_ms",
              "audio": "yue2.vae_decode_ms"}
SESSION_KEY = "session.wall_ms"
# Time spent bringing weights to the device. These lines appear only when the engine had to load
# them, so a warm engine that already held the weights prints none of them. ``yue2.ar.weights_upload_ms``
# already contains ``yue2.ar.prefix_weights_upload_ms`` (3405.96 against 3405.77 ms in a real run), so
# the prefix line is not added again. ``yue2.vae.init_ms`` is not a load cost (about 0.004 ms in a
# real run); the VAE weights are reported by ``framework.oobleck_audio_vae.weights_upload_ms``.
LOAD_KEYS = ("yue2.ar.weights_upload_ms", "yue2.ar.generation_weights_upload_ms", "yue2.nar.weights_upload_ms",
             "framework.oobleck_audio_vae.weights_upload_ms")
# Printed by the engine only when a resident runtime already held the weights.
REUSE_KEYS = ("yue2.ar.resident_reuse", "yue2.nar.resident_reuse")
# Hashing and recording the model files for the saved-sound provenance. It runs for every take, warm
# or not, and was 28 to 32 s of a 168 to 218 s session in a real CPU run.
PIN_KEY = "yue2.acoustic.pin_sources_ms"
LINE = re.compile(r"\[TIMING(?: ts=[^\]\s]*)?\]\s+(\S+)\s+(\S+)")


def _timing_values(lines):
    """Yield valid timing samples without retaining the input or prior samples."""
    for line in lines:
        match = LINE.search(line)
        if not match:
            continue
        try:
            value = float(match.group(2))
        except ValueError:
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        yield match.group(1), value


def parse_timing_lines(lines):
    """Return ({name: [values]}, number of timing lines) for the numeric ``[TIMING]`` lines.

    This collecting interface preserves samples for callers that need them. Take metrics use
    ``summarize`` instead, which keeps a fixed set of aggregates regardless of log length.
    """
    found, count = {}, 0
    for name, value in _timing_values(lines):
        found.setdefault(name, []).append(value)
        count += 1
    return found, count


def _seconds(values):
    return sum(values) / 1000.0


def summarize(lines):
    """Stage seconds, weight-load seconds and engine session seconds for one take.

    ``load_seconds`` is present only when at least one load line was printed. ``parsed_lines`` lets a
    reader tell an empty summary (the log had no timing lines) from a take that simply lacked a stage.
    """
    summed_keys = frozenset((*STAGE_KEYS.values(), *LOAD_KEYS, PIN_KEY))
    totals, count, last_session, resident_reuse = {}, 0, None, False
    for name, value in _timing_values(lines):
        count += 1
        if name in summed_keys:
            totals[name] = totals.get(name, 0.0) + value
        elif name == SESSION_KEY:
            last_session = value
        elif name in REUSE_KEYS and value >= 1:
            resident_reuse = True
    summary = {"parsed_lines": count, "stages": {stage: totals[key] / 1000.0
                                                 for stage, key in STAGE_KEYS.items() if key in totals}}
    loads = [totals[key] for key in LOAD_KEYS if key in totals]
    if loads:
        summary["load_seconds"] = _seconds(loads)
    if last_session is not None:
        summary["engine_session_seconds"] = last_session / 1000.0
    if PIN_KEY in totals:
        summary["acoustic_pin_seconds"] = totals[PIN_KEY] / 1000.0
    if resident_reuse:
        summary["resident_reuse"] = True
    return summary


def warm_record(engine, summary, prepare_seconds=None):
    """Whether a warm take found its weights already loaded.

    ``hit`` is true only for a warm engine whose log says a resident runtime was reused and has no
    weight-load line. It is
    ``None`` when there is nothing to judge from (no timing lines), and false for a take that ran in
    its own process. ``prepare_seconds`` is how long the call that starts or reuses the engine took
    before the take was published as running: near zero for a reused engine, the start-up time otherwise.
    """
    record = {"engine": engine}
    if engine == "warm":
        record["hit"] = None if not summary["parsed_lines"] else (
            summary.get("resident_reuse") is True and "load_seconds" not in summary)
    else:
        record["hit"] = False
    if prepare_seconds is not None:
        record["prepare_seconds"] = prepare_seconds
    return record
