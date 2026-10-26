"""Ardour arrangement (session) artifacts: ``riff-arrangement-v1`` references.

A recorded arrangement is a generated Ardour session directory: the session
XML, the per-track SMF sources, and the exact composition inputs (original
ABC verbatim, voice map, timing manifest, converter identity).  The reference
identity binds BOTH the session file manifest and the composition record, so
identical files built from different original inputs are distinct references
with their own retained provenance.

Retrieval verifies the stored bytes against the recorded manifest and refuses
symlink escapes or mutated content.  Receipts and ``references.resolve`` carry
provenance fields only, never a filesystem path (``operations.py``
``clean_text`` convention).  Records are local to the library database:
arrangements are produced by the local adapter (``ardour_session.py``), not
minted through the engine control plane, so this module deliberately does not
call the Elixir artifact actions.
"""
import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from score_artifacts import _directory


REFERENCE = re.compile(r"riff-arrangement-v1:([a-f0-9]{64})")
PROVENANCE = ("id", "sha256", "bytes", "files", "tracks", "converter")

SCHEMA = """
    CREATE TABLE IF NOT EXISTS arrangements (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, title TEXT NOT NULL,
        created REAL NOT NULL, sha256 TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
        provenance TEXT NOT NULL);
"""


def manifest(session_dir):
    """{relative path: sha256} for every regular file under the session dir.

    Refuses symlink entries: an arrangement is ordinary owned content, never a
    reference escape.
    """
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        raise ValueError(f"Arrangement {session_dir} is not a directory.")
    out = {}
    for path in sorted(session_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Arrangement {session_dir} contains a symlink: {path.name}.")
        if path.is_file():
            out[path.relative_to(session_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not any(name.endswith(".ardour") for name in out):
        raise ValueError(f"Arrangement {session_dir} has no .ardour session file.")
    return out


def content_digest(files, composition):
    """The reference hash over the full content manifest AND the composition record."""
    canonical = json.dumps({"files": files, "composition": composition},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArrangementArtifacts:
    def __init__(self, store):
        self.store = store
        self.root = _directory(store.data_root, "arrangements")
        with store.db() as db:
            db.executescript(SCHEMA)

    def record(self, job_id, session_dir, title, converter, composition=None):
        """Record a built session directory as an arrangement artifact.

        Idempotent on content AND composition: the same session files built
        from the same inputs resolve to the existing record; identical files
        from different original ABC/map/timing/converter are distinct
        references, each retaining its own inputs.  Returns the reference and
        its provenance tuple; receipts carry these fields, never the path.
        """
        if not re.fullmatch(r"[a-f0-9]{32}", job_id or ""):
            raise ValueError("The generation reference is invalid.")
        session_dir = Path(session_dir)
        files = manifest(session_dir)
        digest = content_digest(files, composition)
        total = sum(path.stat().st_size for path in session_dir.rglob("*") if path.is_file())
        with self.store.db() as db:
            row = db.execute("SELECT * FROM arrangements WHERE sha256=?", (digest,)).fetchone()
            if row:
                return {"reference": f"riff-arrangement-v1:{digest}", "replayed": True,
                        **json.loads(row["provenance"])}
            artifact_id = uuid.uuid4().hex
            provenance = {"id": artifact_id, "sha256": digest, "bytes": total,
                          "files": sorted(files), "tracks": len({p.split('/')[-1] for p in files if p.endswith('.mid')}),
                          "converter": converter, "composition": composition}
            stage = self.root / (".stage-" + artifact_id)
            shutil.copytree(session_dir, stage, symlinks=False)
            _directory(self.root)  # published before the row, so a row implies content
            stage.rename(self.root / artifact_id)
            db.execute("INSERT INTO arrangements VALUES (?,?,?,?,?,?,?)",
                       (artifact_id, job_id, title, time.time(), digest, total,
                        json.dumps(provenance)))
        return {"reference": f"riff-arrangement-v1:{digest}", "replayed": False, **provenance}

    def _row(self, reference):
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise ValueError("The arrangement reference is invalid.")
        digest = REFERENCE.fullmatch(reference).group(1)
        with self.store.db() as db:
            row = db.execute("SELECT * FROM arrangements WHERE sha256=?", (digest,)).fetchone()
        if row is None:
            raise KeyError("Arrangement not found.")
        return row

    def _verified(self, row):
        """Re-hash the stored directory and refuse mutated or escaped content."""
        provenance = json.loads(row["provenance"])
        stored = self.root / row["id"]
        if not stored.is_dir():
            raise KeyError("The arrangement's content is missing.")
        current = manifest(stored)
        if content_digest(current, provenance.get("composition")) != row["sha256"]:
            raise ValueError("The arrangement's stored content does not match its record; "
                             "refusing mutated evidence.")
        return provenance

    def describe(self, reference):
        """The recorded arrangement's provenance; no filesystem paths."""
        row = self._row(reference)
        provenance = self._verified(row)
        return {"reference": reference, "title": row["title"], "created": row["created"],
                "job_id": row["job_id"], **provenance}

    def path(self, reference):
        """The internal artifact directory, verified; for the render runner, never a receipt."""
        row = self._row(reference)
        self._verified(row)
        return self.root / row["id"]
