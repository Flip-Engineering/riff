# Missing candidate note: Tracktion Engine 3.2 (#78)

Author: riff-daw-deepseek-20261025. Status: evidence note for the candidate
gap, 2026-10-25. Not a selection; no runtime probe was run.

The candidate landscape covers complete DAW applications but omits the
strongest open-source **programmatic DAW engine**. If "agent-controlled
per-instrument production" is the primary requirement, this omission should be
deliberate, not accidental.

## Evidence

- **Version**: v3.2.0, released 2025-05-15; tag commit
  `ad63adfebd3318e28c0b3903b5f2df0f2bbe2323` (GitHub tag/release metadata).
  https://github.com/Tracktion/tracktion_engine/releases/tag/v3.2.0
- **What it is**: "a high level data model and set of classes for building
  sequence based audio applications. You can build anything from a simple
  file-player or sequencer to a full blown DAW." It is a JUCE module/library,
  not an end-user DAW application; the commercial Waveform DAW is built on it.
  https://github.com/Tracktion/tracktion_engine#readme
- **License**: dual GPLv3-or-later / commercial, and the README explicitly
  requires an appropriate JUCE licence for distribution. A separate helper
  binary could follow Riff's ffmpeg precedent, but the JUCE/AGPL-or-commercial
  obligation needs review; linking into the MIT app would not be appropriate.
  https://github.com/Tracktion/tracktion_engine/blob/v3.2.0/LICENSE.md
- **Headless render**: `Renderer` is the library-level render path, with
  `Parameters` for destination file, format, bit depth, sample rate, time
  range, selected tracks, plugins on/off, real-time on/off, and
  `createMidiFile`; blocking `renderToFile` and async `EditRenderer` APIs.
  This is GUI-independent by construction.
  https://github.com/Tracktion/tracktion_engine/blob/v3.2.0/modules/tracktion_engine/model/export/tracktion_Renderer.h
- **MIDI**: `MidiClip`/`MidiList` hold notes, channels, loop ranges and program
  changes; `MidiList::readSeparateTracksFromFile` reads a MIDI file into
  per-channel lists; `MidiClip::importMidiSequence` imports events. This is a
  first-class API, unlike the GUI-only LMMS import path.
  https://github.com/Tracktion/tracktion_engine/blob/v3.2.0/modules/tracktion_engine/midi/tracktion_MidiList.h
- **Persistence**: `ProjectManager` creates/lists/saves projects and loads
  Edits; `Edit` is the serialisable arrangement model.
  https://github.com/Tracktion/tracktion_engine/blob/v3.2.0/modules/tracktion_engine/project/tracktion_ProjectManager.h
- **Automation/plugins**: `AutomatableParameter`, automation curves/modifiers
  and `PluginManager` are part of the engine (`modules/tracktion_engine/model/
  automation`, `plugins/`). VST3/AU support depends on the JUCE build and the
  user's plugins; no bundled General MIDI instrument/soundfont was found in
  the repository.
- **Platforms**: macOS, Windows, Linux, Raspberry Pi, iOS, Android; C++20.
  This intersects Riff's macOS-arm64 payload but requires a native helper
  build and code signing in addition to a source pin.

## Difference from the shortlisted DAWs

| Axis | Tracktion Engine | Ardour 9.8 | LMMS |
| --- | --- | --- | --- |
| End-user DAW application | No (engine/library) | Yes | Yes |
| Full programmatic MIDI authoring | Yes, by design | No headless import binding found | No headless import (GUI path) |
| Headless offline render | Yes (`Renderer`) | Yes (`ardour9-export`, verified) | Yes (`render`/`rendertracks`) |
| Bundled instruments/GM | No | gmsynth + SF2 | many built-in synths + SF2 |
| Project construction for an agent | API calls in a helper | template/session adapter | `.mmp` XML adapter |
| License/distribution | GPLv3 + JUCE licence | GPLv2+ | GPLv2 |
| Maturity/risk | production engine, but no Riff-side app | mature app, scripting "prototyping" | stable 1.2.2 old; 1.3 alpha |

## Recommendation implication

Tracktion Engine is the best technical fit for **agent authorship**, but
selecting it would make Riff build a production service/UI around a DAW engine
rather than integrate a complete DAW. That is a scope decision for the root,
not something this seat should assume. If Ardour's project-construction gate
fails and the user wants first-class agent MIDI authoring, Tracktion Engine is
the evidence-backed fallback; if the user wants a complete DAW application,
Ardour or LMMS remain the relevant set.

Limits: no runtime build/render probe; no macOS test; no plugin/GM instrument
verification; JUCE and SF2/plugin licensing need review; C++20 helper build,
pinning and signing are unestimated.
