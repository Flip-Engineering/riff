# Reproducible probe scripts and fixtures (#78)

This directory preserves the small scripts and fixtures behind the
ensemble's probe receipts, so results can be reproduced after the
gitignored `.probe-tmp/` workspace is cleaned. Large binaries (AppImages,
the Ardour demo bundle, rendered WAVs) are NOT retained; the committed
`docs/evidence/*.json` receipts record their hashes.

## Attribution

- `verify/midi_summary.py`, `verify/abc2js.js`: riff-daw-deepseek-20261025
  (dsh/deepseek-flash) — dependency-free SMF event summarizer; abcjs
  headless runner (Node vm + DOM shim) driving the vendored
  `web/vendor/abcjs-basic-min.js`.
- `verify/abcjs_midi_to_file.js`: riff-daw-conductor-kimi-20261025
  (omp/kimi-code/k3) — earlier abcjs variant (Array-wrapped return
  handling); superseded by `abc2js.js` for new work.
- `ardour/*.lua`: riff-daw-deepseek-20261025 — headless session load,
  gmsynth insertion, reopen and solo-stem snapshot scripts used with
  `ardour9-lua`.
- `ardour/build_session.py`, `patch_tempo_range.py`, `verify_timeline.py`,
  `verify_stems.py`, `licensing_inventory.py`: riff-daw-deepseek-20261025 —
  deterministic fixture adapter, saved-session tempo/range patch, event and
  stem receipts, bundle license inventory.
- `fixtures/arrangement-timing.json`: riff-daw-deepseek-20261025 — explicit
  timing adapter contract for the shared arrangement (bar-5/bar-8 receipt).
- `lmms/probe.mmp`: riff-daw-glm-20261025 (omp/zai/glm-5.3-flash) —
  hand-written 2-track LMMS project used for the headless render/stem
  receipts. Note the v1.3 schema: `<pattern>` is a child of `<track>`,
  not of `<instrumenttrack>`.
- `verify/mmp_generator.py`: riff-daw-glm-20261025 — per-voice SMFs +
  voice-map → v1.3-schema `.mmp` generator (contrasting built-in
  instruments, discrete tempo automation map on the Song tempo model,
  global 4/4); used for the musical workflow probe
  (`docs/evidence/lmms-arrangement-probe-20261025.json`).
- `fixtures/arrangement-v1.abc`, `fixtures/arrangement-v2.abc`,
  `fixtures/voice-map.json`: conductor — the shared arrangement for the
  root-review musical workflow probe (v2 is the meaningful edit of v1;
  voice-map demonstrates two voices sharing one instrument).

## Reproduction sketch

Converters: abcMIDI 5.07 (source build, commit `6582eb8`); abcjs 6.7.0
(vendored bundle, in this repo).

```sh
# ABC -> per-converter SMF
abc2midi probes/fixtures/arrangement-v1.abc -o out.mid -NGUI
node probes/verify/abc2js.js probes/fixtures/arrangement-v1.abc out-abcjs.mid
python3 probes/verify/midi_summary.py out.mid > out.summary.json
```

DAW render environments are documented in
`docs/daw-integration/ardour-headless-probe.md` (official 9.8.241 demo
nightly, extracted no-root) and `docs/daw-integration/daw-candidates.md`
§2.2 (LMMS 1.3.0-alpha.2 AppImage). Both require owned HOME/XDG dirs and
`JACK_NO_START_SERVER=1`; LMMS `dump`/`upgrade` additionally need a display
(Xvfb) and MMPZ input.


## Musical workflow (four voices, three tracks)

```sh
python3 probes/ardour/build_session.py \
  --template .probe-tmp/deepseek/upstream/ardour/1region.ardour \
  --abc probes/fixtures/arrangement-v1.abc \
  --voice-map probes/fixtures/voice-map.json \
  --timing probes/fixtures/arrangement-timing.json \
  --out .probe-tmp/deepseek/ardour-workflow/v1-build \
  --session-name arrangement-v1 \
  --abc2midi .probe-tmp/abcmidi/src/abc2midi

python3 probes/ardour/verify_timeline.py \
  --abc probes/fixtures/arrangement-v1.abc \
  --voice-map probes/fixtures/voice-map.json \
  --timing probes/fixtures/arrangement-timing.json \
  --midi-dir .probe-tmp/deepseek/ardour-workflow/v1-build/interchange/arrangement-v1/midifiles \
  --session-xml .probe-tmp/deepseek/ardour-workflow/v1/v1-final.ardour \
  --abc2midi .probe-tmp/abcmidi/src/abc2midi
```

See `docs/daw-integration/ardour-musical-workflow.md` for the full
save/reopen/render/stem/edit sequence and its limits.
