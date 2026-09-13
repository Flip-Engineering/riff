Riff 0.6.2 puts update progress beside the update controls and improves preparation and retry behavior.

- Update, setup and restart feedback appears with the relevant controls. Settings errors stay visible inside the dialog, and a failed preference change restores the previous settings.
- The updater retains its verified download without making another archive copy. Existing downloaded files remain intact, and preparation repairs a missing or damaged archive cache even when the expanded package is already valid.

The desktop package includes the music engine, media tools and local writer runtime. Riff Setup downloads and verifies the music and writer models, installs the Riff application and supports app-managed updates. Existing music, models and settings are retained.

The graphical macOS download is an ad-hoc signed preview. It has no Developer ID signature or notarization, so macOS may block its first launch under default security settings. The source archive is a separate developer download. NVIDIA desktop packaging remains in development.

Validation includes the complete application and browser suites, archive ownership and interrupted-update recovery, desktop/mobile settings interactions, direct generation and studies, playback, WebGL and H.264/AAC video export. Source installation is checked through actual startup, failed-update fallback and library preservation. Desktop build and installation evidence is recorded separately from experimental native inference work.
