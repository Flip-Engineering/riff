# DAW production integration decisions (#78, 2026-10-26)

Determinations made from source and recorded evidence, per the root
instruction to prepare the remaining DAW integration decisions as
engineering work rather than leaving ordinary implementation choices as
permission gates. Each determination names its source of truth. The one
remaining user/legal policy gate is stated explicitly in §3.

## 1. Production provisioning route — pinned source build (determined)

**Determination:** the maintained production route is the pinned Ardour
9.8 **source build** (runtime-record `kind: "source-build"`). It is the
only kind with qualification evidence: the Q1–Q10 gate battery passed on
exactly this kind (receipts under `docs/evidence/ardour-qualification-20261026/`
and the gap register in this directory). `reviewed-full` remains an
admissible kind in `daw_runtime.APPROVED_KINDS`, but it has zero recorded
battery evidence; it may not be used in production until the same battery
passes on it, and the two kinds must not run side by side (adapter
contract §5.2). Demo builds are refused by construction:
`daw_runtime.probe()` rejects any kind outside `source-build` /
`reviewed-full`, and the demo is never a production runtime.

**Maintained build/provisioning chain (all in-repo):**

1. Build environment: `tools/daw/ardour-9.8/flake.nix` — a Nix dev shell
   supplying every mandatory dependency from the build preflight, so the
   pinned `waf` build runs without host package changes.
2. Pinned source: Ardour tag `9.8`, commit
   `22ed8656c2533e325322ff11831448e5123e0d4b` (tag object
   `059c95883d8158b03f632056ba36e5c7de91ef28`), with exactly one local
   patch (`tools/daw/ardour-9.8/patches/`, luasession stdout fix; the
   provisioner records patch hashes and dirty files in the build
   receipt).
3. Provisioning: `tools/daw/ardour-9.8/provision_ardour_runtime.py`
   stages the gmsynth payload into the owned prefix (SF2 verified
   byte-for-byte, §3), dereferences the session-utils symlinks into real
   hashed wrapper files, hashes all required artifacts, and writes both
   the build receipt (`riff.daw.ardour-build.v1`) and the runtime record.
4. Acceptance: `tools/daw/qualify-ardour.sh` runs the Q1–Q10 battery
   against the installed prefix and writes the
   `riff.daw.ardour-qualification.v1` receipt; every gate fails closed.

**Scheduling constraint (operator-directed):** builds run on the shared
compilation queue at normal background priority (`nice`/`ionice` or the
managed build queue), never as ad-hoc foreground jobs, and never while
competing with higher-priority production work. The qualification runs
recorded to date used `nice -n 19 ionice -c3` sequentially.

## 2. Runtime record and attestation owner (identified)

- The runtime record is `<data>/ardour-runtime.json`
  (`daw_runtime.RECORD`), consumed by `daw_runtime.probe()` /
  `require()` on every compose/render. It is written **only** by the
  provisioning step (`provision_ardour_runtime.py`), never by
  `daw_runtime` itself (module docstring, adapter contract §5.3).
- **Owner:** the installation administrator performing the
  root-coordinated provisioning step for that installation. There is no
  separate attestation service or signer in the codebase, and none is
  claimed.
- **Attestation mechanism:** hash-manifest integrity, verified on every
  probe — approved version/commit/kind, existing prefix, SHA-256 of the
  three required binaries plus the `abc2midi` converter, the session
  template, and the SF2; symlinked records and symlinked runtime files
  are refused atomically (`O_NOFOLLOW`, GLM hardening at `8b864f5`). A
  missing, malformed, or mismatched record fails closed with
  `RuntimeUnavailable`; there is no demo or PATH fallback.
- The build receipt (`riff.daw.ardour-build.v1`: pin, patch hashes,
  dirty-file list, artifact hashes) is the retained provenance for what
  the provisioner installed; the provisioner refuses a source checkout
  that does not describe as exactly `9.8` at the approved commit.

If cryptographic signing of runtime records is ever wanted, that is a
new user policy decision (no signing code exists today); the current
hash-manifest-plus-provisioner-owner model is the implemented and
qualified behavior and needs no further permission to operate.

## 3. SoundFont redistribution — current posture preserved (one legal gate)

Verified from the primary source: the license strings embedded in the
retained `GeneralUser_LV2.sf2` itself (sha256
`c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce`,
GeneralUser GS 2.0.2, S. Christian Collins, "License v2.0"), read
directly from the file on 2026-10-26 and matching
`docs/evidence/generaluser-sf2-verification-20261026.json`:

- "You may use GeneralUser GS without restriction for your own music
  creation, private or commercial… Please feel free to use it in your
  software projects, and to modify the SoundFont bank or its packaging
  to suit your needs."
- The contained samples inherit their original usage rights (full use
  in music production, including profit from recordings), **but** the
  author explicitly states he "cannot be 100% sure where all of the
  samples originated" and that "this uncertainty may concern you if you
  intend to use GeneralUser GS in a commercial software product."

**Determination (engineering posture, effective now):** Riff does not
bundle or redistribute the SF2, the Ardour demo bundle, or any LV2
payload. Verified: the published `riff-v0.6.33.tar.gz` source release
contains the DAW Python sources and **no** `.sf2`, LV2, or Ardour binary
artifacts. The instrument is separately provisioned per installation:
the provisioner stages the SF2 from a locally retained bundle into the
owned runtime prefix and hash-verifies it before any render. Local
execution of the separately provisioned payload is permitted by the
license text above. `Harrison.lv2` is proprietary/license-gated and is
excluded from the render path entirely (licensing inventory, this
directory).

**The one remaining user/legal policy decision:** whether Riff may ever
*redistribute* the SF2 (or a bundle containing it) in its own artifacts.
Alternatives: (a) keep separate provisioning (current determination —
no legal exposure added, works today); (b) obtain a legal review /
clearance of the GeneralUser GS sample-provenance question, then bundle;
(c) substitute a sound bank with unambiguous redistribution terms, which
requires a new instrument qualification (program/centroid/render
receipts would need to be re-run). Until the user/legal owner chooses
(b) or (c), (a) stands and no uncleared third-party asset is bundled.

## 4. Implemented ABC subset (from source)

Source of truth: `ardour_session.py` and `daw_operations.py`. This is
the exact implemented subset — anything outside it is refused before
any file side effect (`ContractError`), not silently approximated:

- Input: one multi-voice ABC document plus an explicit `voice_map` plus
  an explicit `timing` manifest. `daw.compose` accepts exactly one of
  `abc` or `session_input`; ABC composition without both map and timing
  manifest is rejected.
- Voices: `V:` declarations are required; the document is split at
  `V:` lines (first block declares, a repeated `V:` opens that voice's
  body). ABC voices and voice-map voices must match **exactly**
  (`check_voice_sets`); a declared voice with no body is refused.
- The project stores performance events (SMF), not notation: quoted
  chord symbols are stripped from the performance copy
  (`strip_chords`). No lossless-ABC claim is made.
- Voice map is authoritative: every voice needs an `instrument`
  (non-empty) and a `program_raw` integer in 0–127. Voices sharing an
  instrument group onto **one track** (e.g. PianoRH + PianoLH → one
  Piano track, two channels, one program per channel). Track naming:
  an explicit `track` in the map wins (conflicting names within a group
  are refused); otherwise the longest common voice-ID prefix of ≥ 3
  characters, else the first voice ID. Track names must be unique.
- Timing is **never inferred from ABC text**. The manifest must carry
  tempos and meters starting at quarter 0 and strictly increasing, a
  positive `end_quarters`, and `quarter_note_ticks` equal to the SMF
  division (480; resampling is out of contract). Notes are
  bounds-checked (integral ticks, positive durations, pitch/velocity/
  channel ranges, nothing ending past the manifest end).
- Conversion is per track through the pinned converter
  (`abc2midi 5.07 6582eb8`, identity recorded in the runtime record);
  one program change per channel is injected at tick 0 from the map, in
  sorted channel order, and channel count must equal program count.
- Sessions are shaped only from the qualified 3001 template (route A);
  any other template version is refused rather than shaped.

Not in the subset (refused or absent by design): tempo/meter inference
from ABC headers, lossless notation round-trip, converters other than
the pinned `abc2midi`, and the unqualified native-7004 template route.

## 5. Stem behavior (accurate, from source)

Source of truth: `daw_runner.render()` and
`probes/ardour/save_stems.lua`.

- Stems are **per-track solo snapshots**, not per-voice renders and not
  the Ardour GUI stem dialog: for each track, `save_stems.lua` solos
  exactly that track, saves a snapshot (`stem-<n>`), and
  `ardour9-export` renders that snapshot to `export/stem-<n>.wav`.
- One stem per **track**: a shared-instrument track (both piano hands
  on one instrument/track) renders exactly one stem. The render
  manifest names tracks, never voices.
- Stem evidence fails closed: the snapshot log must show a complete,
  ordered, duplicate-free `save=true` stem sequence or the render is
  refused before anything is published to the library.
- On the qualified fixture, the solo stems sum to the mix within 1 LSB
  (adapter contract §5.4 D; mix/stem hashes recorded in the Q4–Q6
  receipts). Stems are on by default (`daw.stems` defaults to true) and
  can be disabled per render.

## 6. What remains

- PR #90 stays draft for root's consolidation review; this document
  does not mark the feature ready or merged.
- Root/user decisions genuinely remaining: the §3 redistribution
  policy (only if bundling is ever desired), and consolidation timing.
  The provisioning route, record ownership, ABC subset, and stem
  behavior above are determined from source and require no further
  permission to operate as implemented.
- A single-invocation run of `tools/daw/qualify-ardour.sh` against a
  production prefix will produce the unified Q1–Q10 receipt when a
  production installation is provisioned; the gate-by-gate receipts
  already recorded are the current evidence.
