"""Adds another Riff workspace's library and recordings to this workspace.

The archive is a tar of another workspace's ``data`` and ``outputs`` folders
(under ``workspace/``), with each SQLite file copied through SQLite's backup.
Rows go in with their own ids (Riff keys every table by text: uuid4 hex or a
content digest). A row this library already holds with the same content stays
once. A row with a held key and other content, or one that collides on a unique
column (a take's file, an idempotency key), stops the import before its commit
and names the table. ``library_meta`` stays this library's own. Files go in
only where this workspace has none of that name; nothing is overwritten. The
studio may run during an import: the library uses WAL, and its triggers count
rows another process adds, so the studio shows them.

    python3 library_import.py ARCHIVE --sha256 HEX --source NAME

prints one JSON line with the counts. A second import from the same source
does nothing (a mark named after the source is kept in the workspace).
"""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

import paths

SKIPPED_TABLES = ("library_meta",)


def digest_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_sqlite(path):
    with open(path, "rb") as file:
        return file.read(16) == b"SQLite format 3\0"


def mark_path(workspace, source):
    return Path(workspace) / f"imported-from-{re.sub(r'[^A-Za-z0-9_-]', '_', source)}.json"


class ImportConflict(ValueError):
    """Rows of the two libraries that cannot both stand; nothing was imported."""


def join_database(moved, kept, report):
    """Adds the moved library's rows to the kept one, in one transaction.

    Every Riff table is keyed by text ids (uuid4 hex, or a content digest). A row
    whose key the kept library has with the same content is kept once. A row
    whose key the kept library has with other content, or that collides with a
    kept row on a unique column (a take's file, an idempotency key), is a
    conflict: the import stops before its commit and names the table. A table
    keyed by an integer would need its keys remapped; Riff has none, and the
    import refuses one rather than drop its rows.
    """
    db = sqlite3.connect(kept, timeout=60)
    try:
        db.execute("ATTACH DATABASE ? AS moved", ("file:" + str(moved) + "?mode=ro",))
        db.execute("BEGIN IMMEDIATE")
        tables = [row[0] for row in db.execute(
            "SELECT name FROM moved.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            if table in SKIPPED_TABLES:
                continue
            theirs = db.execute(f'PRAGMA moved.table_info("{table}")').fetchall()
            if not db.execute(f'PRAGMA main.table_info("{table}")').fetchall():
                db.execute(db.execute("SELECT sql FROM moved.sqlite_master WHERE type='table' AND name=?",
                                      (table,)).fetchone()[0])
            mine = {row[1] for row in db.execute(f'PRAGMA main.table_info("{table}")')}
            key = [row[1] for row in sorted(theirs, key=lambda r: r[5]) if row[5]]
            if not key or any("INT" in (row[2] or "").upper() for row in theirs if row[5]):
                raise ImportConflict(f"Table {table} has no text key; its rows were not imported.")
            columns = [row[1] for row in theirs if row[1] in mine]
            names = ", ".join(f'"{c}"' for c in columns)
            match = " AND ".join(f'k."{c}" = m."{c}"' for c in key)
            same = " AND ".join(f'k."{c}" IS m."{c}"' for c in columns)
            clashes = db.execute(
                f'SELECT count(*) FROM moved."{table}" m JOIN main."{table}" k ON {match} WHERE NOT ({same})').fetchone()[0]
            if clashes:
                raise ImportConflict(f"Table {table}: {clashes} row(s) have a key this library holds with other content.")
            total = db.execute(f'SELECT count(*) FROM moved."{table}"').fetchone()[0]
            before = db.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0]
            try:
                db.execute(f'INSERT INTO main."{table}" ({names}) SELECT {", ".join("m." + chr(34) + c + chr(34) for c in columns)} '
                           f'FROM moved."{table}" m WHERE NOT EXISTS (SELECT 1 FROM main."{table}" k WHERE {match})')
            except sqlite3.IntegrityError as error:
                raise ImportConflict(f"Table {table}: a row collides with one of this library ({error}).") from None
            added = db.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0] - before
            report["rows_added"] += added
            report["rows_kept"] += total - added
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def join_databases(moved, kept, report):
    """First pass: every SQLite file both sides hold is joined (all or nothing per file)."""
    for item in sorted(moved.rglob("*")):
        target = kept / item.relative_to(moved)
        if item.is_file() and not item.is_symlink() and target.is_file() and is_sqlite(item) and is_sqlite(target):
            join_database(item, target, report)


def join_tree(moved, kept, report):
    """Second pass: files this workspace lacks are copied; nothing is overwritten."""
    for item in sorted(moved.rglob("*")):
        target = kept / item.relative_to(moved)
        if item.is_symlink():
            continue
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            if target.exists():
                if not (is_sqlite(item) and is_sqlite(target)) and digest_of(item) != digest_of(target):
                    report["files_kept"] += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            report["files_added"] += 1


def import_library(archive, sha256, source, workspace=None):
    workspace = Path(workspace or paths.WORKSPACE)
    mark = mark_path(workspace, source)
    if mark.exists():
        return {"state": "imported", "already": True, "source": source}
    if digest_of(archive) != sha256:
        raise ValueError("The archive does not have the SHA-256 it was sent with; nothing was imported.")
    report = {"state": "imported", "source": source, "rows_added": 0, "rows_kept": 0,
              "files_added": 0, "files_kept": 0}
    workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workspace) as staging, tarfile.open(archive) as tar:
        tar.extractall(staging, filter="data")
        staging = Path(staging)
        parts = [part for part in ("data", "outputs") if (staging / "workspace" / part).is_dir()]
        for part in parts:
            join_databases(staging / "workspace" / part, workspace / part, report)
        for part in parts:
            join_tree(staging / "workspace" / part, workspace / part, report)
    mark.write_text(json.dumps({"source": source, "sha256": sha256}))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(import_library(args.archive, args.sha256, args.source)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
