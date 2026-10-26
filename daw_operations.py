"""Validation and recipe building for the daw.compose / daw.render operations.

The daw payload is contract-validated here, outside the YuE2 recipe path;
the job's recorded recipe carries exactly these fields.  Failures are plain
``ValueError`` so the operation surface answers 400, and the same checks run
in ``Generator.prepare`` so no route can queue an unvalidated daw job.
"""
import ardour_session
import arrangement_artifacts


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


def _render(daw):
    source = daw.get("arrangement_source")
    if not source:
        raise ValueError("arrangement_source is required.")
    daw.setdefault("format", "wav")
    daw.setdefault("sample_rate", 48000)
    daw.setdefault("bit_depth", "16")
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
    recipe = {"title": _title(payload), "daw": daw}
    source = daw.get("arrangement_source") or daw.get("session_input")
    if source:
        provenance = _resolve_arrangement(store, source)
        recipe["arrangement_source"] = source
        recipe["arrangement"] = provenance
        if kind == "compose":
            # Regeneration from a saved arrangement reuses its recorded inputs.
            daw.update(provenance.get("composition") or {})
    return recipe
