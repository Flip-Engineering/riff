"""Recover the generated ABC from YuE2's symbolic token IDs (not from audio)."""
import base64
import json
from pathlib import Path


def read_plan(path, vocabulary, write=True):
    path = Path(path)
    if not path.is_file():
        return None
    plan = json.loads(path.read_text())
    ids = plan["tokens"]
    if not isinstance(ids, list) or any(type(token) is not int for token in ids):
        raise ValueError("The symbolic plan contains invalid token IDs.")
    wanted, tokens = set(ids), {}
    with Path(vocabulary).open() as source:
        for line in source:
            encoded, token = line.rstrip().rsplit(" ", 1)
            index = int(token)
            if index in wanted:
                tokens[index] = base64.b64decode(encoded)
    # ABC end-of-sequence markers have no entry in the mergeable vocabulary.
    text = b"".join(tokens.get(token, b"") for token in ids).decode("utf-8", errors="replace").strip()
    result = {"abc": text, "truncated": bool(plan.get("truncated")), "token_count": len(ids)}
    if write:
        path.with_suffix(".abc").write_text(text + "\n")
    return result
