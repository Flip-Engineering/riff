"""CUDA host selection under fake NVCC/toolchains; no CUDA/native compilation."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import setup_engine
from scripts import cuda_host_compiler as cuda


class CudaHostSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.log = self.root / 'probe.jsonl'
        self.compiler('c++', 13)
        self.gcc12 = self.compiler('g++-12', 12)
        self.gcc13 = self.compiler('g++-13', 13)
        self.nvcc = self.root / 'nvcc'
        self.nvcc.write_text('#!' + sys.executable + '\n' + '''
import json, os, subprocess, sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    version = os.environ['FIXTURE_CUDA_VERSION']
    print('nvcc: NVIDIA (R) Cuda compiler driver')
    print('Cuda compilation tools, release ' + version + ', V' + version + '.140')
    sys.exit(0)
assert sys.argv[1] == '--preprocess', 'fixture refuses any actual compile phase'
assert sys.argv[2] == '--compiler-bindir'
host = sys.argv[3]
assert Path(sys.argv[4]).read_text() == '#include <cuda_runtime.h>\\n'
major = int(subprocess.check_output([host, '-dumpversion'], text=True).strip())
with Path(os.environ['FIXTURE_PROBE_LOG']).open('a') as log:
    log.write(json.dumps({'host': host, 'phase': sys.argv[1]}) + '\\n')
if major > int(os.environ['FIXTURE_MAX_HOST']):
    print('host_config.h: error: unsupported GNU version for this toolkit', file=sys.stderr)
    sys.exit(1)
''')
        self.nvcc.chmod(0o755)
        self.environment = {'PATH': str(self.root), 'TMPDIR': str(self.root),
            'FIXTURE_CUDA_VERSION': '12.0', 'FIXTURE_MAX_HOST': '12', 'FIXTURE_PROBE_LOG': str(self.log)}

    def compiler(self, name, major):
        compiler = self.root / name
        compiler.write_text('#!' + sys.executable + '\nimport sys\nprint(' + str(major) +
                            ' if sys.argv[1:] == ["-dumpversion"] else "fixture GCC ' + str(major) + '.3")\n')
        compiler.chmod(0o755)
        return compiler

    def select(self, **environment):
        return cuda.select({**self.environment, **environment})

    def test_default_unsupported_gcc13_discovers_supported_versioned_gcc12(self):
        selected = self.select()
        self.assertEqual(selected['path'], str(self.gcc12))
        self.assertEqual(selected['sha256'], hashlib.sha256(self.gcc12.read_bytes()).hexdigest())
        self.assertIn('release 12.0', selected['nvcc_version'])
        self.assertEqual(selected['verification'], 'nvcc-preprocess-cuda-runtime')
        attempts = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(attempts[0]['host'], str(self.root / 'c++'))
        self.assertTrue(all(attempt['phase'] == '--preprocess' for attempt in attempts))

    def test_compatibility_follows_actual_toolkit_verdict_not_fixed_gcc_ceiling(self):
        selected = self.select(FIXTURE_CUDA_VERSION='12.4', FIXTURE_MAX_HOST='13')
        self.assertEqual(selected['path'], str(self.root / 'c++'))
        self.assertIn('release 12.4', selected['nvcc_version'])
        with self.assertRaisesRegex(ValueError, 'No supported CUDA host compiler'):
            self.select(FIXTURE_CUDA_VERSION='11.8', FIXTURE_MAX_HOST='11')

    def test_explicit_compatible_choice_keeps_literal_alias_and_incompatible_never_substitutes(self):
        alias = self.root / 'chosen-host'
        alias.symlink_to(self.gcc12)
        selected = self.select(CUDAHOSTCXX=str(alias))
        self.assertEqual(selected['path'], str(alias))
        self.log.unlink()
        with self.assertRaisesRegex(ValueError, 'Explicit CUDAHOSTCXX is incompatible.*') as failure:
            self.select(CUDAHOSTCXX=str(self.gcc13))
        self.assertIn('Set CUDAHOSTCXX', str(failure.exception))
        self.assertIn('V12.0.140', str(failure.exception))
        self.assertEqual([json.loads(line)['host'] for line in self.log.read_text().splitlines()], [str(self.gcc13)])

    def test_empty_missing_or_bypass_choices_fail_closed(self):
        for override in ({'CUDAHOSTCXX': ''}, {'CUDAHOSTCXX': '/missing/compiler'},
                         {'NVCC_PREPEND_FLAGS': '-allow-unsupported-compiler'},
                         {'NVCC_APPEND_FLAGS': '--compiler-bindir=/another/compiler'},
                         {'CUDAFLAGS': '-D__NV_NO_HOST_COMPILER_CHECK=1'}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.select(**override)
        self.assertFalse(self.log.exists())

    def test_cudacxx_selects_the_actual_toolkit_instead_of_nvcc_on_path(self):
        other = self.root / 'other-nvcc'
        other.write_text(self.nvcc.read_text().replace("version = os.environ['FIXTURE_CUDA_VERSION']", "version = '12.4'")
                         .replace("int(os.environ['FIXTURE_MAX_HOST'])", '13'))
        other.chmod(0o755)
        selected = self.select(CUDACXX=str(other), CUDAHOSTCXX=str(self.gcc13))
        self.assertEqual(selected['nvcc'], str(other))
        self.assertIn('V12.4.140', selected['nvcc_version'])
        self.assertEqual(selected['path'], str(self.gcc13))
        with self.assertRaisesRegex(ValueError, 'V12.0.140'):
            self.select(CUDAHOSTCXX=str(self.gcc13))

    def test_selected_nvcc_and_compiler_bytes_are_part_of_identity(self):
        selected = self.select(CUDAHOSTCXX=str(self.gcc12), CUDACXX=str(self.nvcc))
        self.assertEqual(selected['nvcc'], str(self.nvcc))
        self.assertEqual(selected['nvcc_sha256'], hashlib.sha256(self.nvcc.read_bytes()).hexdigest())
        self.gcc12.write_text(self.gcc12.read_text() + '# changed compiler implementation\n')
        changed = self.select(CUDAHOSTCXX=str(self.gcc12))
        self.assertNotEqual(changed['sha256'], selected['sha256'])

    def test_setup_configures_literal_verified_host_and_refuses_old_ambiguous_cache(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'sources.json').write_text(json.dumps({'runtime_commit': 'fixture', 'runtime_repo': 'fixture',
                                                       'local_patches': {}}))
        workspace = self.root / 'workspace'
        selected = self.select(CUDAHOSTCXX=str(self.gcc12))
        commands = []
        def run(command, cwd=None, env=None):
            commands.append((command, env))
        with patch.object(cuda, 'select', return_value=selected), \
                patch.object(setup_engine, 'WORKSPACE', workspace), \
                patch.object(setup_engine, 'prerequisites', return_value=[]), \
                patch.object(setup_engine, 'apply_pinned_patches'), \
                patch.object(setup_engine.subprocess, 'run', return_value=Mock(returncode=0, stdout='fixture')):
            configured = setup_engine.prepare('cuda', source=source, run=run, configure_only=True)
            self.assertIn('-DCMAKE_CUDA_HOST_COMPILER=' + str(self.gcc12), configured['configuration'])
            self.assertIn('-DCMAKE_CUDA_COMPILER=' + str(self.nvcc), configured['configuration'])
            self.assertEqual(commands[-1][1]['CUDAHOSTCXX'], str(self.gcc12))
            self.assertEqual(commands[-1][1]['CUDACXX'], str(self.nvcc))
            self.assertFalse(any('--build' in command for command, env in commands))
            build = Path(configured['build_directory'])
            build.mkdir(parents=True)
            cache = build / 'CMakeCache.txt'
            cache.write_text('CMAKE_CUDA_COMPILER:FILEPATH=' + str(self.nvcc) + '\n')
            commands.clear()
            with self.assertRaisesRegex(ValueError, 'fresh RIFF_HOME'):
                setup_engine.prepare('cuda', source=source, run=run, configure_only=True)
            self.assertEqual(commands, [])
            self.assertEqual(cache.read_text(), 'CMAKE_CUDA_COMPILER:FILEPATH=' + str(self.nvcc) + '\n')

    def test_wrapper_rejects_explicit_incompatible_host_before_queue_admission(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        shutil.copyfile(setup_engine.ROOT / 'scripts/cuda_host_compiler.py', scripts / 'cuda_host_compiler.py')
        queue = self.root / 'queue'
        queue.write_text('#!' + sys.executable + '\nraise RuntimeError("queue admission was forbidden")\n')
        queue.chmod(0o755)
        environment = dict(os.environ, **self.environment)
        environment.update(PATH=str(self.root) + os.pathsep + os.environ['PATH'],
            CUDACXX=str(self.nvcc), CUDAHOSTCXX=str(self.gcc13), RIFF_NATIVE_BUILD_SOURCE_ID='fixture-source',
            RIFF_COMPILE_QUEUE=str(queue), RIFF_COMPILE_QUEUE_STATE=str(self.root))
        result = subprocess.run(['/bin/sh', str(setup_engine.ROOT / 'scripts/run_native_build_queued.sh'),
                                 'cuda', '--compile-only', '--jobs', '2'], cwd=self.root,
                                env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Explicit CUDAHOSTCXX is incompatible', result.stderr)
        self.assertNotIn('queue admission was forbidden', result.stderr)
        self.assertEqual(list(self.root.glob('.native-build-inputs.*')), [])

    def test_invalid_explicit_selection_fails_before_queue_or_staging(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'sources.json').write_text('{"local_patches":{}}')
        workspace = self.root / 'workspace'
        with patch.object(setup_engine, 'WORKSPACE', workspace), \
                patch.object(setup_engine, 'prerequisites', return_value=[]), \
                patch.object(cuda, 'select', side_effect=ValueError('Explicit CUDAHOSTCXX is incompatible')), \
                patch.object(setup_engine, 'prepare_queued') as queue:
            with self.assertRaisesRegex(ValueError, 'incompatible'):
                setup_engine.prepare('cuda', source=source, run=Mock())
            queue.assert_not_called()
            self.assertFalse(workspace.exists())


if __name__ == '__main__':
    unittest.main()
