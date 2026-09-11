#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "The MLX writer needs Apple Silicon. Choose OpenRouter writer or Phrase shuffle in Riff."
  exit 1
fi
riff_workspace="${RIFF_HOME:-$PWD}"
uv venv --python 3.11 "$riff_workspace/.writer-venv" --allow-existing
uv pip install --python "$riff_workspace/.writer-venv/bin/python" -r requirements-writer.lock
"$riff_workspace/.writer-venv/bin/python" - <<'PY'
from huggingface_hub import snapshot_download
from writer import MODEL_ID, REVISION
from paths import WORKSPACE
snapshot_download(MODEL_ID, revision=REVISION, local_dir=str(WORKSPACE / 'models/lyric-writer'))
PY
