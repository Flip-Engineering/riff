#!/usr/bin/env python3
"""Prepare immutable v0.6.31 CUDA under queue custody without live install writes.

Run the SHA-pinned, reviewed copy as atari2036 from a new owned NVMe staging
parent. See docs/owner-preparation-v0.6.31.md. Pending import requires a separate
review; this helper never selects the engine or publishes installation metadata.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tarfile
import tempfile

ARCHIVE_SHA256 = '9b4b9e2dfa25495d22230030e4850928956b77109e7a2cc462db8dd80ac5a0f3'
ARCHIVE_BYTES = 2260513
INSTALLER_SHA256 = 'f2d6232f0890a3079edcf3f04df79bb207bdfd587d4e017535b83eb2b31acf88'
SOURCE_COMMIT = '0230da64dd549f4925478903ce8bafc74d3d8c59'
SOURCE_TREE = '03a07bee425beb583d574e820e0f719b72d438b1'
TAG_OBJECT = '0456789fd326bdebca4fa8ef0dd866ee10e33a48'
RUNTIME_COMMIT = 'fbe3eedbf6c504e45189e2cdcf1b257740a28863'
QUEUE = Path('/var/tmp/baton2-compile-queue/compile-queue-runner')
JSON_PREFLIGHT = '''unless Code.ensure_loaded?(:json) and
  function_exported?(:json, :encode, 1) and function_exported?(:json, :decode, 1),
  do: raise("Riff preparation requires OTP :json encode/decode support")
unless :json.decode(IO.iodata_to_binary(:json.encode(%{"riff" => true}))) == %{"riff" => true},
  do: raise("OTP :json round trip failed")
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def clean_environment(environment):
    return {key: value for key, value in environment.items()
            if not key.startswith(('RIFF_', 'CMAKE_', 'SCCACHE_', 'ACTIONS_'))}


def preflight_json(environment):
    """No build or configuration: verify the OTP API required by native_cache.exs."""
    subprocess.run(['elixir', '--erl', '+S 2:2', '-e', JSON_PREFLIGHT], env=environment,
                   check=True, capture_output=True, text=True)


def extract_archive(archive, source):
    with tarfile.open(archive, 'r:gz') as incoming:
        members = incoming.getmembers()
        # Validate every member before extracting any member.
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts and '\\' not in member.name,
                    'The authenticated archive contains an unsafe path.')
            require(member.isdir() or member.isfile(), 'The archive contains a link or special file.')
        for member in members:
            target = source / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with incoming.extractfile(member) as reader, target.open('xb') as writer:
                    shutil.copyfileobj(reader, writer)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def validate_artifact(source, stage, environment):
    # update alone would leave removed values visible to imported release code.
    os.environ.clear()
    os.environ.update(environment)
    sys.path.insert(0, str(source))
    import setup_engine
    from scripts.checkpoint_sha_identity import cache_entries, verify_runtime
    receipt = json.loads((source / 'native-cache-receipts/build.json').read_text())
    require(receipt['schema'] == 'riff.native-cache-build.v1' and
            receipt['backend'] == 'cuda' and receipt['build_exit_status'] == 0,
            'The build receipt is not a successful CUDA preparation.')
    fingerprint = setup_engine.fingerprint(source, receipt['sha256_backend'], receipt['sha256_identity'])
    require(receipt['native_fingerprint'] == fingerprint, 'Native source fingerprint differs.')
    build = source / 'engines' / (fingerprint + '-cuda') / 'build'
    binary = build / 'bin/audiocpp_cli'
    require(binary.is_file() and os.access(binary, os.X_OK), 'The prepared binary is unavailable.')
    entries = cache_entries(build)
    require(entries['ENGINE_ENABLE_CUDA'] == 'ON' and entries['ENGINE_ENABLE_METAL'] == 'OFF',
            'The prepared backend differs.')
    require(entries['CMAKE_CUDA_ARCHITECTURES'], 'The configured CUDA architecture is missing.')
    runtime = json.loads(binary.with_name('riff-sha256.json').read_text())
    require(all(runtime.get(key) == value for key, value in receipt['sha256_identity'].items()),
            'The runtime crypto receipt differs from the build identity.')
    verify_runtime(binary)
    validation = '''Code.require_file("scripts/native_cache.exs")
alias Riff.NativeCache, as: C
app = File.cwd!()
r = C.json("native-cache-receipts/build.json")
build = Path.join([app, "engines", r["native_fingerprint"] <> "-cuda", "build"])
config = %{"build_directory" => build, "source_directory" => Path.join(Path.dirname(build), "audio.cpp"),
 "cmake" => System.find_executable("cmake"), "configuration" => :json.decode(System.fetch_env!("RIFF_REVIEW_CONFIGURATION"))}
i = C.configured!(app, config, "cuda")
unless i.sha256 == r["inputs_sha256"] and i.namespace_sha256 == r["namespace_sha256"] and
 r["source_sha256"] == i.source_sha256 and C.identity(C.json("native-cache-receipts/inputs.json")) == i.sha256 and
 r["sha256_backend"] == i.entries["RIFF_CHECKPOINT_SHA_BACKEND"],
 do: raise("Prepared artifact source/toolchain identity differs")
'''
    validation_environment = dict(environment, RIFF_REVIEW_CONFIGURATION=json.dumps([
        shutil.which('cmake'), *setup_engine.cmake_command(build.parent / 'audio.cpp', build, 'cuda')]))
    subprocess.run(['nice', '-n', '19', 'ionice', '-c', '3', 'elixir', '-e', validation],
                   cwd=source, env=validation_environment, check=True)
    return {'version': '0.6.31', 'source_commit': SOURCE_COMMIT, 'archive_sha256': ARCHIVE_SHA256,
            'backend': 'cuda', 'binary': str(binary), 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'native_fingerprint': fingerprint, 'inputs_sha256': receipt['inputs_sha256'],
            'namespace_sha256': receipt['namespace_sha256'], 'sha256_identity': receipt['sha256_identity'],
            'stage': str(stage), 'inactive': True}


def main(argv=None):
    # Reject both -O/-OO and PYTHONOPTIMIZE before parsing paths or writing files.
    # All other guards are explicit too; no identity check depends on assert.
    require(sys.flags.optimize == 0, 'Run without Python optimization; identity checks are mandatory.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--stage-parent', type=Path, required=True)
    args = parser.parse_args(argv)
    require(pwd.getpwuid(os.geteuid()).pw_name == 'atari2036', 'Run as install owner atari2036, never as root.')
    parent = args.stage_parent.resolve(strict=True)
    require(parent.is_relative_to('/mnt/nvme4tb') and parent.stat().st_uid == os.geteuid(),
            'Use an install-owner-owned NVMe staging parent.')
    require(os.access(parent, os.W_OK | os.X_OK), 'The owner cannot write the staging parent.')
    archive = args.archive.resolve(strict=True)
    require(archive.stat().st_size == ARCHIVE_BYTES and hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA256,
            'The archive is not the immutable official v0.6.31 asset.')
    require(QUEUE.is_file() and os.access(QUEUE, os.X_OK) and os.access(QUEUE.parent, os.W_OK | os.X_OK),
            'The maintained queue or its state directory is unavailable to the install owner.')
    for tool in ('elixir', 'python3', 'git', 'cmake', 'c++', 'nvcc', 'nice', 'ionice'):
        require(shutil.which(tool), 'Missing prerequisite: ' + tool)
    environment = clean_environment(os.environ)
    preflight_json(environment)
    stage = Path(tempfile.mkdtemp(prefix='riff-v0.6.31-cuda-', dir=parent))
    source = stage / 'source'
    source.mkdir()
    extract_archive(archive, source)
    require((source / 'VERSION').read_text().strip() == '0.6.31', 'The archive version differs.')
    require(hashlib.sha256((source / 'install.py').read_bytes()).hexdigest() == INSTALLER_SHA256,
            'The embedded installer identity differs.')
    manifest = json.loads((source / 'sources.json').read_text())
    require(manifest['runtime_commit'] == RUNTIME_COMMIT, 'The pinned audio.cpp commit differs.')
    (stage / 'tmp').mkdir()
    environment.update(RIFF_HOME=str(source), TMPDIR=str(stage / 'tmp'), RIFF_COMPILE_QUEUE=str(QUEUE),
        RIFF_COMPILE_QUEUE_STATE=str(QUEUE.parent), RIFF_NATIVE_BUILD_SOURCE_ID=SOURCE_COMMIT,
        RIFF_NATIVE_BUILD_OUTPUT=str(stage / 'queue-artifact'))
    command = ['nice', '-n', '19', 'ionice', '-c', '3', '/bin/sh',
               'scripts/run_native_build_queued.sh', 'cuda', '--compile-only', '--jobs', '2']
    (stage / 'request.json').write_text(json.dumps({'command': command, 'source_commit': SOURCE_COMMIT,
        'tag_object': TAG_OBJECT, 'source_tree': SOURCE_TREE, 'archive_sha256': ARCHIVE_SHA256,
        'release_id': 423718516}, indent=2) + '\n')
    print('Retained staging directory: ' + str(stage), flush=True)
    with (stage / 'queue.log').open('x') as log:
        subprocess.run(command, cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    plan = validate_artifact(source, stage, environment)
    (stage / 'prepared.json').write_text(json.dumps(plan, indent=2) + '\n')
    print(json.dumps(plan, indent=2))
    print('No install metadata changed. Retain the whole stage; managed pending import requires separate review.')
    return plan


if __name__ == '__main__':
    main()
