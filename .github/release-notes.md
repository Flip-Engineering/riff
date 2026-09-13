Riff 0.6.6 saves the completed sound synthesis, so you can finish its audio without repeating composition, performance generation or synthesis.

- **Finish audio** appears in history when a saved synthesis is ready, including after an interrupted decode. The original take, seed, score and performance are retained.
- A recording’s **Audio refinement** controls let you adjust decoder sections, overlap and precision. Empty fields restore the captured settings.
- Writers, producers and agents share the same saved-synthesis operation and full musical context. Editing the music starts a new performance; Undo restores the saved sound.
- **Sound to finish later** joins the output choices. The public download page keeps a completed graphical installer available while a new release is being assembled.

The desktop package includes the music engine, media tools and local writer runtime. Open Riff Setup, install, then open Riff. Models download as needed; existing music, models and settings are retained. Subsequent updates belong to the app. The graphical macOS download is an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development.

Actual Metal checks reproduce every PCM sample from saved 8-second and 45-second performances. A real application queue replay loads only the audio decoder and preserves the source recording. File integrity, interrupted publication, cancellation, restart recovery, exact seeds and structured producer inputs are covered separately by control and application tests. Browser checks exercise recovery, decoder controls, edit/Undo, queue ownership and draft preservation. Changing decoder settings can change the resulting audio; original-setting parity is validated on the tested M4.
