"""Code and persistent workspace locations for source and managed installs."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("RIFF_HOME", str(ROOT))).expanduser().resolve()
DATA = WORKSPACE / "data"
OUTPUTS = WORKSPACE / "outputs"
MODELS = WORKSPACE / "models"
RUNTIME = WORKSPACE / "audio.cpp"
