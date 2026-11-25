#!/usr/bin/env bash
# Builds one prebuilt Linux x86-64 engine of this checkout as a release asset:
#   dist/riff-engine-<backend>-linux-x64-v<VERSION>.tar.gz
# The build runs in an ubuntu:24.04 container with no GPU device: compiling needs no card.
#
#   cpu   the CPU engine.
#   cuda  the NVIDIA engine for compute capability 7.0 (SASS) and 8.0 (SASS and PTX),
#         compiled with the CUDA 12.0 toolkit Ubuntu 24.04 ships (nvidia-cuda-toolkit).
#         It is linked dynamically against that CUDA 12 runtime. The asset holds no
#         NVIDIA file: a guest installs libcudart12 and libcublas12 (which brings
#         libcublaslt12) from Ubuntu 24.04's multiverse archive, and the NVIDIA driver
#         on the computer provides libcuda.so.1.
#
# The docker engine is the caller's (DOCKER_HOST or DOCKER_CONTEXT); it must run the
# container on the host's own CPUs.
#
# RIFF_ENGINE_JOBS sets the compile jobs. Without it the job count is half of the CPUs
# that are idle now (nproc minus the 1-minute load), so the build leaves the host's
# other work its share; at least 1.
set -euo pipefail

backend=${1:?usage: build_linux_engine.sh cpu|cuda}
case "$backend" in cpu|cuda) ;; *) echo "backend must be cpu or cuda" >&2; exit 64 ;; esac
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(cat "$root/VERSION")
# ubuntu:24.04 (noble), pinned by digest so the toolkit and runtime packages match a known archive state.
image=${RIFF_ENGINE_IMAGE:-ubuntu:24.04@sha256:9c7f200c07e3d4a03e7249d2c16bf66a51120fb876aa2242cf31451412543289}
if [ -z "${RIFF_ENGINE_JOBS:-}" ]; then
  load=$(cut -d' ' -f1 /proc/loadavg)
  RIFF_ENGINE_JOBS=$(python3 -c "import os,sys; print(max(1, int((os.cpu_count() - float(sys.argv[1])) // 2)))" "$load")
fi
mkdir -p "$root/dist"
work=$(mktemp -d "${RUNNER_TEMP:-$root/dist}/engine-$backend.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

# The container sees a copy of the tracked source and writes only into $work/out.
git -C "$root" archive --format=tar HEAD | tar -x -C "$work" --one-top-level=src
mkdir "$work/out"

docker run --rm --pull=missing \
  --cpus="$RIFF_ENGINE_JOBS" \
  -e BACKEND="$backend" -e JOBS="$RIFF_ENGINE_JOBS" -e VERSION="$version" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$work/src:/src:ro" -v "$work/out:/out" \
  "$image" /bin/bash /src/scripts/build_linux_engine_inside.sh

asset="riff-engine-$backend-linux-x64-v$version.tar.gz"
mv "$work/out/$asset" "$root/dist/$asset"
cp "$work/out/engine-$backend.json" "$root/dist/riff-engine-$backend-linux-x64-v$version.json"
(cd "$root/dist" && sha256sum "$asset" > "$asset.sha256")
cat "$root/dist/$asset.sha256"
