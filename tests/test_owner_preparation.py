"""Safety/preflight coverage for the pinned owner recipe; never build or deploy."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

HELPER = Path(__file__).resolve().parents[1] / 'scripts/prepare_release_0_6_31.py'
spec = importlib.util.spec_from_file_location('owner_preparation', HELPER)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)


class OwnerPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.parent = self.root / 'staging'
        self.parent.mkdir()
        self.archive = self.root / 'release.tar.gz'
        self.archive_with({'VERSION': b'0.6.31\n', 'install.py': b'fixture installer',
                           'sources.json': json.dumps({'runtime_commit': owner.RUNTIME_COMMIT}).encode()})
        self.queue = self.root / 'queue/runner'
        self.queue.parent.mkdir()
        self.queue.write_text('fixture only; never executed')
        self.queue.chmod(0o755)
        self.environment = dict(os.environ, RIFF_HOME='/old/live-workspace', RIFF_INSTALL_ROOT='/old/live-install',
            CMAKE_TOOLCHAIN_FILE='/old/toolchain', SCCACHE_DIR='/old/cache', ACTIONS_CACHE_URL='old-service')
        contexts = [patch.dict(os.environ, self.environment, clear=True),
                    patch.object(owner.pwd, 'getpwuid', return_value=Mock(pw_name='fixture-owner')),
                    patch.object(owner, 'ARCHIVE_BYTES', self.archive.stat().st_size),
                    patch.object(owner, 'ARCHIVE_SHA256', hashlib.sha256(self.archive.read_bytes()).hexdigest()),
                    patch.object(owner, 'INSTALLER_SHA256', hashlib.sha256(b'fixture installer').hexdigest()),
                    patch.object(owner, 'QUEUE', self.queue),
                    patch.object(owner.shutil, 'which', side_effect=lambda tool: '/fixture-tools/' + tool)]
        for context in contexts:
            context.start()
            self.addCleanup(context.stop)
        self.args = ['--expected-owner', 'fixture-owner', '--archive', str(self.archive), '--stage-parent', str(self.parent)]

    def archive_with(self, files):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def main(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return owner.main(self.args)

    def test_optimized_python_and_environment_optimization_stop_before_any_write(self):
        # Use the real CLI guard and no fixture identity overrides in children.
        for options, optimized in [(['-O'], None), ([], '1'), ([], '2')]:
            with self.subTest(options=options, optimized=optimized):
                environment = dict(os.environ)
                environment.pop('PYTHONOPTIMIZE', None)
                if optimized:
                    environment['PYTHONOPTIMIZE'] = optimized
                result = subprocess.run([sys.executable, *options, str(HELPER), *self.args],
                                        env=environment, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Run without Python optimization', result.stderr)
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_wrong_owner_or_archive_or_queue_stop_before_preflight_and_writes(self):
        with patch.object(owner, 'preflight_json') as preflight:
            with patch.object(owner.pwd, 'getpwuid', return_value=Mock(pw_name='other-owner')):
                with self.assertRaisesRegex(ValueError, 'Run as the specified install owner'):
                    self.main()
            with patch.object(owner, 'ARCHIVE_SHA256', 'wrong'):
                with self.assertRaisesRegex(ValueError, 'immutable official'):
                    self.main()
            with patch.object(owner, 'QUEUE', self.root / 'missing-queue'):
                with self.assertRaisesRegex(ValueError, 'maintained queue'):
                    self.main()
            preflight.assert_not_called()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_json_failure_is_preflight_before_all_staging_or_build_work(self):
        def fail(environment):
            self.assertFalse(any(key.startswith(('RIFF_', 'CMAKE_', 'SCCACHE_', 'ACTIONS_')) for key in environment))
            self.assertEqual(list(self.parent.iterdir()), [])
            raise subprocess.CalledProcessError(1, ['elixir'])
        with patch.object(owner, 'preflight_json', side_effect=fail), patch.object(owner.subprocess, 'run') as build:
            with self.assertRaises(subprocess.CalledProcessError):
                self.main()
            build.assert_not_called()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_preparation_is_compile_only_in_new_stage_and_retains_failures(self):
        sentinel = self.root / 'live-workspace/settings'
        sentinel.parent.mkdir()
        sentinel.write_text('existing settings and music')
        clean = owner.clean_environment(os.environ)
        commands = []
        def preflight(environment):
            self.assertEqual(environment, clean)
            self.assertEqual(list(self.parent.iterdir()), [])
        def queue(command, **kwargs):
            commands.append((command, kwargs))
            stage = Path(kwargs['cwd']).parent
            environment = kwargs['env']
            self.assertEqual(kwargs['cwd'], stage / 'source')
            self.assertEqual(environment['RIFF_HOME'], str(stage / 'source'))
            self.assertEqual(environment['TMPDIR'], str(stage / 'tmp'))
            self.assertEqual(environment['RIFF_NATIVE_BUILD_OUTPUT'], str(stage / 'queue-artifact'))
            self.assertEqual(environment['RIFF_NATIVE_BUILD_SOURCE_ID'], owner.SOURCE_COMMIT)
            self.assertNotIn('RIFF_INSTALL_ROOT', environment)
            self.assertNotIn('CMAKE_TOOLCHAIN_FILE', environment)
        def validate(source, stage, environment):
            self.assertEqual(source.parent, stage)
            self.assertEqual(environment, commands[0][1]['env'])
            return {'inactive': True, 'stage': str(stage)}
        with patch.object(owner, 'preflight_json', side_effect=preflight), \
                patch.object(owner.subprocess, 'run', side_effect=queue), \
                patch.object(owner, 'validate_artifact', side_effect=validate):
            plan = self.main()
        stage = Path(plan['stage'])
        self.assertTrue((stage / 'prepared.json').is_file())
        self.assertTrue((stage / 'request.json').is_file())
        self.assertEqual(commands[0][0], ['nice', '-n', '19', 'ionice', '-c', '3', '/bin/sh',
            'scripts/run_native_build_queued.sh', 'cuda', '--compile-only', '--jobs', '2'])
        self.assertEqual(sentinel.read_text(), 'existing settings and music')
        self.assertFalse(any(path.name in ('pending-update.json', '.engine.json', 'current.json')
                             for path in self.root.rglob('*')))
        with patch.object(owner, 'preflight_json'), \
                patch.object(owner.subprocess, 'run', side_effect=subprocess.CalledProcessError(7, ['fake-queue'])), \
                patch.object(owner, 'validate_artifact') as validation:
            with self.assertRaises(subprocess.CalledProcessError):
                self.main()
            validation.assert_not_called()
        self.assertEqual(len(list(self.parent.iterdir())), 2)
        self.assertTrue((stage / 'prepared.json').is_file())

    def test_archive_traversal_is_rejected_before_any_extraction(self):
        self.archive_with({'safe-first': b'keep', '../outside': b'bad'})
        source = self.root / 'source'
        source.mkdir()
        with self.assertRaisesRegex(ValueError, 'unsafe path'):
            owner.extract_archive(self.archive, source)
        self.assertEqual(list(source.iterdir()), [])
        self.assertFalse((self.root / 'outside').exists())


class OwnerValidationEnvironmentTests(unittest.TestCase):
    def test_owner_copy_and_execution_recipe_pin_current_helper_bytes(self):
        recipe = (HELPER.parents[1] / 'docs/owner-preparation-v0.6.31.md').read_text()
        digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
        self.assertEqual(recipe.count(digest), 3)
        self.assertIn('install -m 0400 -o "$prep_owner"', recipe)
        self.assertNotIn('/home/', recipe)
        self.assertIn("parser.add_argument('--expected-owner', required=True", HELPER.read_text())
        self.assertIn('Pending metadata import is not approved', recipe)

    def test_imported_validation_observes_exact_cleaned_build_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary).resolve()
            source = stage / 'source'
            (source / 'scripts').mkdir(parents=True)
            expected = owner.clean_environment(os.environ)
            expected['RIFF_HOME'] = str(source)
            (stage / 'expected.json').write_text(json.dumps(expected))
            check = '''import json, os
from pathlib import Path
expected = json.loads((Path(os.environ['RIFF_HOME']).parent / 'expected.json').read_text())
if dict(os.environ) != expected:
    raise RuntimeError('imported validation environment differs')
'''
            (source / 'setup_engine.py').write_text(check + '''
def fingerprint(*args):
    return 'fixture-fingerprint'
def cmake_command(*args):
    return []
''')
            (source / 'scripts/__init__.py').write_text('')
            (source / 'scripts/checkpoint_sha_identity.py').write_text(check + '''
def cache_entries(*args):
    return {'ENGINE_ENABLE_CUDA': 'ON', 'ENGINE_ENABLE_METAL': 'OFF', 'CMAKE_CUDA_ARCHITECTURES': '86'}
def verify_runtime(*args):
    pass
''')
            binary = source / 'engines/fixture-fingerprint-cuda/build/bin/audiocpp_cli'
            binary.parent.mkdir(parents=True)
            binary.write_text('fake engine bytes')
            binary.chmod(0o755)
            binary.with_name('riff-sha256.json').write_text('{"sha256_backend":"portable"}')
            (source / 'native-cache-receipts').mkdir()
            (source / 'native-cache-receipts/build.json').write_text(json.dumps({
                'schema': 'riff.native-cache-build.v1', 'backend': 'cuda', 'build_exit_status': 0,
                'sha256_backend': 'portable', 'sha256_identity': {'sha256_backend': 'portable'},
                'native_fingerprint': 'fixture-fingerprint', 'inputs_sha256': 'inputs', 'namespace_sha256': 'toolchain'}))
            child = '''import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('helper', sys.argv[1])
helper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
stage=Path(sys.argv[2])
environment=json.loads((stage/'expected.json').read_text())
os.environ.update(RIFF_INSTALL_ROOT='/old/live-install',CMAKE_TOOLCHAIN_FILE='/stale',SCCACHE_DIR='/stale',ACTIONS_CACHE_URL='/stale')
def fake_validation(command, **kwargs):
    if kwargs['env'] != dict(environment, RIFF_REVIEW_CONFIGURATION=kwargs['env']['RIFF_REVIEW_CONFIGURATION']):
        raise RuntimeError('child validation environment differs')
helper.subprocess.run=fake_validation
plan=helper.validate_artifact(stage/'source',stage,environment)
if not plan['inactive'] or dict(os.environ) != environment:
    raise RuntimeError('validation changed live state or retained stale environment')
'''
            result = subprocess.run([sys.executable, '-c', child, str(HELPER), str(stage)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('elixir'), 'Elixir unavailable')
    def test_real_json_preflight_uses_supported_api_without_building(self):
        owner.preflight_json(owner.clean_environment(os.environ))

    def test_unsupported_json_preflight_propagates_failure(self):
        with patch.object(owner.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['elixir'])) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                owner.preflight_json({'PATH': '/fixture-tools'})
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ['elixir', '--erl', '+S 2:2', '-e', owner.JSON_PREFLIGHT])
        self.assertIn('function_exported?(:json, :decode, 1)', command[-1])
        self.assertEqual(run.call_args.kwargs['env'], {'PATH': '/fixture-tools'})


if __name__ == '__main__':
    unittest.main()
