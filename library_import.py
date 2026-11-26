"""Adds another Riff workspace's library and recordings to this workspace.

The archive is a tar of another workspace's ``data`` and ``outputs`` folders
(under ``workspace/``), with each SQLite file copied through SQLite's backup.
Every table row goes in with its own id; a row whose key this library already
has stays as this library has it. A file goes in only where this workspace has
none; a file of the same name is never overwritten. ``library_meta`` stays this
library's own. The studio may run during an import: the library uses WAL, and
its triggers count rows that another process adds, so the studio shows them.

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


def join_database(moved, kept, report):
    db = sqlite3.connect(kept, timeout=60)
    try:
        db.execute("ATTACH DATABASE ? AS moved", ("file:" + str(moved) + "?mode=ro",))
        db.execute("BEGIN IMMEDIATE")
        tables = [row[0] for row in db.execute(
            "SELECT name FROM moved.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            if table in SKIPPED_TABLES:
                continue
            mine = [row[1] for row in db.execute(f'PRAGMA main.table_info("{table}")')]
            if not mine:
                db.execute(db.execute("SELECT sql FROM moved.sqlite_master WHERE type='table' AND name=?",
                                      (table,)).fetchone()[0])
                mine = [row[1] for row in db.execute(f'PRAGMA main.table_info("{table}")')]
            columns = ", ".join(f'"{c}"' for c in (row[1] for row in db.execute(f'PRAGMA moved.table_info("{table}")'))
                                if c in mine)
            total = db.execute(f'SELECT count(*) FROM moved."{table}"').fetchone()[0]
            # Counted by rows: total_changes would also count the library's own triggers.
            before = db.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0]
            db.execute(f'INSERT OR IGNORE INTO main."{table}" ({columns}) SELECT {columns} FROM moved."{table}"')
            added = db.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0] - before
            report["rows_added"] += added
            report["rows_kept"] += total - added
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def join_tree(moved, kept, report):
    for item in sorted(moved.rglob("*")):
        target = kept / item.relative_to(moved)
        if item.is_symlink():
            continue
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            if target.exists():
                if is_sqlite(item) and is_sqlite(target):
                    join_database(item, target, report)
                elif digest_of(item) != digest_of(target):
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
        for part in ("data", "outputs"):
            if (staging / "workspace" / part).is_dir():
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
