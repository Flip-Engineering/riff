"""Pinned Ardour runtime discovery for the DAW composition/render workflow.

Production work runs only against the pinned, hash-verified runtime recorded
in ``<data>/ardour-runtime.json``.  There is no demo fallback and no PATH
drift: an unprovisioned or corrupted runtime is an explicit error, so a
compose/render request fails closed instead of producing a no-op or silently
using the wrong build.  The record is written by the root-coordinated
provisioning step (see the adapter contract §5.3), never by this module.
"""
import hashlib
import json
from pathlib import Path


class RuntimeUnavailable(ValueError):
    """No verified pinned Ardour runtime is provisioned for this installation."""


RECORD = "ardour-runtime.json"
REQUIRED_BINARIES = ("ardour9-lua", "ardour9-export", "ardour9-new_session")


def record_path(store):
    return Path(store.data_root) / RECORD


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as content:
        for chunk in iter(lambda: content.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(store):
    """The verified runtime record, or None when nothing is provisioned.

    A record whose listed binaries are missing or whose hashes no longer
    match is corrupted provisioning: an explicit error, not a silent miss.
    """
    path = record_path(store)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    prefix = Path(record["prefix"])
    for name, expected in (record.get("sha256") or {}).items():
        binary = prefix / name
        if not binary.is_file():
            raise RuntimeUnavailable(
                f"The pinned Ardour runtime is incomplete: {name} is missing. "
                "Re-run the coordinated provisioning step.")
        if _sha256(binary) != expected:
            raise RuntimeUnavailable(
                f"The pinned Ardour runtime changed since provisioning: {name} no longer "
                "matches its recorded hash. Re-run the coordinated provisioning step.")
    for name in REQUIRED_BINARIES:
        if not (prefix / name).is_file():
            raise RuntimeUnavailable(
                f"The pinned Ardour runtime is incomplete: {name} is missing. "
                "Re-run the coordinated provisioning step.")
    for key in ("abc2midi", "template"):
        if not Path(record[key]).is_file():
            raise RuntimeUnavailable(
                f"The pinned Ardour runtime is incomplete: {key} is missing. "
                "Re-run the coordinated provisioning step.")
    return record


def require(store):
    """The verified runtime record, or an explicit refusal naming the remedy."""
    record = probe(store)
    if record is None:
        raise RuntimeUnavailable(
            "No pinned Ardour runtime is provisioned for this installation. "
            "The DAW workflow runs only on the reviewed full runtime; ask the "
            "administrator to complete the coordinated provisioning step.")
    return record
