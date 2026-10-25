"""Managed native preparation under fake queue custody; no compilation or weights."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import launcher
import maintenance
import setup_engine


class ManagedBuildQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.source = self.root / 'install/releases/0.6.31'
        self.source.mkdir(parents=True)
        for name in ('sources.json', 'setup_engine.py', 'paths.py', 'platform_support.py',
                     'scripts/run_native_build_queued.sh', 'scripts/build_cached_native.exs',
                     'scripts/native_cache.exs', 'scripts/checkpoint_sha_identity.py',
                     'scripts/compiler_cache_tool.json', '.github/workflows/ci.yml'):
            destination = self.source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(setup_engine.ROOT / name, destination)
        self.manifest = {'runtime_repo': 'fixture', 'runtime_commit': 'fixture-pinned-runtime', 'local_patches': {}, 'model': {'revision': 'same'}}
        (self.source / 'sources.json').write_text(json.dumps(self.manifest))
        # The wrapper runs discovery outside custody; this fixture does no configuration.
        (self.source / 'scripts/checkpoint_sha_identity.py').write_text('print("{}")\n')
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        self.log = self.root / 'queue.jsonl'
        self.queue = self.executable('queue', '''
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:2] == ['--', 'run'], args
assert '--source-root' in args and '--artifact' in args
with Path(os.environ['FIXTURE_LOG']).open('a') as f:
    f.write(json.dumps({'queue': args, 'cwd': os.getcwd()}) + '\\n')
if os.environ.get('FIXTURE_QUEUE_FAIL'):
    sys.exit(7)
command = args[args.index('--', 2) + 1:]
env = dict(os.environ, FIXTURE_IN_CUSTODY='yes')
sys.exit(subprocess.run(command, env=env).returncode)
''')
        self.executable('c++', 'print("fixture compiler version 1")\n')
        self.executable('elixir', '''
import hashlib, json, os, sys
from pathlib import Path
stage = Path(os.environ['RIFF_HOME'])
with Path(os.environ['FIXTURE_LOG']).open('a') as f:
    f.write(json.dumps({'elixir': sys.argv[1:], 'cwd': os.getcwd(), 'home': str(stage),
                       'queue_policy': [k for k in ('RIFF_REQUIRE_COMPILE_QUEUE', 'RIFF_COMPILE_QUEUE', 'RIFF_COMPILE_QUEUE_STATE') if k in os.environ]}) + '\\n')
if 'build_cached_native.exs' in sys.argv[1]:
    assert os.environ.get('FIXTURE_IN_CUSTODY') == 'yes'
    assert not any(k in os.environ for k in ('RIFF_REQUIRE_COMPILE_QUEUE', 'RIFF_COMPILE_QUEUE', 'RIFF_COMPILE_QUEUE_STATE'))
    assert '--compile-only' in sys.argv and '--build-only' not in sys.argv
    backend = sys.argv[sys.argv.index('--backend') + 1]
    fingerprint = hashlib.sha256(json.loads((stage / 'sources.json').read_text())['runtime_commit'].encode()).hexdigest()[:16]
    binary = stage / 'engines' / (fingerprint + '-' + backend) / 'build/bin/audiocpp_cli'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\\necho fixture-device\\n')
    binary.chmod(0o755)
    receipts = stage / 'native-cache-receipts'
    receipts.mkdir()
    receipt = {'schema': 'riff.native-cache-build.v1', 'backend': backend, 'native_fingerprint': fingerprint,
               'sha256_backend': None, 'sha256_identity': {}, 'build_exit_status': 0,
               'inputs_sha256': 'fixture-inputs', 'namespace_sha256': 'fixture-toolchain'}
    if os.environ.get('FIXTURE_BAD_BACKEND'):
        receipt['backend'] = 'wrong'
    if os.environ.get('FIXTURE_BAD_FINGERPRINT'):
        receipt['native_fingerprint'] = '../wrong'
    (receipts / 'build.json').write_text(json.dumps(receipt))
    (receipts / 'inputs.json').write_text('{}')
else:
    assert 'validate_native_build.exs' in sys.argv[1]
    assert stage == Path(sys.argv[2])
    if os.environ.get('FIXTURE_BAD_VALIDATION'):
        sys.exit(9)
''')
        self.state = self.root / 'queue-state'
        self.state.mkdir()
        self.environment = {'PATH': str(self.tools) + os.pathsep + os.environ['PATH'],
                            'RIFF_COMPILE_QUEUE': str(self.queue), 'RIFF_COMPILE_QUEUE_STATE': str(self.state),
                            'RIFF_REQUIRE_COMPILE_QUEUE': '1', 'FIXTURE_LOG': str(self.log)}
        for key in ('RIFF_NATIVE_BUILD_ROOT', 'RIFF_NATIVE_BUILD_OUTPUT', 'RIFF_NATIVE_BUILD_KEY_ONLY',
                    'FIXTURE_QUEUE_FAIL', 'FIXTURE_BAD_VALIDATION', 'FIXTURE_BAD_BACKEND', 'FIXTURE_BAD_FINGERPRINT'):
            self.environment[key] = ''
        self.environment.pop('RIFF_NATIVE_BUILD_ROOT')
        self.patches = [patch.dict(os.environ, self.environment),
                        patch.object(setup_engine, 'WORKSPACE', self.workspace),
                        patch.object(setup_engine, 'prerequisites', return_value=[]),
                        patch.object(setup_engine.platform_support, 'configure')]
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)
        for name, content in {'engines/active/notes': 'keep engine', 'data/engine.json': '{"backend":"cuda","device":0,"threads":64,"warm_engine":true}',
                              'data/music.db': 'library', 'models/weights': 'keep model', 'outputs/take.wav': 'keep recording'}.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.before = self.preserved()
        self.commands = []

    def executable(self, name, code):
        path = self.tools / name
        path.write_text('#!' + sys.executable + '\n' + code)
        path.chmod(0o755)
        return path

    def preserved(self):
        return {str(p.relative_to(self.workspace)): p.read_bytes() for p in self.workspace.rglob('*')
                if p.is_file() and 'native-builds' not in p.relative_to(self.workspace).parts}

    def run_command(self, command, cwd=None, env=None):
        self.commands.append(command)
        subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)

    def prepare(self, **kwargs):
        return setup_engine.prepare('cuda', source=self.source, run=self.run_command,
                                    download_models=False, activate=False, **kwargs)

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_real_wrapper_uses_isolated_custody_and_retains_receipts_without_touching_live_work(self):
        value = self.prepare(cuda_arch='86')
        binary = Path(value['binary'])
        self.assertEqual(value['backend'], 'cuda')
        self.assertTrue(binary.is_file())
        events = self.events()
        self.assertEqual(len(events), 3)
        stage = Path(events[0]['cwd'])
        self.assertNotEqual(stage, self.source)
        self.assertTrue(stage.is_relative_to(self.workspace / 'native-builds'))
        self.assertEqual(events[1]['home'], str(stage))
        self.assertEqual(events[1]['queue_policy'], [])
        self.assertIn('--compile-only', events[1]['elixir'])
        self.assertEqual(events[1]['elixir'][-4:], ['--jobs', '2', '--cuda-arch', '86'])
        self.assertIn('source_id=sha256:', ' '.join(events[0]['queue']))
        self.assertTrue((stage / 'native-cache-receipts/inputs.json').is_file())
        prepared = json.loads((stage / 'prepared.json').read_text())
        self.assertEqual(prepared['binary_sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())
        self.assertEqual(prepared['binary'], value['binary'])
        self.assertEqual(self.before, self.preserved())
        setup_engine.platform_support.configure.assert_not_called()
        self.assertFalse(any('--build' in command for command in self.commands))
        # A second preparation owns a new cwd, avoiding an existing CMakeCache.
        second = self.prepare(jobs=1, probe=False)
        self.assertNotEqual(value['binary'], second['binary'])
        self.assertTrue(binary.is_file())

    def test_required_queue_missing_or_unavailable_never_falls_back(self):
        for environment in ({'RIFF_COMPILE_QUEUE': str(self.root / 'missing')},
                            {'RIFF_COMPILE_QUEUE_STATE': str(self.root / 'missing-state')},
                            {'FIXTURE_QUEUE_FAIL': '1'}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.prepare()
                self.assertEqual(self.before, self.preserved())
                self.assertFalse(any('--build' in command for command in self.commands))
        setup_engine.platform_support.configure.assert_not_called()

    def test_wrong_artifacts_or_validator_failure_cannot_become_a_prepared_engine(self):
        for key, error in [('FIXTURE_BAD_BACKEND', ValueError), ('FIXTURE_BAD_FINGERPRINT', ValueError),
                           ('FIXTURE_BAD_VALIDATION', subprocess.CalledProcessError)]:
            with self.subTest(key=key), patch.dict(os.environ, {key: '1'}):
                with self.assertRaises(error):
                    self.prepare()
        self.assertEqual(self.before, self.preserved())
        self.assertEqual(list(self.workspace.rglob('prepared.json')), [])

    def test_managed_update_publishes_only_validated_engine_and_activation_rechecks_reservation(self):
        app = self.root / 'running-app'
        app.mkdir()
        (app / 'VERSION').write_text('0.6.30')
        (app / 'sources.json').write_text(json.dumps({'model': self.manifest['model']}))
        (self.source / 'studio.py').write_text('pass')
        install_root = self.root / 'install'
        (install_root / 'current.json').write_text('{"version":"0.6.30"}')
        generator = Mock()
        generator.has_active_work.return_value = False
        generator.quiesce.return_value = True
        with patch.dict(os.environ, {'RIFF_INSTALL_ROOT': str(install_root)}), \
                patch.object(maintenance, 'DATA', self.workspace / 'data'), \
                patch.object(maintenance, 'ROOT', app), \
                patch.object(maintenance.Maintenance, 'automatic'), \
                patch.object(maintenance.install, 'get_release', return_value={'version': '0.6.31'}), \
                patch.object(maintenance.install, 'stage_release', return_value=self.source):
            worker = maintenance.Maintenance(generator, None)
            for key in ('FIXTURE_QUEUE_FAIL', 'FIXTURE_BAD_VALIDATION'):
                with patch.dict(os.environ, {key: '1'}):
                    worker.work('update', {})
                self.assertEqual(worker.task['status'], 'failed', worker.task)
                self.assertIsNone(worker.pending)
                self.assertFalse((self.source / '.engine.json').exists())
                self.assertFalse((self.workspace / 'data/pending-update.json').exists())
            worker.work('update', {})
            self.assertEqual(worker.task['status'], 'done', worker.task)
            self.assertEqual(worker.pending, {'version': '0.6.31', 'path': str(self.source)})
            engine = json.loads((self.source / '.engine.json').read_text())
            self.assertEqual(set(engine), {'backend', 'binary'})
            for name, content in self.before.items():
                self.assertEqual((self.workspace / name).read_bytes(), content)
            self.assertEqual(json.loads((install_root / 'current.json').read_text())['version'], '0.6.30')
            generator.quiesce.return_value = False
            with self.assertRaisesRegex(ValueError, 'current work'):
                worker.apply_pending()
            self.assertEqual(json.loads((install_root / 'current.json').read_text())['version'], '0.6.30')
            generator.quiesce.return_value = True
            self.assertEqual(worker.apply_pending(), {'status': 'restarting'})
            # Launcher merges only the prepared engine keys into current settings.
            # Its installation layout uses workspace/ under the managed root.
            (install_root / 'workspace').symlink_to(self.workspace, target_is_directory=True)
            launcher.activate_engine(install_root, self.source)
            selected = json.loads((self.workspace / 'data/engine.json').read_text())
            self.assertEqual(selected, {'backend': 'cuda', 'binary': engine['binary'], 'device': 0,
                                        'threads': 64, 'warm_engine': True})
            self.assertTrue(Path(engine['binary']).is_file())
            self.assertEqual((self.workspace / 'outputs/take.wav').read_text(), 'keep recording')
            generator.resume.assert_called()

    def test_portable_direct_and_configuration_only_paths_keep_existing_behavior(self):
        native_id = setup_engine.fingerprint(self.source)
        build = self.workspace / 'engines' / (native_id + '-cuda') / 'build'
        binary = build / 'bin/audiocpp_cli'
        binary.parent.mkdir(parents=True)
        binary.write_text('fixture')
        commands = Mock()
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('RIFF_COMPILE_QUEUE', 'RIFF_COMPILE_QUEUE_STATE', 'RIFF_REQUIRE_COMPILE_QUEUE')}
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(setup_engine, 'apply_pinned_patches'), \
                patch.object(setup_engine.subprocess, 'run', return_value=Mock(returncode=0, stdout=self.manifest['runtime_commit'])), \
                patch.object(setup_engine.shutil, 'which', return_value='/fixture/cmake'), \
                patch.object(setup_engine, 'prepare_queued') as queued:
            value = setup_engine.prepare('cuda', source=self.source, run=commands, jobs=3,
                                         download_models=False, activate=False, probe=False)
            self.assertEqual(value['binary'], str(binary))
            self.assertTrue(any('--build' in call.args[0] and '3' in call.args[0] for call in commands.call_args_list))
            commands.reset_mock()
            with patch.dict(os.environ, {'RIFF_REQUIRE_COMPILE_QUEUE': '1'}):
                configured = setup_engine.prepare('cuda', source=self.source, run=commands, configure_only=True)
            self.assertEqual(configured['build_directory'], str(build))
            self.assertFalse(any('--build' in call.args[0] for call in commands.call_args_list))
            queued.assert_not_called()

    def test_explicit_queue_configuration_implies_required_and_invalid_policy_fails_closed(self):
        with patch.dict(os.environ, {'RIFF_REQUIRE_COMPILE_QUEUE': '0'}):
            self.assertTrue(setup_engine.queue_required())
        with patch.dict(os.environ, {'RIFF_REQUIRE_COMPILE_QUEUE': 'typo'}):
            with self.assertRaisesRegex(ValueError, 'must be 0 or 1'):
                self.prepare()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(setup_engine.queue_required())
        self.assertEqual(self.commands, [])


@unittest.skipUnless(shutil.which('elixir'), 'Elixir unavailable')
class NativeReceiptValidationTests(unittest.TestCase):
    """Real cache identity reader with synthetic File API data; no CMake/build."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('setup_engine.py', 'paths.py', 'platform_support.py', 'scripts/checkpoint_sha_identity.py',
                     'scripts/native_cache.exs', 'scripts/build_cached_native.exs',
                     'scripts/compiler_cache_tool.json', '.github/workflows/ci.yml'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(setup_engine.ROOT / name, target)
        (self.root / 'sources.json').write_text(json.dumps({'runtime_repo': 'fixture', 'runtime_commit': 'fixture', 'local_patches': {}}))
        self.compiler = self.root / 'fixture-compiler'
        self.compiler.write_text('fixture compiler bytes')
        build = self.root / 'build'
        replies = build / '.cmake/api/v1/reply'
        replies.mkdir(parents=True)
        toolchains = {'kind': 'toolchains', 'version': {'major': 1}, 'toolchains': [
            {'language': language, 'compiler': {'path': str(self.compiler), 'id': 'Fixture', 'version': '1'}}
            for language in ('C', 'CXX')]}
        self.cache = replies / 'cache.json'
        self.cache.write_text(json.dumps({'kind': 'cache', 'version': {'major': 2}, 'entries': [
            {'name': 'ENGINE_ENABLE_CUDA', 'value': 'OFF'}, {'name': 'ENGINE_ENABLE_METAL', 'value': 'OFF'}]}))
        (replies / 'toolchains.json').write_text(json.dumps(toolchains))
        (replies / 'index-fixture.json').write_text(json.dumps({'cmake': {'paths': {'cmake': str(self.compiler)}},
            'reply': {'client-riff-cache': {kind + '-v' + str(version):
                {'kind': kind, 'version': {'major': version}, 'jsonFile': filename}
                for kind, version, filename in [('toolchains', 1, 'toolchains.json'), ('cache', 2, 'cache.json')]}}}))
        self.configuration = json.dumps({'build_directory': str(build), 'source_directory': str(self.root / 'native'),
                                         'cmake': str(self.compiler), 'configuration': [str(self.compiler), '-S', str(self.root / 'native')]})
        self.environment = dict(os.environ, ERL_FLAGS='+S 2:2')
        create = '\n'.join([
            'Code.require_file(Path.join(hd(System.argv()), "scripts/native_cache.exs"))',
            'alias Riff.NativeCache, as: C',
            '[app, config] = System.argv()',
            'i = C.configured!(app, :json.decode(config), "cpu")',
            'output = Path.join(app, "native-cache-receipts")',
            'File.mkdir!(output)',
            'C.write_json(Path.join(output, "inputs.json"), i.descriptor)',
            'C.write_json(Path.join(output, "build.json"), %{"schema" => "riff.native-cache-build.v1", "backend" => "cpu", "build_exit_status" => 0, "inputs_sha256" => i.sha256, "namespace_sha256" => i.namespace_sha256, "source_sha256" => i.source_sha256, "sha256_backend" => nil})'])
        subprocess.run(['elixir', '-e', create, str(self.root), self.configuration],
                       check=True, capture_output=True, env=self.environment)

    def validate(self):
        return subprocess.run(['elixir', str(setup_engine.ROOT / 'scripts/validate_native_build.exs'),
                               str(self.root), 'cpu', self.configuration], capture_output=True, env=self.environment)

    def test_actual_toolchain_and_source_identity_changes_are_rejected(self):
        self.assertEqual(self.validate().returncode, 0)
        for path in (self.compiler, self.root / 'setup_engine.py'):
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            self.assertNotEqual(self.validate().returncode, 0)
            path.write_bytes(original)
        # Even internally rehashed receipt edits cannot claim another backend.
        cache = json.loads(self.cache.read_text())
        cache['entries'][0]['value'] = 'ON'
        self.cache.write_text(json.dumps(cache))
        self.assertNotEqual(self.validate().returncode, 0)

    def test_incomplete_or_changed_receipts_are_rejected(self):
        receipt = self.root / 'native-cache-receipts/build.json'
        original = json.loads(receipt.read_text())
        for key, value in [('build_exit_status', 1), ('backend', 'cuda'), ('inputs_sha256', 'wrong'),
                           ('source_sha256', 'wrong'), ('namespace_sha256', 'wrong')]:
            receipt.write_text(json.dumps({**original, key: value}))
            self.assertNotEqual(self.validate().returncode, 0, key)
        receipt.write_text(json.dumps(original))
        (self.root / 'native-cache-receipts/inputs.json').write_text('{}')
        self.assertNotEqual(self.validate().returncode, 0)


if __name__ == '__main__':
    unittest.main()
