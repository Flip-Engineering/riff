"""Ardour arrangement (session) artifacts: ``riff-arrangement-v1`` references.

A recorded arrangement is a generated Ardour session directory: the session
XML, the per-track SMF sources, and the exact composition inputs (original
ABC, voice map, timing manifest).  References are content-addressed over the
directory's full file manifest, so the same content resolves to the same
record and a changed session is a new reference.

Receipts and ``references.resolve`` carry provenance fields only, never a
filesystem path (``operations.py`` ``clean_text`` convention).  Records are
local to the library database: arrangements are produced by the local adapter
(``ardour_session.py``), not minted through the engine control plane, so this
module deliberately does not call the Elixir artifact actions.
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
PROVENANCE = ("id", "sha256", "bytes", "files", "tracks", "sample_rate", "converter")

SCHEMA = """
    CREATE TABLE IF NOT EXISTS arrangements (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, title TEXT NOT NULL,
        created REAL NOT NULL, sha256 TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
        provenance TEXT NOT NULL);
"""


def manifest(session_dir):
    """{relative path: sha256} for every regular file under the session dir."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        raise ValueError(f"Arrangement {session_dir} is not a directory.")
    out = {}
    for path in sorted(p for p in session_dir.rglob("*") if p.is_file()):
        relative = path.relative_to(session_dir).as_posix()
        out[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not any(name.endswith(".ardour") for name in out):
        raise ValueError(f"Arrangement {session_dir} has no .ardour session file.")
    return out


def content_digest(files):
    """The reference hash over the full content manifest."""
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArrangementArtifacts:
    def __init__(self, store):
        self.store = store
        self.root = _directory(store.data_root, "arrangements")
        with store.db() as db:
            db.executescript(SCHEMA)

    def record(self, job_id, session_dir, title, converter, composition=None):
        """Record a built session directory as an arrangement artifact.

        Idempotent on content: the same files resolve to the existing record.
        ``converter`` records the tool that produced the SMF sources (e.g.
        "abc2midi 5.07 6582eb8") and ``composition`` the exact inputs (ABC,
        voice map, timing manifest) so a later daw.compose can regenerate from
        the reference.  Returns the reference and its provenance tuple;
        receipts carry these fields, never the path.
        """
        if not re.fullmatch(r"[a-f0-9]{32}", job_id or ""):
            raise ValueError("The generation reference is invalid.")
        session_dir = Path(session_dir)
        files = manifest(session_dir)
        digest = content_digest(files)
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
            shutil.copytree(session_dir, stage)
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

    def describe(self, reference):
        """The recorded arrangement's provenance; no filesystem paths."""
        row = self._row(reference)
        provenance = json.loads(row["provenance"])
        return {"reference": reference, "title": row["title"], "created": row["created"],
                "job_id": row["job_id"], **provenance}

    def path(self, reference):
        """The internal artifact directory; for the render runner, never a receipt."""
        row = self._row(reference)
        return self.root / row["id"]
