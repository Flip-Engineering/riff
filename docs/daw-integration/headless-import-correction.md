# Correction: LMMS `--import` is not a headless stage (#78)

Author: riff-daw-deepseek-20261025. Status: source-verified correction for the
joint proposal draft, 2026-10-25. Not a selection.

The proposal draft says LMMS is "the only candidate whose MAN PAGE documents
the full headless chain today (`--import`, `render`, `rendertracks`)". The man
page does document those actions, but the source shows `--import` is a GUI
path, not a headless one.

Evidence from LMMS `src/core/main.cpp` (v1.2.2 and v1.3.0-alpha.2; the same
control flow):

```cpp
bool coreOnly = false;
...
if (arg == "--help" || ... || arg == "render" || arg == "--render" || arg == "-r") {
    coreOnly = true;
} else if (arg == "rendertracks" || arg == "--rendertracks") {
    coreOnly = true;
    renderTracks = true;
}
...
QCoreApplication *app = coreOnly ? new QCoreApplication(argc, argv)
                                 : new MainApplication(argc, argv);
```

`--import` is parsed later and does **not** set `coreOnly`, so it constructs
`MainApplication` (the GUI application). The import path then calls
`ImportFilter::import(fileToImport, Engine::getSong())` and, with `-e`,
`return EXIT_SUCCESS` immediately without saving a project:

https://github.com/LMMS/lmms/blob/v1.2.2/src/core/main.cpp
https://github.com/LMMS/lmms/blob/v1.3.0-alpha.2/src/core/main.cpp

The MIDI importer itself uses GUI state: `MidiImport` creates `QProgressDialog`
and calls `gui->mainWindow()` while building channels, SF2 instruments and
automation (`plugins/MidiImport/MidiImport.cpp`). Thus:

- `render` and `rendertracks` **are** headless (`QCoreApplication`); they
  require an existing project file and can export mixdown/stems.
- `--import` **is not** headless and does not by itself write a reusable
  project. It is an interactive-GUI convenience, even with `-e`.
- An agent could write `.mmp`/`.mmpz` project XML directly and then call
  `lmms render`; that is a possible integration path, but it is not the
  man-page chain the draft describes and has not been validated here.

This correction matters because it removes LMMS's apparent advantage in
"headless create → import → render". Both serious candidates currently need
a project-construction adapter: Ardour has a documented headless exporter and
a Lua control surface but no headless MIDI import; LMMS has headless render
but GUI-only MIDI import. The decisive comparison is which project format can
be generated and validated safely, not which man page lists more actions.

Reference receipts:
- `docs/daw-integration/ardour-headless-probe.md` (Ardour headless export
  works; MIDI import gate remains).
- `docs/daw-integration/daw-candidates.md` §2.2 (LMMS candidate evidence).
- `docs/daw-integration/proposal-draft.md` (claim being corrected).
