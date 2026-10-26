"""Validation and recipe building for the daw.compose / daw.render operations.

The daw payload is contract-validated here, outside the YuE2 recipe path;
the job's recorded recipe carries exactly these fields.  Failures are plain
``ValueError`` so the operation surface answers 400, and the same checks run
in ``Generator.prepare`` so no route can queue an unvalidated daw job.

Unsupported selections are rejected before queueing — the runner never
silently substitutes a timbre, format, rate or depth the requester did not
ask for.
"""
import ardour_session
import arrangement_artifacts
import daw_runtime


DEFAULT_INSTRUMENT = {"plugin": daw_runtime.GMSYNTH_URI, "bank": "GeneralUser_LV2.sf2"}
SUPPORTED_FORMATS = ("wav",)
SUPPORTED_RATES = (44100, 48000, 96000)
SUPPORTED_DEPTHS = ("16", "24", "float")


def _title(payload):
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("A title is required.")
    return title.strip()


def _resolve_arrangement(store, source):
    if not isinstance(source, str) or not arrangement_artifacts.REFERENCE.fullmatch(source):
        raise ValueError("arrangement_source must be a riff-arrangement-v1 reference.")
    try:
        return store.arrangements.describe(source)
    except KeyError:
        raise ValueError("The arrangement reference is unavailable.") from None


def _instruments(daw):
    """Instrument selection: the bundled gmsynth/GeneralUser default or nothing.

    Any other engine, bank or override is refused until that selection is
    implemented and qualified; the runner never substitutes silently.
    """
    overrides = daw.get("instrument_map")
    if overrides is None:
        daw["instrument_map"] = {"default": dict(DEFAULT_INSTRUMENT)}
        return
    if overrides != {"default": DEFAULT_INSTRUMENT}:
        raise ValueError("Only the bundled gmsynth + GeneralUser instrument is supported; "
                         "other instrument_map selections are not yet implemented.")


def _compose(daw):
    abc, session_input = daw.get("abc"), daw.get("session_input")
    if bool(abc) == bool(session_input):
        raise ValueError("Send exactly one of abc or session_input.")
    if abc:
        voice_map = daw.get("voice_map")
        timing = daw.get("timing")
        if not isinstance(voice_map, dict) or not isinstance(timing, dict):
            raise ValueError("abc composition requires a voice_map and a timing manifest.")
        try:
            header, bodies = ardour_session.split_abc_voices(abc)
            ardour_session.check_voice_sets(bodies, voice_map)
            ardour_session.group_tracks(voice_map)
            ardour_session.timing_to_meta(timing)
        except ardour_session.ContractError as error:
            raise ValueError(str(error)) from None
        daw.setdefault("session_name", "arrangement")
        daw.setdefault("converter", "abc2midi 5.07 6582eb8")
        daw.pop("session_input", None)
    _instruments(daw)


def _render(daw):
    source = daw.get("arrangement_source")
    if not source:
        raise ValueError("arrangement_source is required.")
    fmt = daw.setdefault("format", "wav")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"format {fmt} is not supported by the runner; supported: "
                         + ", ".join(SUPPORTED_FORMATS) + ".")
    rate = daw.setdefault("sample_rate", 48000)
    if rate not in SUPPORTED_RATES:
        raise ValueError(f"sample_rate {rate} is not supported; supported: "
                         + ", ".join(map(str, SUPPORTED_RATES)) + ".")
    depth = daw.setdefault("bit_depth", "16")
    if depth not in SUPPORTED_DEPTHS:
        raise ValueError(f"bit_depth {depth} is not supported; supported: "
                         + ", ".join(SUPPORTED_DEPTHS) + ".")
    daw.setdefault("stems", True)


def prepare(store, payload):
    """The daw job recipe: title + the contract-validated daw block."""
    daw = payload.get("daw")
    if not isinstance(daw, dict):
        raise ValueError("The daw operation block is required.")
    kind = daw.get("kind")
    if kind == "compose":
        _compose(daw)
    elif kind == "render":
        _render(daw)
    else:
        raise ValueError("The daw operation kind is required.")
    daw["stages"] = {}
    recipe = {"daw": daw}
    source = daw.get("arrangement_source") or daw.get("session_input")
    provenance = None
    if source:
        provenance = _resolve_arrangement(store, source)
        recipe["arrangement_source"] = source
        recipe["arrangement"] = provenance
        if kind == "compose":
            # Regeneration from a saved arrangement reuses its recorded inputs.
            daw.update(provenance.get("composition") or {})
    if kind == "render" and not payload.get("title") and provenance is not None:
        recipe["title"] = provenance["title"] or "Arrangement render"
    else:
        recipe["title"] = _title(payload)
    return recipe
