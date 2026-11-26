#!/bin/bash
# Runs inside ubuntu:24.04 for build_linux_engine.sh: compiles one engine with
# setup_engine.py --compile-only and packs the binary with its own files.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
packages="ca-certificates git python3 python3-venv build-essential libssl-dev file patchelf"
if [ "$BACKEND" = cuda ]; then
  # Ubuntu's CUDA 12.0 toolkit; its nvcc accepts g++-12 as the host compiler.
  packages="$packages nvidia-cuda-toolkit g++-12"
fi
apt-get -q update
apt-get -q -y --no-install-recommends install $packages

home=/build/workspace
mkdir -p "$home"
cp -a /src /build/src
cd /build/src
options=(--backend "$BACKEND" --compile-only --jobs "$JOBS")
if [ "$BACKEND" = cuda ]; then
  # One binary for the cards in use: SASS for 7.0 and 8.0, and PTX for 8.0 so a
  # newer card can run it. CUDA 13 cannot build for 7.0; 12.0 can.
  options+=(--cuda-arch "70-real;80")
  nvcc --version
fi
RIFF_HOME="$home" python3 setup_engine.py "${options[@]}"

binary=$(find "$home/engines" -path "*-$BACKEND/build/bin/audiocpp_cli" -type f | head -n 1)
test -x "$binary"
engine=$(basename "$(dirname "$(dirname "$(dirname "$binary")")")")
stage=/build/stage/engine
mkdir -p "$stage"
cp "$binary" "$stage/audiocpp_cli"
[ -f "$(dirname "$binary")/riff-sha256.json" ] && cp "$(dirname "$binary")/riff-sha256.json" "$stage/"
# Every shared library the build made (ggml and audio.cpp's own) goes beside the binary.
find "$(dirname "$binary")" "$(dirname "$(dirname "$binary")")" -maxdepth 2 -name "*.so*" -type f -exec cp -n {} "$stage/" \; || true

# The binary and its libraries find each other beside them ($ORIGIN), with no
# LD_LIBRARY_PATH at render time. Only the driver's libcuda.so.1 may stay unresolved here.
for file in "$stage"/audiocpp_cli "$stage"/*.so*; do
  [ -f "$file" ] && patchelf --set-rpath '$ORIGIN' "$file"
done
unresolved=$(env -u LD_LIBRARY_PATH ldd "$stage/audiocpp_cli" | awk '/not found/ {print $1}' | grep -v '^libcuda\.so' || true)
if [ -n "$unresolved" ]; then
  echo "The engine does not find these libraries by its RUNPATH: $unresolved" >&2
  exit 1
fi
needed=$(readelf -d "$stage/audiocpp_cli" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | sort -u | paste -sd, -)
echo "== NEEDED: $needed"
echo "== RUNPATH:"; readelf -d "$stage/audiocpp_cli" | grep -E 'RUNPATH|RPATH' || true
echo "== ldd:"; env -u LD_LIBRARY_PATH ldd "$stage/audiocpp_cli" || true
python3 - "$stage" "$BACKEND" "$engine" "$needed" "$VERSION" <<'PY'
import hashlib, json, pathlib, subprocess, sys
stage, backend, engine, needed, version = pathlib.Path(sys.argv[1]), *sys.argv[2:]
files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.iterdir()) if p.is_file()}
receipt = {"schema": "riff.prebuilt-engine.v1", "riff_version": version, "backend": backend,
    "engine": engine, "platform": "linux-x64", "built_on": "ubuntu:24.04",
    "needed": needed.split(",") if needed else [], "files": files}
if backend == "cuda":
    receipt["cuda_architectures"] = "70-real;80"
    receipt["cuda_toolkit"] = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", "nvidia-cuda-toolkit"], text=True)
    receipt["guest_packages"] = ["libcudart12", "libcublas12", "libcublaslt12"]
    receipt["guest_install"] = "apt-get install --no-install-recommends libcudart12 libcublas12"
(stage / "engine.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
(pathlib.Path("/out") / f"engine-{backend}.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
PY
asset="riff-engine-$BACKEND-linux-x64-v$VERSION.tar.gz"
tar -C /build/stage --owner=0 --group=0 --numeric-owner --sort=name -czf "/out/$asset" engine
chown "$HOST_UID:$HOST_GID" /out/*
