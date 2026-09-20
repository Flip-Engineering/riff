Riff 0.6.16 fixes desktop update discovery when GitHub's anonymous API is rate-limited or unavailable. The updater automatically uses the public stable release and its published payload receipt, without a GitHub login or command-line tools. Version, platform, filename, size, checksum and package validation remain required. An incomplete desktop publication leaves the current app unchanged.

It retains the removal of the rejected Share/Master export profiles from 0.6.15. There is one export configuration, retaining the previous high-quality settings. There is no quality downgrade or delivery selector. The original WAV, saved exports and historical receipts are preserved. Export optimization remains separate work, measured at equal quality.

It retains Delete song from 0.6.14 in the library's three-dot recording menu. Confirm to remove the song and its WAV permanently. Other takes stay usable: needed performance data is copied to them, and the parent link is broken. Saved videos and shared synthesis assets are kept. Archive remains available for reversible removal.

Deleting the playing song stops playback and clears its selection without changing your creative draft. Cancelled or failed deletions leave the song in place. This release also fixes a timing-dependent preference-save browser test.

It retains Origin & variations from 0.6.12. Follow recorded parent and performance links, see branches including archived takes, and inspect which creative inputs changed. Related recordings open separately so your current draft and unsaved notes stay intact.

- The Studio and agents share a versioned, read-only lineage graph. Missing sources remain explicit and cyclic historical links cannot trap traversal. No ancestry is inferred from similar titles, seeds or music.
- Original recipes and recordings are not rewritten. This is the ancestry foundation; named study families and development operations remain in progress.
- The rejected score-analysis proposal is not included. There is no notation-conformance check or automatic regeneration.

It retains the export reliability fixes from 0.6.11 without changing your chosen picture size, frame rate or passage.

- MP4 uses lossy AAC audio, clearly labeled in the dialog. Download the untouched full original WAV beside the video, including when it covers only a passage.
- Browser encoding uses a non-dropping quality mode with bounded frame draining. This fixes a low-bitrate stall exposed by the Share benchmark; stalled workers also fail with a useful error and remain cancellable.
- Export receipts and agent capabilities describe the same fixed encoding settings. Broader long-run and independent mobile-player acceptance remain separate; this release does not claim lossless MP4 or a universal speedup.

It retains the 0.6.10 playback correction: failures retain the actual error, and Play reaches the media player directly without an empty asynchronous preparation step.

It includes the accelerated export timing fix and persistent performance receipts from 0.6.9:

- Accelerated video follows the requested frame clock even when the browser encoder embeds different timestamps. Output validation still checks dimensions, frame count and timing before download.
- Completed exports retain browser stage timings, server write/finalization timings and byte counts through app restarts. Existing export clients remain compatible.
- A reproducible developer benchmark compares PNG and H.264 paths with short/long passages, decoded-frame hashes, audio/video timing and resource measurements. Its limits are documented; this release does not claim a universal speedup.

It retains the browser H.264 encoding added in 0.6.8 for supported high-resolution exports, avoiding the PNG transfer and second video encode. PNG remains available when the browser cannot use the accelerated path.

- Worker startup failures safely select the compatible export path before encoding starts.
- Completed H.264 exports are checked for dimensions, frame count and timing before download.
- Bundled media tools support the new H.264 transport. MP4 audio remains AAC; original WAV recordings are preserved.
- Real 4K/60 browser checks cover frame count, audio/video timing and first-frame artwork similarity. Broader performance acceptance remains in progress.

This release also includes the saved-synthesis recovery work since 0.6.5:

- **Finish audio** appears in history when a saved synthesis is ready, including after an interrupted decode. The original take, seed, score and performance are retained.
- A recording’s **Audio refinement** controls let you adjust decoder sections, overlap and precision. Empty fields restore the captured settings.
- **What changed** explains decoder refinements between takes, using the settings actually selected for each finish.
- Writers, producers and agents share the same saved-synthesis operation and full musical context. Editing the music starts a new performance; Undo restores the saved sound.
- **Sound to finish later** joins the output choices. The public download page keeps a completed graphical installer available while a new release is being assembled.

The desktop package includes the music engine, media tools and local writer runtime. Open Riff Setup, install, then open Riff. Models download as needed; existing music, models and settings are retained. Subsequent updates belong to the app. The graphical macOS download is an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development.

Actual Metal checks reproduce every PCM sample from saved 8-second and 45-second performances. A real application queue replay loads only the audio decoder and preserves the source recording. File integrity, interrupted publication, cancellation, restart recovery, exact seeds and structured producer inputs are covered separately by control and application tests. Browser checks exercise recovery, decoder controls, edit/Undo, queue ownership and draft preservation. Changing decoder settings can change the resulting audio; original-setting parity is validated on the tested M4.
