#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
exec "${RIFF_PYTHON:-python3}" setup_engine.py --build-only "$@"
