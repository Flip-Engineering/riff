#!/usr/bin/env python3
"""Select a CUDA host compiler using the selected NVCC/toolkit's own checks.

The probe only preprocesses cuda_runtime.h; it does not compile, link, inspect a
GPU or use models. Never bypass the toolkit's supported-host-compiler guard.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


PROBE = '#include <cuda_runtime.h>\n'


def executable(value, environment):
    path = shutil.which(value, path=environment.get('PATH', os.defpath)) if value else None
    if not path or not Path(path).is_file():
        raise ValueError('Compiler executable is unavailable: ' + repr(value))
    # Keep the chosen literal alias (and verify its bytes), rather than replacing
    # a user's /path/g++-12 with a different canonical spelling.
    return os.path.abspath(path)


def candidates(environment):
    preferred = [environment.get('CXX'), 'c++', 'g++', 'clang++']
    versioned = set()
    for directory in environment.get('PATH', os.defpath).split(os.pathsep):
        try:
            versioned.update(str(path) for path in Path(directory or '.').iterdir()
                             if re.fullmatch(r'(g\+\+|clang\+\+)-\d+(\.\d+)*', path.name))
        except OSError:
            continue
    preferred += sorted(versioned, key=lambda name: (tuple(map(int, re.findall(r'\d+', Path(name).name))), name), reverse=True)
    seen = set()
    for name in preferred:
        try:
            path = executable(name, environment)
        except ValueError:
            continue
        identity = Path(path).resolve()
        if identity not in seen:
            seen.add(identity)
            yield path


def select(environment=None):
    environment = dict(os.environ if environment is None else environment)
    for key in ('NVCC_PREPEND_FLAGS', 'NVCC_APPEND_FLAGS', 'CUDAFLAGS', 'CXXFLAGS', 'CPPFLAGS'):
        flags = environment.get(key, '')
        if ('allow-unsupported-compiler' in flags or '__NV_NO_HOST_COMPILER_CHECK' in flags or
                re.search(r'(^|\s)(-ccbin|--compiler-bindir)(=|\s|$)', flags)):
            raise ValueError('Remove CUDA host-check/host-compiler overrides from ' + key +
                             '; select a supported compiler with CUDAHOSTCXX.')
    nvcc = executable(environment.get('CUDACXX', 'nvcc'), environment)
    version = subprocess.run([nvcc, '--version'], env=environment, capture_output=True, text=True,
                             check=True, timeout=15).stdout.strip()
    toolkit = re.search(r'Cuda compilation tools, release \d+\.\d+, V\d+\.\d+(?:\.\d+)?', version)
    if not toolkit:
        raise ValueError('CUDACXX must select NVIDIA NVCC with an identifiable CUDA toolkit version.')
    explicit = 'CUDAHOSTCXX' in environment
    choices = [executable(environment['CUDAHOSTCXX'], environment)] if explicit else candidates(environment)
    failures = []
    for host in choices:
        before = hashlib.sha256(Path(host).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='riff-cuda-host-', dir=environment.get('TMPDIR')) as temporary:
            source = Path(temporary) / 'host-check.cu'
            source.write_text(PROBE)
            # -E delegates compatibility to the actual toolkit, including its
            # host_config.h version/platform guards. No object or device code.
            result = subprocess.run([nvcc, '--preprocess', '--compiler-bindir', host, str(source)],
                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode:
            failures.append(host + ': ' + result.stderr.strip()[-2000:])
            continue
        after = hashlib.sha256(Path(host).read_bytes()).hexdigest()
        if before != after:
            raise ValueError('The CUDA host compiler changed during verification: ' + host)
        host_version = subprocess.run([host, '--version'], env=environment, capture_output=True,
                                      text=True, check=True, timeout=15).stdout.strip()
        return {'path': host, 'sha256': after, 'version': host_version,
                'nvcc': nvcc, 'nvcc_sha256': hashlib.sha256(Path(nvcc).read_bytes()).hexdigest(),
                'nvcc_version': version, 'verification': 'nvcc-preprocess-cuda-runtime'}
    choice = 'Explicit CUDAHOSTCXX is incompatible' if explicit else 'No supported CUDA host compiler was found'
    raise ValueError(choice + ' for ' + toolkit.group(0) +
                     '. Set CUDAHOSTCXX to a compiler supported by this toolkit (or unset it for discovery).\n' +
                     '\n'.join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Emit the verified toolchain identity')
    args = parser.parse_args()
    try:
        identity = select()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(identity, sort_keys=True) if args.json else identity['path'])


if __name__ == '__main__':
    main()
