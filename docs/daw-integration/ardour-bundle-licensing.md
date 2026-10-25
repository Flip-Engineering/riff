# Ardour 9.8.241 bundle licensing inventory (#78)

Author: riff-daw-deepseek-20261025. Status: evidence for root/legal review,
2026-10-25. This is an inventory of the **actual extracted nightly demo
bundle**, not a conclusion that redistribution is cleared. Separate-process
execution is **not** blanket clearance.

## 1. Artifact

- URL: `https://nightly.ardour.org/d/A_Linux_x86_64_FREE/Ardour-9.8.241-demo-x86_64.run`
- SHA1: `0007d1664fe92c0d982c8c5e5bfea1b07dc1a98d`
- Extracted path: `.probe-tmp/deepseek/ardour-bin/ardour-root/Ardour_x86_64-9.8.241-demo`
- Ardour source tag: `9.8`, commit `059c95883d8158b03f632056ba36e5c7de91ef28`
- Binary string: "under the terms of the GNU General Public License (GPL) v2.0 or any later version"; the source-tree `COPYING` has the plugin clarification. The demo bundle itself has **no top-level `COPYING` file**; redistribution must carry the applicable notices.

The render path in `ardour-musical-workflow.md` uses only:

1. Ardour core,
2. the bundled `gmsynth` LV2,
3. `GeneralUser_LV2.sf2`.

## 2. LV2 inventory

34 directories under `lib/LV2` (77 MB total). The inventory script is
`probes/ardour/licensing_inventory.py`; machine-readable output is
`docs/evidence/ardour-bundle-licensing-20261025.json`.

| category | count | directories |
| --- | ---: | --- |
| GPL-family (TTL says GPL; version not stated) | 8 | `a-comp`, `a-delay`, `a-eq`, `a-exp`, `a-fluidsynth`, `a-reverb`, `gmsynth`, `reasonablesynth` |
| ISC (LV2 extension/spec bundles) | 15 | `atom`, `core`, `data-access`, `dynmanifest`, `event`, `instance-access`, `midi`, `patch`, `port-groups`, `presets`, `state`, `ui`, `units`, `uri-map`, `urid` |
| no explicit `doap:license` in TTL (spec bundles) | 10 | `buf-size`, `log`, `morph`, `options`, `parameters`, `port-props`, `resize-port`, `schemas`, `time`, `worker` |
| proprietary / license-gated, exclude | 1 | `Harrison.lv2` (many XT plugins; license interface; `UNLICENSED` and demo-mode strings; one `XT_GV` TTL claims GPL but the directory is mixed) |

Only the eight GPL-family dirs contain audio plugins used by Ardour here. The
ISC/no-explicit dirs are LV2 extension/spec bundles; they are not on the render
path. If the complete demo bundle is redistributed, every directory and the
media assets still need a distribution review.

## 3. gmsynth

- Path: `lib/LV2/gmsynth.lv2/`
- `gmsynth.ttl`: `doap:license <http://usefulinc.com/doap/licenses/gpl>`
- `gmsynth.so` sha256 `0ff559f0d732b951c4edd08d549c0ab1dc700e7cd881a7adda191ddfe332d7bd`
- `gmsynth.ttl` sha256 `cea126a1a60d701aa268e51e49969e19f261ade1cbc44316d36525c312b65e4c`
- The TTL does **not** state a GPL version; it must be resolved against the
  source/Ardour licensing notice. Treat as GPL-family pending legal review.
- The plugin statically/otherwise links a Fluidsynth-derived synth and loads
  the SF2 below.

## 4. GeneralUser GS SoundFont

- File: `lib/LV2/gmsynth.lv2/GeneralUser_LV2.sf2` (32,322,864 bytes)
- sha256 `c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce`
- Embedded header: **GeneralUser GS 2.0.2**, S. Christian Collins,
  **License v2.0** (custom, not OSI).
- Key terms, verbatim from the SF2 string table:
  - "You may use GeneralUser GS without restriction for your own music
    creation, private or commercial."
  - "Please feel free to use it in your software projects, and to modify the
    SoundFont bank or its packaging to suit your needs."
  - "Some samples were taken from other banks freely ... I cannot be 100% sure
    where all of the samples originated ... This uncertainty may concern you
    if you intend to use GeneralUser GS in a commercial software product."
- **Redistribution clearance is not established.** The license permits use in
  software projects and packaging modification, but the author explicitly
  warns about sample provenance and commercial use. Legal review is required
  before redistributing this SF2 (or a bundle containing it). This probe only
  executes the extracted bundle locally.

## 5. Other bundled plugins

- `a-fluidsynth.lv2`, `reasonablesynth.lv2`: GPL-family TTL; not used by this
  render path. Hashes recorded in the evidence JSON.
- `a-comp`, `a-delay`, `a-eq`, `a-exp`, `a-reverb`: GPL-family TTL; not used.
- `Harrison.lv2`: proprietary/license-gated. Strings include "UNLICENSED",
  "Continue in Demo Mode", license-key UI and a `harrisonconsoles.com/lv2/
  license#interface` extension. It must be excluded from any redistributed
  render path unless a separate Harrison license is obtained. Do not infer
  open-source status from the one GPL `XT_GV` TTL.

## 6. Conclusion

- Local probe execution: permissible for this research receipt under the
  existing separate-process approach, but this is not a redistribution
  clearance.
- If Riff ships or bundles an Ardour-based renderer, the **gmsynth GPL-family
  terms and the GeneralUser GS License v2.0 / sample-provenance question are
  hard gates** for legal review.
- Do not redistribute `Harrison.lv2` or the full demo bundle without a
  complete per-artifact review.
- The safe short-term integration shape is to depend on a separately installed
  Ardour + gmsynth + GeneralUser environment, not to re-package the demo
  bundle into Riff artifacts.

Related: `ardour-musical-workflow.md`, `ardour-headless-probe.md`,
`docs/evidence/ardour-bundle-licensing-20261025.json`, issue #78.
