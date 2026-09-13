#!/usr/bin/env python3
"""Check the native flow solvers against analytic ODEs without model weights."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import platform_support


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, help='audio.cpp build directory; defaults to the configured engine')
    args = parser.parse_args()
    build = (args.build or Path(platform_support.settings()['binary']).parent.parent).resolve()
    compiler = shutil.which('c++')
    if not compiler:
        raise RuntimeError('A C++ compiler is required for the native solver check.')
    with tempfile.TemporaryDirectory(prefix='riff-solver-check-') as folder:
        executable = Path(folder)/'solver'
        subprocess.run([compiler, '-std=c++17', '-O2', '-I', str(build.parent/'audio.cpp/include'),
                        str(ROOT/'tests/native_solver.cpp'), '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    main()
