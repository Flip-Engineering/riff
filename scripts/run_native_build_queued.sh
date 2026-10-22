#!/bin/sh
# Run the native engine build through the homelab shared compile queue.
#
# Only the build belongs under queue custody. Solver, LoRA, score-replay and
# acoustic checks remain ordinary caller steps after this script returns.
#
# The queue executable and state root are host provisioning, not source. They
# can be overridden for another runner, but a missing queue is a hard failure:
# there is deliberately no direct-build fallback.
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: $0 BACKEND [build_cached_native options...]" >&2
  exit 2
fi

backend=$1
shift

queue=${RIFF_COMPILE_QUEUE:-/var/tmp/baton2-compile-queue/compile-queue-runner}
state_root=${RIFF_COMPILE_QUEUE_STATE:-/var/tmp/baton2-compile-queue}
cwd=$(pwd -P)
output=${RIFF_NATIVE_BUILD_OUTPUT:-"$cwd/.native-build-queue"}
source_id=${RIFF_NATIVE_BUILD_SOURCE_ID:-}

if [ -z "$source_id" ]; then
  source_id=$(git rev-parse HEAD 2>/dev/null || true)
fi
[ -n "$source_id" ] || {
  echo "cannot determine native source identity; set RIFF_NATIVE_BUILD_SOURCE_ID" >&2
  exit 1
}

if [ ! -x "$queue" ]; then
  echo "shared compile queue is unavailable: $queue" >&2
  echo "provision the homelab queue or set RIFF_COMPILE_QUEUE" >&2
  exit 1
fi
[ -d "$state_root" ] || {
  echo "shared compile queue state root is unavailable: $state_root" >&2
  exit 1
}

compiler=${CXX:-c++}
command -v "$compiler" >/dev/null 2>&1 || {
  echo "native C++ compiler is unavailable: $compiler" >&2
  exit 1
}
compiler_version=$("$compiler" --version | head -n 1)

# Bind the actual build identity before taking queue custody. The manifest
# contains the relative hashes of the build scripts, drivers, policy, engine
# pins/patches and the resolved toolchain versions. The queue hashes this
# manifest as an auxiliary input, so changing any bound byte changes its key.
manifest=$(mktemp "$cwd/.native-build-inputs.XXXXXX")
trap 'rm -f "$manifest"' EXIT HUP INT TERM
(
  cd "$cwd" || exit 1
  for file in scripts/build_cached_native.exs scripts/native_cache.exs setup_engine.py paths.py platform_support.py scripts/compiler_cache_tool.json .github/workflows/ci.yml sources.json; do
    [ -f "$file" ] || {
      echo "missing native build input: $file" >&2
      exit 1
    }
    sha256sum "$file"
  done
  for patch in patches/*.patch; do
    [ -f "$patch" ] || continue
    sha256sum "$patch"
  done
  printf 'compiler=%s\n' "$compiler_version"
  for tool in c++ nvcc cmake; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf 'toolchain-%s=%s\n' "$tool" "$("$tool" --version | head -n 1)"
    fi
  done
) > "$manifest" || {
  echo "could not write the native build input manifest" >&2
  exit 1
}

queue_command() {
  mode=$1
  shift
  # The queue replaces artifact destinations with its private work path. The
  # child writes the complete build there; the queue publishes that directory
  # and installs it at $output on a cache hit or a fresh execution.
  set -- --kind cc \
    --state-root "$state_root" \
    --cwd "$cwd" \
    --source-root "$cwd" \
    --compiler "$compiler" \
    --linker "$compiler" \
    --version "$compiler_version" \
    --aux "$manifest" \
    --option "source_id=$source_id" \
    --option "backend=$backend" \
    --artifact "native=$output" \
    -- /bin/sh -c '
      set -eu
      destination=$1
      backend=$2
      shift 2
      elixir scripts/build_cached_native.exs --backend "$backend" "$@"
      mkdir -p "$destination"
      rm -rf "$destination/engines" "$destination/native-cache-receipts"
      cp -a engines "$destination/engines"
      cp -a native-cache-receipts "$destination/native-cache-receipts"
    ' _ "$output" "$backend" "$@"
  "$queue" -- "$mode" "$@"
}

if [ "${RIFF_NATIVE_BUILD_KEY_ONLY:-}" = 1 ]; then
  queue_command key "$@"
  exit $?
fi
queue_command run "$@"

[ -d "$output/engines" ] || {
  echo "shared compile queue did not install native engines at $output/engines" >&2
  exit 1
}
[ -d "$output/native-cache-receipts" ] || {
  echo "shared compile queue did not install native receipts at $output/native-cache-receipts" >&2
  exit 1
}

rm -rf engines native-cache-receipts
cp -a "$output/engines" engines
cp -a "$output/native-cache-receipts" native-cache-receipts
