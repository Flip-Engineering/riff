"""Pinned Ardour runtime discovery for the DAW composition/render workflow.

Production work runs only against the pinned, hash-verified runtime recorded
in ``<data>/ardour-runtime.json``.  There is no demo fallback and no PATH
drift: an unprovisioned, malformed or corrupted record is an explicit error,
so a compose/render request fails closed instead of producing a no-op or
silently using the wrong build.  The record is written by the root-coordinated
provisioning step (see the adapter contract §5.3), never by this module.

The approved pin is Ardour 9.8 — tag object
``059c95883d8158b03f632056ba36e5c7de91ef28``, which peels to commit
``22ed8656c2533e325322ff11831448e5123e0d4b``; the record names the peeled
commit.  ``kind`` must be ``source-build`` or ``reviewed-full``; a demo build
is never a production runtime.
"""
import errno
import hashlib
import json
import os
import stat
from pathlib import Path


class RuntimeUnavailable(ValueError):
    """No verified pinned Ardour runtime is provisioned for this installation."""


RECORD = "ardour-runtime.json"
REQUIRED_BINARIES = ("ardour9-lua", "ardour9-export", "ardour9-new_session")
APPROVED_VERSION = "9.8"
APPROVED_COMMIT = "22ed8656c2533e325322ff11831448e5123e0d4b"
APPROVED_KINDS = ("source-build", "reviewed-full")
# The bundled instrument identity from the retained licensing inventory
# (docs/evidence/ardour-bundle-licensing-20261025.json).
GMSYNTH_URI = "http://gareus.org/oss/lv2/gmsynth"
GENERALUSER_SF2_SHA256 = "c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce"
APPROVED_CONVERTER = "abc2midi 5.07 6582eb8"


def record_path(store):
    return Path(store.data_root) / RECORD


def _sha256_file(content):
    digest = hashlib.sha256()
    for chunk in iter(lambda: content.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _sha256(path):
    with open(path, "rb") as content:
        return _sha256_file(content)


def _refuse(reason):
    raise RuntimeUnavailable(reason + " Re-run the coordinated provisioning step.")


def _hashed_file(prefix, name, expected, executable=False):
    if not isinstance(expected, str) or not len(expected) == 64:
        _refuse(f"The pinned Ardour runtime record is malformed: no sha256 recorded for {name}.")
    path = Path(prefix) / name
    try:
        # O_NOFOLLOW makes the symlink refusal and the hashed bytes refer to
        # the same directory entry: a link swapped in after the check cannot
        # redirect the hash (the gate is atomic, not check-then-use).
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        if error.errno == errno.ELOOP:
            _refuse(f"The pinned Ardour runtime file {name} is a symlink.")
        _refuse(f"The pinned Ardour runtime is incomplete: {name} is missing.")
    with os.fdopen(fd, "rb") as content:
        if not stat.S_ISREG(os.fstat(content.fileno()).st_mode):
            _refuse(f"The pinned Ardour runtime file {name} is not a regular file.")
        if _sha256_file(content) != expected:
            _refuse(f"The pinned Ardour runtime changed since provisioning: {name} no longer "
                    "matches its recorded hash.")
    if executable and not os.access(path, os.X_OK):
        _refuse(f"The pinned Ardour runtime is incomplete: {name} is not executable.")
    return path


def probe(store):
    """The verified runtime record, or None when nothing is provisioned.

    A record that is malformed, unapproved (wrong version/commit/kind), or
    whose listed files are missing, unexecutable or hash-mismatched is refused
    with ``RuntimeUnavailable`` — a missing hash never means verified.
    """
    path = record_path(store)
    try:
        # O_NOFOLLOW makes the symlink refusal and the bytes read refer to
        # the same directory entry: a record swapped in after a check cannot
        # redirect the read (the gate is atomic, not check-then-use).
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            _refuse("The pinned Ardour runtime record is a symlink.")
        _refuse("The pinned Ardour runtime record is not readable.")
    with os.fdopen(fd, "rb") as content:
        if not stat.S_ISREG(os.fstat(content.fileno()).st_mode):
            _refuse("The pinned Ardour runtime record is not a regular file.")
        raw = content.read()
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _refuse("The pinned Ardour runtime record is not readable JSON.")
    if not isinstance(record, dict):
        _refuse("The pinned Ardour runtime record is malformed.")
    if record.get("version") != APPROVED_VERSION:
        _refuse(f"The pinned Ardour runtime version {record.get('version')!r} is not the approved "
                f"{APPROVED_VERSION} pin.")
    if record.get("commit") != APPROVED_COMMIT:
        _refuse("The pinned Ardour runtime commit does not match the approved 9.8 pin "
                f"({APPROVED_COMMIT}).")
    if record.get("kind") not in APPROVED_KINDS:
        _refuse("The pinned Ardour runtime kind must be source-build or reviewed-full; "
                "a demo build is never a production runtime.")
    prefix = record.get("prefix")
    if not isinstance(prefix, str) or not Path(prefix).is_dir():
        _refuse("The pinned Ardour runtime prefix is missing.")
    hashes = record.get("sha256")
    if not isinstance(hashes, dict):
        _refuse("The pinned Ardour runtime record carries no hash manifest.")
    for name in REQUIRED_BINARIES:
        _hashed_file(prefix, name, hashes.get(name), executable=True)
    for key in ("abc2midi", "template"):
        target = record.get(key)
        if not isinstance(target, str):
            _refuse(f"The pinned Ardour runtime record is malformed: no {key} path.")
        if key == "abc2midi":
            _hashed_file("", target, (record.get("tools") or {}).get(key), executable=True)
        else:
            _hashed_file("", target, (record.get("tools") or {}).get(key))
    instrument = record.get("instrument")
    if not isinstance(instrument, dict) or instrument.get("uri") != GMSYNTH_URI:
        _refuse("The pinned Ardour runtime instrument must be the bundled gmsynth LV2.")
    # The bank contract is the approved GeneralUser provenance identity:
    # the record must name GENERALUSER_SF2_SHA256 (written by the
    # provisioner after verifying the staged bytes against it), and probe
    # verifies the installed file at the recorded path byte-for-byte
    # against that identity, atomically.  A self-consistent record naming
    # any other bank is refused, not silently trusted.
    if instrument.get("sf2_sha256") != GENERALUSER_SF2_SHA256:
        _refuse("The pinned Ardour runtime instrument bank does not match the approved "
                "GeneralUser_LV2.sf2 provenance hash.")
    sf2_path = instrument.get("sf2_path")
    if not isinstance(sf2_path, str) or not sf2_path:
        _refuse("The pinned Ardour runtime record is malformed: no instrument.sf2_path.")
    _hashed_file("", sf2_path, instrument.get("sf2_sha256"))
    if record.get("converter") != APPROVED_CONVERTER:
        _refuse(f"The pinned Ardour runtime converter must be the approved {APPROVED_CONVERTER!r}.")
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
