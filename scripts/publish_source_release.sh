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
(cd dist && sha256sum --check SHA256SUMS)

notes="$RUNNER_TEMP/riff-source-release-notes.md"
{
  printf '## Qualification scope\n\n'
  printf 'Source archive qualified on self-hosted Linux x86-64. '
  printf 'This release does not establish Apple Silicon/Metal, CUDA device inference, or desktop-package qualification. '
  printf 'Compatible desktop payloads require their separate platform build and acceptance workflow.\n\n'
  printf 'Source commit: `%s`\n\n' "$GITHUB_SHA"
  printf 'Linux qualification and publication: https://github.com/%s/actions/runs/%s\n\n' "$GITHUB_REPOSITORY" "${GITHUB_RUN_ID:?}"
  cat .github/release-notes.md
} > "$notes"

# Explicit asset names prevent accidental publication of an unrelated payload.
# --verify-tag requires the reviewed version tag; an existing release fails
# instead of replacing its assets or moving the immutable tag.
gh release create "$GITHUB_REF_NAME" "dist/$archive" dist/install.py dist/SHA256SUMS \
  --repo "$GITHUB_REPOSITORY" --verify-tag --notes-file "$notes" \
  --title "Riff $GITHUB_REF_NAME (Linux-qualified source)"
