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
for file in scripts/build_cached_native.exs scripts/native_cache.exs setup_engine.py paths.py platform_support.py scripts/compiler_cache_tool.json .github/workflows/ci.yml sources.json; do
  printf 'bound input: %s\n' "$file" > "$tmp/$file"
done
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
printf 'PASS native queue key changes when a bound source input changes\n'
