#!/usr/bin/env python3
"""End-to-end daw.compose/daw.render run against the built pinned Ardour runtime (#78).

Drives the real operation surface (StudioServer) with the shared four-voice
fixture (probes/fixtures/arrangement-v1.abc + voice-map.json +
arrangement-timing.json): compose builds the session on the source-built
Ardour, render produces the mix and per-track stems.  Emits a
riff.daw.e2e-run.v1 evidence record with artifact hashes.

Usage:
  e2e_daw_run.py --repo /path/to/riff-checkout --runtime-record /path/to/ardour-runtime.json \
      --store /path/to/owned/store --out /path/to/evidence.json
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

JSON = {"Content-Type": "application/json", "X-Riff-Request": "1"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def post(server, name, payload):
    conn = http.client.HTTPConnection(*server.server_address)
    conn.request("POST", f"/api/v1/operations/{name}", json.dumps(payload), JSON)
    response = conn.getresponse()
    body = json.loads(response.read())
    conn.close()
    return response.status, body


def wait_job(store, job_id, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.job(job_id)
        if job and job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.5)
    raise TimeoutError(f"job {job_id} not settled within {timeout}s")


def wav_stats(path):
    import wave, array, math
    w = wave.open(str(path), "rb")
    frames = w.getnframes()
    rate = w.getframerate()
    a = array.array("h")
    a.frombytes(w.readframes(frames))
    peak = max(abs(x) for x in a) if len(a) else 0
    rms = math.sqrt(sum(x * x for x in a) / len(a)) if len(a) else 0.0
    return {"frames": frames, "rate": rate, "seconds": frames / rate,
            "peak_linear": peak, "rms_linear": round(rms, 3), "nonzero": peak > 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--runtime-record", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "tests"))

    from studio import StudioServer
    from studio_core import Store, Generator
    from operations import Operations

    store_root = Path(args.store)
    store_root.mkdir(parents=True, exist_ok=True)
    (store_root / "outputs").mkdir(exist_ok=True)
    store = Store(store_root, store_root / "outputs")

    record = json.loads(Path(args.runtime_record).read_text())
    (store_root / "ardour-runtime.json").write_text(json.dumps(record))

    def command(settings, output):
        raise AssertionError("daw work must not route into the YuE2 command builder")

    generator = Generator(store, command)
    server = StudioServer(("127.0.0.1", 0), store, generator)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    fixtures = repo / "probes" / "fixtures"
    abc = (fixtures / "arrangement-v1.abc").read_text()
    voice_map = json.loads((fixtures / "voice-map.json").read_text())
    timing = json.loads((fixtures / "arrangement-timing.json").read_text())

    evidence = {"schema": "riff.daw.e2e-run.v1", "store": str(store_root),
                "runtime_record": args.runtime_record}

    try:
        status, body = post(server, "daw.compose",
                            {"title": "E2E arrangement v1", "abc": abc,
                             "voice_map": voice_map, "timing": timing,
                             "idempotency_key": "e2e-compose-1"})
        assert status == 201, (status, body)
        job = wait_job(store, body["job_id"])
        assert job["status"] == "done", job
        receipt = Operations(store, generator).receipt(body["operation_id"])
        evidence["compose"] = {
            "job_id": body["job_id"],
            "operation_id": body["operation_id"],
            "stages": receipt["stages"],
            "arrangement_source": receipt["outputs"]["arrangement_source"],
        }
        reference = receipt["outputs"]["arrangement_source"]

        status, render = post(server, "daw.render",
                              {"arrangement_source": reference,
                               "idempotency_key": "e2e-render-1"})
        assert status == 201, (status, render)
        job = wait_job(store, render["job_id"])
        assert job["status"] == "done", job
        rreceipt = Operations(store, generator).receipt(render["operation_id"])
        evidence["render"] = {"job_id": render["job_id"],
                              "stages": rreceipt["stages"],
                              "stem_manifest": rreceipt["outputs"].get("stem_manifest")}

        # Publication: the mix is copied to outputs/riff/<job_id>.wav; stems
        # stay under daw/<render job>/export/ with the manifest carrying
        # per-stem sha256 recorded by the runner.
        render_job = render["job_id"]
        mix_path = store_root / "outputs" / "riff" / f"{render_job}.wav"
        stems = []
        for entry in rreceipt["outputs"].get("stem_manifest") or []:
            p = store_root / "daw" / render_job / entry["file"]
            assert p.is_file(), f"missing published stem {p}"
            stats = wav_stats(p)
            assert sha256(p) == entry["sha256"], f"stem hash drifted after publication: {p}"
            stems.append({"track": entry["track"], "path": str(p),
                          "sha256": entry["sha256"], **stats})
        if mix_path.exists():
            evidence["render"]["mix"] = {"path": str(mix_path), "sha256": sha256(mix_path),
                                         **wav_stats(mix_path)}
        evidence["render"]["stems"] = stems
        evidence["status"] = "pass"
    finally:
        server.shutdown()
        server.server_close()
        generator.close()
        thread.join()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(evidence, indent=1))
    print(json.dumps({"status": evidence["status"], "out": args.out}, indent=1))


if __name__ == "__main__":
    main()
