#!/bin/sh
# Lightweight regression: a bound native source mutation must change the queue key.
# This calls the installed queue's key operation only; it never compiles.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
queue=${RIFF_COMPILE_QUEUE:-/var/tmp/baton2-compile-queue/compile-queue-runner}
[ -x "$queue" ] || {
  echo "shared compile queue is unavailable: $queue" >&2
  exit 1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/riff-native-queue-inputs.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
mkdir -p "$tmp/scripts" "$tmp/.github/workflows" "$tmp/patches" "$tmp/state"
for file in scripts/build_cached_native.exs scripts/native_cache.exs scripts/checkpoint_sha_identity.py scripts/cuda_host_compiler.py setup_engine.py paths.py platform_support.py scripts/compiler_cache_tool.json .github/workflows/ci.yml sources.json; do
  printf 'bound input: %s\n' "$file" > "$tmp/$file"
done
# The real wrapper executes discovery, so this fixture must be valid Python.
printf '#!/usr/bin/env python3\nprint("{}")\n' > "$tmp/scripts/checkpoint_sha_identity.py"
chmod +x "$tmp/scripts/checkpoint_sha_identity.py"
printf 'bound patch\n' > "$tmp/patches/test.patch"

key() {
  (cd "$tmp" && \
    RIFF_NATIVE_BUILD_KEY_ONLY=1 \
    RIFF_NATIVE_BUILD_SOURCE_ID=test \
    RIFF_COMPILE_QUEUE="$queue" \
    RIFF_COMPILE_QUEUE_STATE="$tmp/state" \
    "$root/scripts/run_native_build_queued.sh" cpu --build-only)
}

first=$(key)
printf 'mutated bound input\n' > "$tmp/scripts/build_cached_native.exs"
second=$(key)
[ "$first" != "$second" ] || {
  echo "native source mutation did not change the queue key" >&2
  exit 1
}
printf 'mutated selection policy\n' > "$tmp/scripts/cuda_host_compiler.py"
third=$(key)
[ "$second" != "$third" ] || {
  echo "CUDA selector mutation did not change the queue key" >&2
  exit 1
}
printf 'PASS native queue key changes for source and CUDA selector mutations\n'
printf 'source key: %s\nmutated source key: %s\nmutated selector key: %s\n' "$first" "$second" "$third"
