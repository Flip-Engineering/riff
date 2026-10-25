#!/usr/bin/env python3
"""Select a compiled engine for CPU synthetic checks; never qualify Metal inference."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import WORKSPACE
import platform_support


def configure(receipt_path):
    receipt = json.loads(Path(receipt_path).read_text())
    fingerprint = receipt.get("native_fingerprint", "")
    backend = receipt.get("backend")
    if (receipt.get("schema") != "riff.native-cache-build.v1" or receipt.get("build_exit_status") != 0
            or backend not in ("cpu", "metal", "cuda") or not re.fullmatch(r"[0-9a-f]{16}", fingerprint)):
        raise ValueError("CPU native checks require a successful pinned native build receipt.")
    binary = WORKSPACE / "engines" / (fingerprint + "-" + backend) / "build/bin/audiocpp_cli"
    if not binary.is_file():
        raise ValueError("The build receipt's engine executable is missing: " + str(binary))
    return platform_support.configure({"backend": "cpu", "binary": str(binary), "device": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=WORKSPACE / "native-cache-receipts/build.json")
    args = parser.parse_args()
    configured = configure(args.receipt)
    print("CPU synthetic checks selected: " + configured["binary"] + "; no Metal inference qualification claimed.")


if __name__ == "__main__":
    main()
