#!/usr/bin/env python3
"""Record Metal eligibility without models; unsupported hardware skips inference."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine_devices import probe_metal_device
from platform_support import settings


def qualification(result):
    facts = result["device"]
    status = result["status"]
    if status == "unsupported":
        detail = " (paravirtual Metal device)" if "paravirtual" in facts.get("name", "").lower() else " (unsupported Metal device)"
        message = "Not qualifiable on this runner" + detail + "; no LoRA inference qualification was performed."
    elif status == "supported":
        message = "Metal device meets the minimum YuE2 requirement; inference qualification must still run."
    else:
        message = "Metal device probe failed; qualification was not run."
    receipt = {"schema": "riff.metal-eligibility.v1", "status": {
        "unsupported": "not-qualified", "supported": "eligible", "unverified": "failed"}[status],
        "qualified": False, "inference_run": False, "message": message,
        "capability": {key: value for key, value in result.items() if key != "output"}}
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe_metal_device(args.binary or settings()["binary"], args.device)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "devices.log").write_text(result.get("output", ""))
    receipt = qualification(result)
    (args.output / "metal-eligibility.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("eligible=" + str(result["status"] == "supported").lower() + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("### " + receipt["message"] + "\n\n")
            summary.write("Device facts (eligibility only; no inference qualification claimed):\n\n```json\n")
            summary.write(json.dumps(receipt["capability"], indent=2) + "\n```\n")
    print(json.dumps(receipt, indent=2))
    return 1 if result["status"] == "unverified" else 0


if __name__ == "__main__":
    sys.exit(main())
