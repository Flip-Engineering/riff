#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
exec "${RIFF_PYTHON:-python3}" studio.py "$@"
