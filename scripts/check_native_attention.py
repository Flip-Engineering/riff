#!/usr/bin/env python3
"""Compare Metal acoustic-attention kernels on identical inputs and allocations."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import platform_support


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, help='audio.cpp build directory; defaults to the configured engine')
    parser.add_argument('--repetitions', type=int, default=3, help='Measured ABBA rounds after kernel warmup')
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('repetitions must be positive')
    build = (args.build or Path(platform_support.settings()['binary']).parent.parent).resolve()
    executable = build/'bin/audiocpp_cli'
    devices = subprocess.check_output([str(executable), '--list-devices'], stderr=subprocess.STDOUT, text=True)
    if platform.system() != 'Darwin' or 'MTL:0' not in devices:
        print('SKIP: Metal execution is unavailable on this runner; compilation is checked separately.')
        return
    source = build.parent/'audio.cpp'
    libraries = build/'ggml/src'
    with tempfile.TemporaryDirectory(prefix='riff-attention-check-') as folder:
        work = Path(folder)
        benchmark = work/'attention'
        subprocess.run(['/usr/bin/c++', '-std=c++17', '-O3', '-I', str(source/'external/ggml/include'),
                        str(ROOT/'tests/native_attention.cpp'), str(libraries/'ggml-metal/libggml-metal.a'),
                        str(libraries/'libggml-base.a'), '-framework', 'Foundation', '-framework', 'Metal',
                        '-framework', 'MetalKit', '-o', str(benchmark)], check=True)
        for queries, keys in ((37, 93), (128, 256), (202, 509), (512, 1021), (802, 1901), (1402, 3299), (4103, 8193), (5040, 18929)):
            output = work/'result.f32'
            result = subprocess.run([str(benchmark), str(queries), str(keys), str(args.repetitions), str(output)],
                                    text=True, capture_output=True, check=True)
            old, wide = Path(str(output)+'.0').read_bytes(), Path(str(output)+'.1').read_bytes()
            if len(old) != queries * 16 * 128 * 4 or old != wide:
                raise AssertionError(f'Attention outputs differ for {queries} queries and {keys} keys')
            if queries >= 4096 and 'dk128_dv128_q16' not in result.stderr:
                raise AssertionError('The wider kernel was not exercised; select the patched engine build')
            rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            print(json.dumps({'queries': queries, 'keys': keys, 'exact': True, 'timings': rows}), flush=True)


if __name__ == '__main__':
    main()
