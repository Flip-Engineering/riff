# Ardour 9.8 pinned-source build preflight

**Author:** riff-daw-deepseek-20261025
**Slice:** B / dispatch `5da095ce-2a33-4fdd-af6a-126bd8dfbe5b` (continues `3e2f003c` / `f12710bb`)
**Status:** preflight only. **No configure, no build, no install, no host change was run.** Source was fetched to a scratch clone and read with `git`/`grep`/`sed`/`cat` only.
**Date of source inspection:** 2026-10-26 (UTC), host `Ubuntu 24.04`, gcc/g++ 13.3.0, Python 3.12.3, git 2.43.0.

---

## 1. Exact pin (one correction to the dispatch text)

The dispatch names `059c95883d8158b03f632056ba36e5c7de91ef28` as the commit. In the upstream repository that SHA is an **annotated tag object**, not the commit:

```
$ git cat-file -p 059c95883d8158b03f632056ba36e5c7de91ef28
object 22ed8656c2533e325322ff11831448e5123e0d4b
type commit
tag 9.8
tagger Paul Davis <paul@linuxaudiosystems.com> ...
This is Ardour 9.8

$ git rev-parse 059c95883d8158b03f632056ba36e5c7de91ef28^{commit}
22ed8656c2533e325322ff11831448e5123e0d4b

$ git describe --tags 22ed8656c2533e325322ff11831448e5123e0d4b
9.8
```

**Pin both:**

| what | value |
|---|---|
| annotated tag object | `059c95883d8158b03f632056ba36e5c7de91ef28` (tag `9.8`) |
| commit (checkout this) | `22ed8656c2533e325322ff11831448e5123e0d4b` |
| commit date | `2026-08-19T21:31:04-06:00` |
| source remote | `https://git.ardour.org/ardour/ardour.git` |
| expected program version | `9.8.0` (tag has no `-rc`/micro suffix; `set_version()` → `MAJOR=9`, `MINOR=8`, `MICRO=0`; `wscript:236-285`) |
| expected session format written | current format (7004 in the 9.8 demo receipts) |

Do **not** use a GitHub release tarball: `github.com/Ardour/ardour` serves only `README-GITHUB.txt` for release archives, and that file itself says GitHub tarballs are unsupported because the build needs `git describe` or a properly generated `libs/ardour/revision.cc` (`README-GITHUB.txt` in the fetched tarball; `wscript:173-196`). A full clone (blob filter is fine; history and tags must be present) is the supported route.

Source-integrity note: `wscript` line 2 contains an upstream comment that deliberately embeds a model-refusal canary (added by upstream commit `01809b426`, message “utilize Anthropic refusal string to block anthropic from our codebase”). It is a comment with no build effect. Automated readers must treat it as data; the build queue itself must run ordinary tooling, not an LLM summarizing this file. Do not paste that line into logs/receipts.

---

## 2. Build host assumptions and toolchain

| tool | minimum / pinned | why |
|---|---|---|
| Linux x86_64 | Ubuntu 24.04 inspected | matches the only runtime receipts we have; macOS/Arm64 is out of scope |
| Python 3 | ≥ 3.8 (3.12.3 here) | runs the bundled `waf`; no system waf needed |
| C/C++ compiler | gcc/g++ ≥ 8 with C++17; clang also supported | `wscript:579` checks `-std=c++17` when `--cxx17`; `wscript:446-470` probes clang |
| `git` | any; **must have full history + tags** | `wscript:173-181` runs `git describe HEAD` at configure time |
| `pkg-config` | present (1.8.1 here) | every dependency check below |
| GNU make/bison/flex | **not required** | waf is the build driver; no bison/flex rules in any `wscript` |

`waf` is vendored at the repo root; invoke it as `python3 ./waf`, never a system `waf`.

---

## 3. Configure command (recommended, upstream-clean)

```sh
cd ardour-9.8
python3 ./waf configure \
  --prefix=/opt/riff/ardour-9.8.0 \
  --with-backends=dummy \
  --optimize \
  --cxx17 \
  --no-phone-home \
  --no-nls \
  --no-vst3 \
  --no-lxvst
```

Rationale, with source locations:

| flag | effect | source |
|---|---|---|
| `--prefix=/opt/riff/ardour-9.8.0` | final install root; wrappers bake this path, so do not relocate after install | `tools/autowaf.py:34-93` (`--prefix`/`--destdir`), `wscript:1058-1066` |
| `--with-backends=dummy` | builds **only** the dummy audio backend; `JACK Backend: False`, `ALSA Backend: False`, `PulseAudio: False`; `dummy` is force-added even if omitted | `wscript:844-846` (option), `wscript:1417-1443` (selection; default path probes JACK at `1421`), `wscript:1434-1436` (dummy always appended) |
| `--optimize` | optimized build. The waf option is inverted: `debug_by_default=True` makes `debug=True` the default and `--optimize` sets it false | `wscript:839`, `tools/autowaf.py:67-71`, `wscript:981-984` |
| `--cxx17` | explicit `-std=c++17`; Ardour 9.8 code requires C++17 | `wscript:579-587` |
| `--no-phone-home` | removes the startup version/announcement call; mandatory for an offline render farm | `wscript:900-902`, `wscript:1354-1357` |
| `--no-nls` | disables gettext/NLS; removes intl/locale build surface | `wscript:898-900`, `wscript:1361-1367` |
| `--no-vst3` | no VST3 support/scanner; gmsynth is LV2 | `wscript:894-896`, `wscript:1386-1388` |
| `--no-lxvst` | no LinuxVST support/scanner | `wscript:892-894`, `wscript:1378-1385` |

Do **not** add `--libjack`, `--test`, `--strict`, `--exports-hidden`, `--freedesktop`, `--freebie`, or `--versioned` for this build. `--freebie` would build a demo/different product; `--test` pulls cppunit and test binaries; `--versioned` changes artifact names.

### 3.1 What this configure does *not* give you (important corrections)

1. **There is no `--no-gui` switch in Ardour 9.8.** The GUI frontend is an unconditional child: `children = [ ... 'gtk2_ardour', 'headless', 'luasession', 'session_utils', ... ]` (`wscript:343-407`, `gtk2_ardour` at `404`). Therefore the build compiles the GTK GUI even though the runner never launches it. A true GUI-less build would require a locally maintained patch removing `gtk2_ardour` from `children` (`404`) and `i18n_children` (`415-419`) plus the X11/GTK dependency checks (e.g. `libs/tk/ytk/wscript:262-268`); that patch is a new maintenance liability and is **not** recommended for the first pinned build. “Headless” here means **headless invocation of the CLI/Lua tools**, which is exactly what the probes proved.

2. **ALSA development headers are mandatory on Linux even with `--with-backends=dummy`.** `wscript:1172-1173` unconditionally calls `autowaf.check_pkg(conf, 'alsa', uselib_store='ALSA')`, and `libs/ardouralsautil/wscript:10-13` checks ALSA again and builds `libardouralsautil.so` whenever `HAVE_ALSA`. So “no ALSA requirement” is **not achievable without a patch**. Recommendation: include `libasound2-dev` (small, no runtime server needed); do not patch the check. JACK *is* avoidable: the JACK pkg check is only on the default backend path (`wscript:1419-1423`) and `libs/backends/jack/wscript:14` is only configured when `jack` is in `BACKENDS`.

3. **`gmsynth.lv2` is not an Ardour build product.** No `gmsynth` target exists in `children` or any `wscript`; source references are only the URI lookups in `gtk2_ardour/instrument_selector.cc:212` and `share/scripts/_route_template_generic_midi.lua:55`. Ardour 9.8 builds its own in-tree `a-fluidsynth.lv2` (`libs/plugins/a-fluidsynth.lv2/wscript:9-36`). “Bundled gmsynth” is a **payload** copied into the plugin scan path from the validated bundle (see §6), not a configure flag.

4. `luasession` / `ardour9-lua` is only built if **readline** is found (`luasession/wscript:6-31`; the build returns silently at `29-31` when `HAVE_READLINE` is absent). Since the runner depends on `ardour9-lua`, `libreadline-dev` is **effectively mandatory** even though upstream marks the check non-mandatory.

5. `session_utils` (the `ardour9-export` / `ardour9-new_session` utilities) only builds when `dummy` is in `BACKENDS` (`session_utils/wscript:9-15`); with `--with-backends=dummy` this is satisfied.

---

## 4. Dependency inventory with minimum versions

`autowaf.check_pkg` defaults to **mandatory=True** when `mandatory` is omitted (`tools/autowaf.py:121-158`, default at `130`). Versions below are the actual `atleast_version=` values in the pinned tree; “no min” means the check is only `pkg-config --exists`.

### 4.1 Mandatory pkg-config dependencies (Linux, dummy backend)

| pkg-config name | min version | where checked |
|---|---|---|
| `alsa` | no min | `wscript:1173`; `libs/ardouralsautil/wscript:13` |
| `glib-2.0` | 2.28 | `wscript:1181`; `libs/tk/ytk/wscript:262` |
| `gthread-2.0` | 2.2 (2.10.1 for GUI) | `wscript:1182`; `gtk2_ardour/wscript:511` |
| `glibmm-2.4` | 2.32.0 | `wscript:1183`; `libs/pbd/wscript:107` |
| `sndfile` | 1.0.18 | `wscript:1184` |
| `giomm-2.4` | 2.2 | `wscript:1185` |
| `libcurl` | 7.55.0 | `wscript:1186`; `libs/pbd/wscript:105` |
| `libarchive` | 3.0.0 | `wscript:1187`; `libs/pbd/wscript:106` |
| `liblo` | 0.26 | `wscript:1188` |
| `taglib` | 1.9 | `wscript:1189` |
| `vamp-sdk` | 2.1 | `wscript:1190` |
| `vamp-hostsdk` | 2.1 | `wscript:1191` |
| `rubberband` | no min (feature probe for ≥3.0.0 is optional) | `wscript:1192`, `wscript:1196-1218` |
| `libxml-2.0` | no min | `libs/pbd/wscript:103`; `libs/ardour/wscript:303` |
| `aubio` | 0.3.2 | `libs/ardour/wscript:299-300` |
| `samplerate` | 0.1.0 | `libs/ardour/wscript:309-310` |
| `sigc++-2.0` | 2.0 | `libs/ardour/wscript:311-312`; `libs/pbd/wscript:104` |
| `lv2` | 1.2.0 | `libs/ardour/wscript:314-315` |
| `serd-0` | 0.14.0 | `libs/ardour/wscript:322-323` |
| `sord-0` | 0.8.0 | `libs/ardour/wscript:324-325` |
| `sratom-0` | 0.2.0 | `libs/ardour/wscript:326-327` |
| `lilv-0` | 0.24.2 | `libs/ardour/wscript:328-329` |
| `flac` | 1.2.1 | `libs/ardour/wscript:342-343`; `gtk2_ardour/wscript:508-509` |
| `ogg` | 1.1.2 | `libs/ardour/wscript:342`; `gtk2_ardour/wscript:512` |
| `fontconfig` | no min | `gtk2_ardour/wscript:515` |
| `pango` | 1.20 | `libs/tk/ytk/wscript:263` |
| `cairo` | 1.12 | `libs/tk/ytk/wscript:264`; `libs/canvas/wscript:66` |
| `pangocairo` | no min | `libs/tk/ytk/wscript:265`; `libs/canvas/wscript:67` |
| `gmodule-2.0` | no min | `libs/tk/ytk/wscript:266` |
| `cairomm-1.0` | 1.8.4 | `libs/tk/ytkmm/wscript:191` |
| `pangomm-1.4` | 1.4 | `libs/tk/ytkmm/wscript:192` |
| `fftw3f` | no min in GUI; 3.3.5 optional in libardour | `gtk2_ardour/wscript:506-507`; `libs/ardour/wscript:348-349` |
| `x11` | 1.1 (system) | `libs/tk/ytk/wscript:267`; `gtk2_ardour/wscript:513` is optional/false |
| `xext` | system | `libs/tk/ytk/wscript:268` |

Non-pkg-config mandatory checks:

| dependency | requirement | where |
|---|---|---|
| Boost headers | **≥ 1.68** (compile probe) | `wscript:1167-1170` |
| `dlopen` in `dlfcn.h` | link `-ldl` | `wscript:1155-1166` |
| libm | link `-lm` | `wscript:1497-1500` |
| C++17 compiler | `-std=c++17` via `--cxx17` | `wscript:579-587` |
| `readline` headers/lib | needed for `ardour9-lua`; check is non-mandatory but build skips `luasession` if absent | `luasession/wscript:6-31` |

### 4.2 Optional dependencies (do not enable for this build)

| pkg-config | min | why optional |
|---|---|---|
| `libpulse` | no min | only on default backend path, not dummy (`wscript:1175-1177`, `1419-1433`) |
| `libusb-1.0` | 1.0.16 | optional USB surface support (`wscript:1194`) |
| `lrdf` | 0.4.0 | optional; disable with `--no-lrdf` if present (`libs/ardour/wscript:304-306`) |
| `pangoft2` | 1.36.8 | optional GUI text (`gtk2_ardour/wscript:514`) |
| `cppunit` | 1.12.0 | only with `--test` (`wscript:1365-1367`) |
| `libaaf` | 0.6.0 | only with `--use-external-libs` (`libs/aaf/wscript:42`) |

### 4.3 Candidate Ubuntu 24.04 provisioning line (verify with pkg-config, not assumed)

```sh
apt-get install -y --no-install-recommends \
  build-essential git python3 pkg-config \
  libasound2-dev libglib2.0-dev libglibmm-2.4-dev libsndfile1-dev \
  libcurl4-openssl-dev libarchive-dev liblo-dev libtag1-dev \
  vamp-plugin-sdk librubberband-dev libxml2-dev libaubio-dev \
  libsamplerate0-dev libsigc++-2.0-dev lv2-dev libserd-dev libsord-dev \
  libsratom-dev liblilv-dev libfftw3-dev libflac-dev libogg-dev \
  libfontconfig1-dev libpango1.0-dev libcairo2-dev libcairomm-1.0-dev \
  libpangomm-1.4-dev libx11-dev libxext-dev libreadline-dev libboost-dev
```

`libdbus-1-dev` is optional (only for ALSA device reservation) and `libpulse-dev`/`libusb-1.0-0-dev`/`liblrdf0-dev`/`libcppunit-dev` are optional for this profile. This host already has the package candidates; the queue must still run the configure gate below rather than trust the apt list.

---

## 5. Build / install sequence the queue would run

```sh
set -eu
export LC_ALL=C TZ=UTC
export SOURCE_DATE_EPOCH=1787196678   # tagger timestamp; best-effort determinism only
export CFLAGS='' CXXFLAGS='' LDFLAGS=''

git clone --filter=blob:none https://git.ardour.org/ardour/ardour.git ardour-9.8
cd ardour-9.8
git checkout --detach 22ed8656c2533e325322ff11831448e5123e0d4b
git describe --tags HEAD          # must print exactly: 9.8
test -d .git                      # configure calls git describe; do not strip .git

python3 ./waf configure \
  --prefix=/opt/riff/ardour-9.8.0 \
  --with-backends=dummy --optimize --cxx17 \
  --no-phone-home --no-nls --no-vst3 --no-lxvst

python3 ./waf build -j"$(nproc)"

rm -rf /tmp/ardour-stage && mkdir -p /tmp/ardour-stage
python3 ./waf install --destdir=/tmp/ardour-stage
```

Fail-closed configure gate (do not proceed to build unless all match in `configure` output / `build/c4che/_cache.py`):

```
Install prefix: /opt/riff/ardour-9.8.0
Dummy backend: True
JACK Backend: False
ALSA Backend: False
PulseAudio Backend: False
Free/Demo copy: False
Lua Commandline Tool: True        # HAVE_READLINE; otherwise no ardour9-lua
LV2 support: True
Build target: <host target printed by configure>
```

Notes:
- `--optimize` is required for a release-quality render; without it Ardour configures a debuggable build (`wscript:981-984`).
- `waf install --destdir` is used so the same build can be hashed in staging; the **final** prefix must be `/opt/riff/ardour-9.8.0` on the runtime host because wrappers bake paths. If staging and runtime differ, re-run install against the runtime prefix or regenerate wrappers; do not `mv` the tree.
- No `sudo make install` and no `/opt` write is implied here; root owns the queue/owner decision. This document only defines what that queue would execute.

Post-build unit tests (optional, only if root wants them):
- Reconfigure with `--test` (adds `cppunit ≥ 1.12.0`), build, then `python3 ./waf test` for the two suites that define `test(ctx)` (`libs/evoral/wscript:140-148`, `libs/temporal/wscript`) and inspect `--run-tests` output.
- These are library unit tests, **not** a DAW integration gate. The real gate is §7.

---

## 6. Expected artifacts to hash (staging prefix `$STAGE/opt/riff/ardour-9.8.0`)

Hash **both** the wrapper and the executable it dispatches to; a wrapper hash alone can mask a binary change, and a binary hash alone can mask a path/flag change in the wrapper.

| # | artifact (relative to prefix) | produced by |
|---|---|---|
| 1 | `bin/ardour9` | `gtk2_ardour/wscript:788-798` (GUI wrapper `ardour` + `MAJOR`) |
| 2 | `lib/ardour9/ardour-9.8.0` | `gtk2_ardour/wscript:649-678` |
| 3 | `bin/ardour9-lua` | `luasession/wscript:32-38` |
| 4 | `lib/ardour9/luasession` | `luasession/wscript:40-88` |
| 5 | `bin/ardour9-export` → `lib/ardour9/utils/ardour-util.sh` | `session_utils/wscript:80` |
| 6 | `lib/ardour9/utils/ardour9-export` | `session_utils/wscript:22-45` |
| 7 | `bin/ardour9-new_session` → `lib/ardour9/utils/ardour-util.sh` | `session_utils/wscript:80` |
| 8 | `lib/ardour9/utils/ardour9-new_session` | `session_utils/wscript:22-45` |
| 9 | `bin/ardour9-new_empty_session` → `lib/ardour9/utils/ardour-util.sh` | `session_utils/wscript:80` |
| 10 | `lib/ardour9/backends/libdummy_audiobackend.so` | `libs/backends/dummy/wscript:16-22` |
| 11 | `lib/ardour9/LV2/a-fluidsynth.lv2/{a-fluidsynth.so,manifest.ttl,a-fluidsynth.ttl}` | `libs/plugins/a-fluidsynth.lv2/wscript:9-36` |
| 12 | `lib/ardour9/LV2/{a-comp,a-delay,a-eq,a-exp,a-reverb,reasonablesynth}.lv2/*` | in-tree plugin wscripts |
| 13 | `share/ardour9/scripts/_route_template_generic_midi.lua` (and the tree it is in) | `share/scripts` child, `wscript:343-407` |
| 14 | `etc/ardour9/*` | `autowaf` config dir layout |

External, only if the gmsynth payload is staged for revalidation (not a build product):

| # | artifact | provenance |
|---|---|---|
| 15 | `lib/ardour9/LV2/gmsynth.lv2/{gmsynth.so,gmsynth.ttl,manifest.ttl}` | demo bundle `lib/LV2/gmsynth.lv2` |
| 16 | `lib/ardour9/LV2/gmsynth.lv2/GeneralUser_LV2.sf2` | same bundle (32,322,864 bytes in the probed 9.8.241 package) |

Receipt should be a deterministic JSON (`riff.daw.ardour-build.v1`) with: pin (tag/commit), configure argv, dependency versions from `pkg-config --modversion` for every row in §4.1, compiler versions, install prefix, stage root, and per-artifact `{path, sha256, bytes, symlink_target?}`. Hash the *resolved* path for symlinks and record the link target separately.

Disk/time are **estimates, not measured here**: full source+history plus build tree should be budgeted at multiple GB and tens of minutes on a normal builder; the queue must measure and record actuals rather than reuse these numbers.

---

## 7. Provisioning layout

### 7.1 Prefix (FHS-style, from `waf install`)

```
/opt/riff/ardour-9.8.0/
  bin/ardour9
  bin/ardour9-lua
  bin/ardour9-export
  bin/ardour9-new_session
  bin/ardour9-new_empty_session
  lib/ardour9/
    ardour-9.8.0
    luasession
    libardour.so* libpbd.so* ... (internal libs)
    backends/libdummy_audiobackend.so
    LV2/a-fluidsynth.lv2/ ...
    utils/ardour9-export ...
  share/ardour9/...
  etc/ardour9/...
```

`DLLDIR` is `$PREFIX/lib/ardour9` (`wscript:1629-1639` in `build()`); every wrapper points `ARDOUR_DLL_PATH` there (`luasession/ardour-lua.sh.in`, `session_utils/ardour-util.sh.in`). `LV2DIR` defaults to `$PREFIX/lib/ardour9/LV2` (`wscript:1061-1066`).

### 7.2 Plugin scan path (this is how gmsynth is found)

Ardour builds a bundled-plugin search path from `ARDOUR_DLL_PATH` plus `/LV2`, then calls `lilv_world_load_all()`:

- `libs/ardour/search_paths.cc:125-131` — `lv2_bundled_search_path()` = `ardour_dll_directory()` + `LV2`.
- `libs/ardour/lv2_plugin.cc:3785-3805` — scans every bundle there and calls `lilv_world_load_bundle()`, then `lilv_world_load_all()`.

Therefore install the external bundle at:

```
$PREFIX/lib/ardour9/LV2/gmsynth.lv2/
```

and keep `GeneralUser_LV2.sf2` **inside that directory** (the validated probe added the plugin with an empty state string and got audio because the SF2 sits beside the .so). `LV2_PATH` may additionally be set for other bundles, but it is not required for `ARDOUR_DLL_PATH/LV2`.

Copy command (staging, no root, no execution):

```sh
install -d "$PREFIX/lib/ardour9/LV2"
cp -a gmsynth.lv2 "$PREFIX/lib/ardour9/LV2/"
test -s "$PREFIX/lib/ardour9/LV2/gmsynth.lv2/gmsynth.so"
test -s "$PREFIX/lib/ardour9/LV2/gmsynth.lv2/GeneralUser_LV2.sf2"
```

### 7.3 Runner environment (per job, owned state)

```sh
export ARDOUR_PREFIX=/opt/riff/ardour-9.8.0
export ARDOUR_DLL_PATH="$ARDOUR_PREFIX/lib/ardour9"
export ARDOUR_DATA_PATH="$ARDOUR_PREFIX/share/ardour9"
export ARDOUR_CONFIG_PATH="$JOB_STATE/ardour-config"   # writable per-job
export HOME="$JOB_STATE/home"                          # never the service HOME
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_DATA_HOME="$HOME/.local/share"
export JACK_NO_START_SERVER=1
export LC_ALL=C TZ=UTC
# optional: export LV2_PATH="$ARDOUR_PREFIX/lib/ardour9/LV2:$EXTRA_LV2"
```

This matches the validated probe interface (`ARDOUR_PROBE_SESSION_DIR/_SNAPSHOT_IN/_SNAPSHOT_OUT/_LOG/_STEM_PREFIX`) and the owned-HOME/XDG, `JACK_NO_START_SERVER=1` rule from the contract.

### 7.4 Licensing caveat on the payload (not cleared by this build)

- `gmsynth.ttl:29` declares `doap:license <http://usefulinc.com/doap/licenses/gpl>` — GPL family, **version not stated**. No license file ships inside `gmsynth.lv2`.
- `GeneralUser_LV2.sf2` has no license file in the bundle and was previously recorded as GeneralUser GS 2.0.2 custom / non-OSI with sample-provenance caveat.
- `a-fluidsynth.lv2` is in-tree Ardour source; Ardour’s own headers state GPL-2.0-or-later (e.g. `session_utils/new_session.cc:4-6`), and `COPYING` is GPLv2.
- Therefore a source build of Ardour does **not** by itself clear the instrument payload. Root must choose: (a) stage the demo gmsynth payload for byte-identical revalidation only, (b) build gmsynth from its own pinned upstream source and clear its license, or (c) switch production to the built `a-fluidsynth.lv2` + a licensed SF2 and re-run the per-track program battery.

---

## 8. Qualification battery entry point (contract §5.4)

Proposed entry point (to be authored in the implementation plan, not here):

```
tools/daw/qualify-ardour.sh \
  --prefix "$ARDOUR_PREFIX" \
  --fixtures probes/fixtures \
  --work "$QUALIFY_WORK" \
  --out "$QUALIFY_WORK/receipts/qualification.json"
```

It must exit non-zero on any failure and write `riff.daw.ardour-qualification.v1` with one entry per gate:

| gate | what it proves | accept |
|---|---|---|
| Q1 identity | wrappers and binaries run; `--version` reports `9.8.0`; sha256 matches build receipt | exact strings + hashes |
| Q2 clean session | `ardour9-new_session -s 48000 -m 2 <dir> <name>` creates a loadable session | file exists; track count 0; stereo master bus present; reopen-save succeeds |
| Q3 fixture build | `build_session.py` v1 produces 3 sources / 3 tracks with programs 73/33/0 | source+session hashes recorded |
| Q4 chain | upgrade → instrument → tempo/range → reopen/save → mix export | every snapshot exists; `save ok=true`; mix nonzero, duration 21.166667 s ±1 sample |
| Q5 events | `verify_timeline.py` against clean abc2midi | Flute 12 / Bass 30 / Piano 37 exact events; tempo/meter ticks 0/7680/12960, end 14880; end `b59520` |
| Q6 stems | `save_stems.lua` + per-snapshot export | 3 WAVs; each nonzero; sum peak 17877 = mix peak (or ±1 LSB); diff RMS ≤ 1.0 |
| Q7 program control | 73→0 swap on the same note events | note counts unchanged; peak/RMS/centroid shift matches the recorded receipt, **not** a timbre-quality claim |
| Q8 edit | same-topology v2 replacement on a project copy | source hashes change for all tracks; duration 21.166667 s; v2 mix hash differs from v1; exact events |
| Q9 determinism | re-run the Q4–Q6 chain in a fresh workdir | mix/stem sha256 match the first run; otherwise report a determinism failure (do not silently accept) |
| Q10 negative | missing SF2, bad snapshot, wrong track count | each fails closed with no promoted artifact |

The battery must run with `nice -n 19 ionice -c3`, sequentially, and publish artifacts only after Q1–Q8 pass. This is the gate the queued build should use; unit tests are supplementary.

---

## 9. Decisions routed to root / open uncertainty

1. **Commit pin wording:** use commit `22ed8656c2533e325322ff11831448e5123e0d4b` (tag `9.8`); the dispatch SHA `059c9588…` is the tag object. Ask root to record both.
2. **Build owner/host:** this document does not authorize a native build. Root owns the runtime queue and the build-owner decision.
3. **GUI compilation:** accepted as unavoidable upstream-clean cost. If root insists on zero GTK/X11 build deps, that is a maintained patch decision, not a configure flag.
4. **ALSA dev headers:** recommended as a build dependency (`libasound2-dev`); no ALSA server/runtime needed for dummy-backend renders.
5. **Instrument payload:** root must pick gmsynth (staged or source-built, license review) vs in-tree `a-fluidsynth` + licensed SF2. The build itself produces only the latter.
6. **No claims made here:** no compile/install was performed, no binary hash exists yet, no macOS/Arm64 path, no redistribution clearance, no claim that a GUI-less build works without a patch.

---
