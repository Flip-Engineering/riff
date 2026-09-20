Riff 0.6.9 fixes accelerated export timing and adds persistent performance receipts.

- Accelerated video follows the requested frame clock even when the browser encoder embeds different timestamps. Output validation still checks dimensions, frame count and timing before download.
- Completed exports retain browser stage timings, server write/finalization timings and byte counts through app restarts. Existing export clients remain compatible.
- A reproducible developer benchmark compares PNG and H.264 paths with short/long passages, decoded-frame hashes, audio/video timing and resource measurements. Its limits are documented; this release does not claim a universal speedup.

It retains the browser H.264 encoding added in 0.6.8 for supported high-resolution exports, avoiding the PNG transfer and second video encode. PNG remains available when the browser cannot use the accelerated path.

- Worker startup failures safely select the compatible export path before encoding starts.
- Completed H.264 exports are checked for dimensions, frame count and timing before download.
- Bundled media tools support the new H.264 transport. MP4 audio remains AAC; original WAV recordings are preserved.
- Real 4K/60 browser checks cover frame count, audio/video timing and first-frame artwork similarity. Broader performance benchmarks and share/master profiles remain in progress.

This release also includes the saved-synthesis recovery work since 0.6.5:

- **Finish audio** appears in history when a saved synthesis is ready, including after an interrupted decode. The original take, seed, score and performance are retained.
- A recording’s **Audio refinement** controls let you adjust decoder sections, overlap and precision. Empty fields restore the captured settings.
- **What changed** explains decoder refinements between takes, using the settings actually selected for each finish.
- Writers, producers and agents share the same saved-synthesis operation and full musical context. Editing the music starts a new performance; Undo restores the saved sound.
- **Sound to finish later** joins the output choices. The public download page keeps a completed graphical installer available while a new release is being assembled.

The desktop package includes the music engine, media tools and local writer runtime. Open Riff Setup, install, then open Riff. Models download as needed; existing music, models and settings are retained. Subsequent updates belong to the app. The graphical macOS download is an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development.

Actual Metal checks reproduce every PCM sample from saved 8-second and 45-second performances. A real application queue replay loads only the audio decoder and preserves the source recording. File integrity, interrupted publication, cancellation, restart recovery, exact seeds and structured producer inputs are covered separately by control and application tests. Browser checks exercise recovery, decoder controls, edit/Undo, queue ownership and draft preservation. Changing decoder settings can change the resulting audio; original-setting parity is validated on the tested M4.
