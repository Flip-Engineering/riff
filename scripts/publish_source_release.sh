#!/usr/bin/env bash
# Called only after release.yml's Linux source qualification succeeds.
set -euo pipefail

test "${GITHUB_EVENT_NAME:?}" = push
test "${GITHUB_REPOSITORY:?}" = Flip-Engineering/riff
test "${GITHUB_REF_TYPE:?}" = tag
test "${GITHUB_REF:?}" = "refs/tags/${GITHUB_REF_NAME:?}"
test "$GITHUB_REF_NAME" = "v$(cat VERSION)"
test "$(git rev-parse HEAD)" = "${GITHUB_SHA:?}"
test "$(git rev-parse "$GITHUB_REF^{commit}")" = "$GITHUB_SHA"
test "${RUNNER_ENVIRONMENT:?}" = self-hosted
test "$(uname -s):$(uname -m)" = Linux:x86_64
test -d "${RUNNER_TEMP:?}"

archive="riff-$GITHUB_REF_NAME.tar.gz"
test -f "dist/$archive"
test -f dist/install.py
engines=()
for backend in cpu cuda; do
  engine="dist/riff-engine-$backend-linux-x64-$GITHUB_REF_NAME.tar.gz"
  test -f "$engine"
  grep -q "  ${engine#dist/}\$" dist/SHA256SUMS || {
    echo "${engine#dist/} is not in SHA256SUMS; refusing publication." >&2
    exit 1
  }
  engines+=("$engine")
done
(cd dist && sha256sum --check SHA256SUMS)

notes="$RUNNER_TEMP/riff-source-release-notes.md"
{
  printf '## Qualification scope\n\n'
  printf 'Source archive qualified on self-hosted Linux x86-64. '
  printf 'This release does not establish Apple Silicon/Metal, CUDA device inference, or desktop-package qualification. '
  printf 'Compatible desktop payloads require their separate platform build and acceptance workflow.\n\n'
  printf 'Prebuilt Linux x86-64 engines: riff-engine-cpu and riff-engine-cuda (compute capability 7.0 and 8.0, '
  printf 'linked against the CUDA 12 runtime of Ubuntu 24.04). They hold only the engine and its receipts, no NVIDIA file; '
  printf 'a CUDA guest installs them from Ubuntu with `apt-get install --no-install-recommends libcudart12 libcublas12` (recommends would bring a driver library that does not match the computer'"'"'s driver). Activate one with '
  printf '`setup_engine.py --backend <cpu|cuda> --prebuilt <asset> --prebuilt-sha256 <digest>`.\n\n'
  printf 'Source commit: `%s`\n\n' "$GITHUB_SHA"
  printf 'Linux qualification and publication: https://github.com/%s/actions/runs/%s\n\n' "$GITHUB_REPOSITORY" "${GITHUB_RUN_ID:?}"
  cat .github/release-notes.md
} > "$notes"

# Explicit asset names prevent accidental publication of an unrelated payload.
# --verify-tag requires the reviewed version tag; an existing release fails
# instead of replacing its assets or moving the immutable tag.
# Check the actual remote immediately before publication too: --verify-tag only
# establishes existence, and the event checkout can outlive a moved remote tag.
remote_tags=$(git ls-remote --exit-code origin "$GITHUB_REF" "$GITHUB_REF^{}")
remote_commit=$(printf '%s\n' "$remote_tags" | python3 -c '
import re, sys
ref = sys.argv[1]
refs = {}
for line in sys.stdin:
    fields = line.rstrip("\n").split("\t")
    if (len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[0])
            or fields[1] not in (ref, ref + "^{}") or fields[1] in refs):
        raise SystemExit("Cannot authenticate the remote version tag")
    refs[fields[1]] = fields[0]
if ref not in refs:
    raise SystemExit("The remote version tag is missing")
print(refs.get(ref + "^{}", refs[ref]))
' "$GITHUB_REF")
test "$remote_commit" = "$GITHUB_SHA" || {
  echo 'The remote version tag no longer selects the validated source; refusing publication.' >&2
  exit 1
}
gh release create "$GITHUB_REF_NAME" "dist/$archive" dist/install.py "${engines[@]}" dist/SHA256SUMS \
  --repo "$GITHUB_REPOSITORY" --verify-tag --notes-file "$notes" \
  --title "Riff $GITHUB_REF_NAME (Linux-qualified source)"
