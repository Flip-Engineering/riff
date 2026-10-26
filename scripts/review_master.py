#!/usr/bin/env python3
"""Submit an artist-owned edited master through an already running Riff studio.

No keychain access, account switching or library import. Default is local preparation
only; --submit explicitly uploads and queues one artist request. Retry with the same
request ID and manifest to retrieve that request, never automatically resubmit a failure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def prepare(spec, request_id):
    master = Path(spec["master"]).resolve(strict=True)
    digest = hashlib.sha256()
    with master.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != spec["master_sha256"] or master.stat().st_size != spec["master_bytes"]:
        raise ValueError("Master bytes changed; refresh the artist's request identity.")
    body = {name: spec[name] for name in ("source_recipe", "model", "focus", "keep_lyrics")}
    body.update(request_id=request_id, master={"sha256": digest.hexdigest(), "bytes": spec["master_bytes"],
                                               "duration": spec["duration"]}, edit_context=spec.get("edit_context", {}))
    if spec.get("source_track_id"):
        body["source_track_id"] = spec["source_track_id"]
    return master, body


def request(studio, path, body=None, binary=False, size=None):
    headers = {"X-Riff-Request": "1", "Content-Type": "application/octet-stream" if binary else "application/json"}
    if body is not None and not binary:
        body = json.dumps(body).encode()
    if size is not None:
        headers["Content-Length"] = str(size)
    req = urllib.request.Request(studio + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get("error", "Studio request failed.")
        except ValueError:
            message = "Studio request failed."
        raise ValueError(f"HTTP {error.code}: {message}") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--request-id", required=True, help="Stable artist-chosen ID; a new ID authorizes a new paid attempt.")
    parser.add_argument("--studio", default="http://127.0.0.1:7878")
    parser.add_argument("--output", type=Path, required=True, help="Retained request/result receipt, reusable only for this same request.")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    studio = args.studio.rstrip("/")
    url = urlsplit(studio)
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Choose the existing studio's HTTP(S) origin, without credentials or a path.")
    master, body = prepare(json.loads(args.request.read_text()), args.request_id)
    receipt = {"studio": studio, "request": body, "submitted": False}
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if previous.get("studio") != studio or previous.get("request") != body:
            raise ValueError("Preserve the existing receipt; it names different review inputs.")
        receipt = previous
    else:
        with args.output.open("x") as output:
            output.write(json.dumps(receipt, indent=2) + "\n")

    def save():
        part = args.output.with_name(args.output.name + ".part")
        with part.open("w") as output:
            output.write(json.dumps(receipt, indent=2) + "\n")
        part.replace(args.output)

    if not args.submit:
        print("Prepared locally only; no upload or listening request.")
        return
    try:
        with master.open("rb") as audio:
            uploaded = request(studio, "/api/review/masters?sha256=" + body["master"]["sha256"],
                               audio, binary=True, size=body["master"]["bytes"])
        if (uploaded["sha256"] != body["master"]["sha256"] or uploaded["bytes"] != body["master"]["bytes"]
                or abs(uploaded["duration"] - body["master"]["duration"]) > .001):
            raise ValueError("Uploaded master timing or identity differs from the artist's manifest; no review submitted.")
        receipt.update(upload=uploaded, review=request(studio, "/api/reviews", body), submitted=True)
        receipt.pop("transport_error", None)
        save()
        deadline = time.monotonic() + args.timeout
        while args.wait and receipt["review"]["status"] in ("queued", "running", "cancelling") and time.monotonic() < deadline:
            time.sleep(.5)
            receipt["review"] = request(studio, "/api/reviews/" + receipt["review"]["id"])
            save()
    except Exception as error:
        receipt["transport_error"] = str(error)
        save()
        raise
    print("Review " + receipt["review"]["id"] + ": " + receipt["review"]["status"] + ". Inspect the retained result; queued is not proof of listening.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        sys.exit(str(error))
