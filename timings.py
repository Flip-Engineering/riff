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
# them, so a warm engine that already held the weights prints none of them.
LOAD_KEYS = ("yue2.ar.weights_upload_ms", "yue2.ar.generation_weights_upload_ms", "yue2.ar.prefix_weights_upload_ms",
             "yue2.nar.weights_upload_ms", "yue2.vae.init_ms")
LINE = re.compile(r"\[TIMING(?: ts=[^\]\s]*)?\]\s+(\S+)\s+(\S+)")


def parse_timing_lines(lines):
    """Return ({name: [values]}, number of timing lines) for the numeric ``[TIMING]`` lines."""
    found, count = {}, 0
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
        found.setdefault(match.group(1), []).append(value)
        count += 1
    return found, count


def _seconds(values):
    return sum(values) / 1000.0


def summarize(lines):
    """Stage seconds, weight-load seconds and engine session seconds for one take.

    ``load_seconds`` is present only when at least one load line was printed. ``parsed_lines`` lets a
    reader tell an empty summary (the log had no timing lines) from a take that simply lacked a stage.
    """
    found, count = parse_timing_lines(lines)
    summary = {"parsed_lines": count, "stages": {stage: _seconds(found[key])
                                                 for stage, key in STAGE_KEYS.items() if key in found}}
    loads = [value for key in LOAD_KEYS for value in found.get(key, [])]
    if loads:
        summary["load_seconds"] = _seconds(loads)
    if SESSION_KEY in found:
        summary["engine_session_seconds"] = _seconds(found[SESSION_KEY][-1:])
    return summary


def warm_record(engine, summary, prepare_seconds=None):
    """Whether a warm take found its weights already loaded.

    ``hit`` is true only for a warm engine whose log has timing lines but no weight-load line. It is
    ``None`` when there is nothing to judge from (no timing lines), and false for a take that ran in
    its own process. ``prepare_seconds`` is how long the call that starts or reuses the engine took
    before the take was published as running: near zero for a reused engine, the start-up time otherwise.
    """
    record = {"engine": engine}
    if engine == "warm":
        record["hit"] = None if not summary["parsed_lines"] else "load_seconds" not in summary
    else:
        record["hit"] = False
    if prepare_seconds is not None:
        record["prepare_seconds"] = prepare_seconds
    return record
