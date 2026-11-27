#!/usr/bin/env python3
"""Provision the pinned Ardour 9.8 runtime from a completed source build (#78).

After `waf install` into the owned prefix, this script:
  1. stages the gmsynth.lv2 payload (plugin + GeneralUser_LV2.sf2) from the
     retained demo-bundle extraction, verifying the SF2 sha256 byte-for-byte;
  2. hashes the required artifacts (wrappers + resolved binaries, the abc2midi
     converter, the session template);
  3. writes the build receipt (riff.daw.ardour-build.v1);
  4. writes the daw_runtime runtime record consumed by
     daw_runtime.probe()/require().

Usage:
  provision_ardour_runtime.py --prefix <owned prefix> --ardour-src <checkout> \
      --bundle-root <extracted demo bundle root> --abc2midi <path> \
      --build-log <configure.log> --receipt-out <build json> --record-out <runtime json>
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

GMSYNTH_URI = "http://gareus.org/oss/lv2/gmsynth"
SF2_SHA256 = "c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce"
CONVERTER_ID = "abc2midi 5.07 6582eb8"
APPROVED_COMMIT = "22ed8656c2533e325322ff11831448e5123e0d4b"
TAG_OBJECT = "059c95883d8158b03f632056ba36e5c7de91ef28"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hashed_file(path, executable=False):
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"missing artifact: {p}")
    if executable and not os.access(p, os.X_OK):
        raise SystemExit(f"artifact is not executable: {p}")
    resolved = p.resolve()
    return {"path": str(p), "sha256": sha256(resolved), "bytes": resolved.stat().st_size,
            **({"symlink_target": os.readlink(p)} if p.is_symlink() else {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--ardour-src", required=True)
    ap.add_argument("--bundle-root", required=True,
                    help="extracted demo bundle root (…/Ardour_x86_64-9.8.241-demo)")
    ap.add_argument("--abc2midi", required=True)
    ap.add_argument("--receipt-out", required=True)
    ap.add_argument("--record-out", required=True)
    args = ap.parse_args()

    prefix = Path(args.prefix).resolve()
    src = Path(args.ardour_src).resolve()
    bundle = Path(args.bundle_root).resolve()

    describe = subprocess.run(["git", "-C", str(src), "describe", "--tags", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    if describe != "9.8":
        raise SystemExit(f"source checkout describes {describe!r}, expected '9.8'")
    commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    if commit != APPROVED_COMMIT:
        raise SystemExit(f"source checkout is {commit}, expected {APPROVED_COMMIT}")

    # 1. Stage the gmsynth payload (plugin + bank) beside the built LV2 dir.
    lv2dir = prefix / "lib" / "ardour9" / "LV2"
    lv2dir.mkdir(parents=True, exist_ok=True)
    gmsynth_src = bundle / "lib" / "LV2" / "gmsynth.lv2"
    gmsynth_dst = lv2dir / "gmsynth.lv2"
    if gmsynth_dst.exists():
        shutil.rmtree(gmsynth_dst)
    shutil.copytree(gmsynth_src, gmsynth_dst)
    sf2 = gmsynth_dst / "GeneralUser_LV2.sf2"
    sf2_hash = sha256(sf2)
    if sf2_hash != SF2_SHA256:
        raise SystemExit(f"staged SF2 hash {sf2_hash} != recorded {SF2_SHA256}")
    for member in ("gmsynth.so", "gmsynth.ttl", "manifest.ttl"):
        if not (gmsynth_dst / member).is_file():
            raise SystemExit(f"staged gmsynth.lv2 lacks {member}")

    # The runtime contract (daw_runtime._hashed_file) refuses symlinked
    # artifacts: a bin/ symlink could be retargeted without a hash change on
    # the link itself.  session_utils installs ardour9-{export,new_session,
    # new_empty_session} as symlinks to ardour-util.sh, which dispatches on
    # `basename $0` — so dereferencing them into real wrapper copies preserves
    # behavior exactly while making each artifact a real, hashed file.
    for name in ("ardour9-export", "ardour9-new_session", "ardour9-new_empty_session"):
        link = prefix / "bin" / name
        if link.is_symlink():
            target = link.resolve()
            link.unlink()
            shutil.copyfile(target, link)
            link.chmod(0o755)

    # 2. Hash the required artifacts (preflight §6: wrappers + resolved binaries).
    binaries = {}
    for name in ("ardour9-lua", "ardour9-export", "ardour9-new_session"):
        binaries[name] = hashed_file(prefix / "bin" / name, executable=True)
    abc2midi = hashed_file(args.abc2midi, executable=True)
    template = hashed_file(src / "libs/ardour/test/profiling/sessions/1region/1region.ardour")
    a_fluidsynth = lv2dir / "a-fluidsynth.lv2"
    if not (a_fluidsynth / "a-fluidsynth.so").is_file():
        raise SystemExit("source-built a-fluidsynth.lv2 is missing from the prefix")

    # The pinned tree carries exactly one documented local patch:
    #   patches/0001-luasession-drop-unconditional-call_slot-cout.patch
    # Upstream luasession.cc's MyEventLoop::call_slot prints two unconditional
    # cout debug leftovers on every dispatch; they race the maintained Lua
    # scripts' stdout evidence lines (byte-level interleaving) and corrupt the
    # fail-closed log protocol (observed: "lugin_isnil=false").  The patch
    # removes the two cout lines only; dispatch behavior is unchanged.
    patch_dir = Path(__file__).resolve().parent / "patches"
    patches = []
    for patch in sorted(patch_dir.glob("*.patch")):
        patches.append({"file": patch.name, "sha256": sha256(patch),
                        "bytes": patch.stat().st_size})
    describe_full = subprocess.run(["git", "-C", str(src), "status", "--porcelain"],
                                   capture_output=True, text=True, check=True).stdout.strip()
    dirty = sorted({line[2:].strip() for line in describe_full.splitlines() if line.strip()})

    receipt = {
        "schema": "riff.daw.ardour-build.v1",
        "pin": {"tag_object": TAG_OBJECT, "commit": APPROVED_COMMIT,
                "describe": describe, "remote": "https://git.ardour.org/ardour/ardour.git"},
        "local_patches": patches,
        "dirty_files": dirty,
        "prefix": str(prefix),
        # Top-level wrapper hash map: the qualification battery
        # (tools/daw/qualify-ardour.sh Q1) cross-checks these exact keys.
        "sha256": {name: meta["sha256"] for name, meta in binaries.items()},
        "artifacts": {"binaries": binaries, "abc2midi": abc2midi, "template": template,
                      "gmsynth": {m: sha256(gmsynth_dst / m)
                                  for m in ("gmsynth.so", "gmsynth.ttl", "manifest.ttl",
                                            "GeneralUser_LV2.sf2")}},
        "payload_note": "gmsynth.lv2 staged from the retained demo bundle; SF2 verified "
                        "byte-for-byte against the recorded provenance hash.",
    }
    Path(args.receipt_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.receipt_out).write_text(json.dumps(receipt, indent=1))

    # 3. Runtime record for daw_runtime.probe()/require().
    record = {
        "version": "9.8",
        "commit": APPROVED_COMMIT,
        "kind": "source-build",
        "prefix": str(prefix / "bin"),
        "abc2midi": str(args.abc2midi),
        "template": str(template["path"]),
        "converter": CONVERTER_ID,
        "instrument": {"uri": GMSYNTH_URI, "sf2_path": str(sf2), "sf2_sha256": SF2_SHA256},
        "sha256": {name: meta["sha256"] for name, meta in binaries.items()},
        "tools": {"abc2midi": abc2midi["sha256"], "template": template["sha256"]},
    }
    Path(args.record_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record_out).write_text(json.dumps(record, indent=1))
    print(json.dumps({"receipt": args.receipt_out, "record": args.record_out,
                      "describe": describe}, indent=1))


if __name__ == "__main__":
    main()
