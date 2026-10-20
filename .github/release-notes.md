Riff 0.6.25 makes the sound visualization respond immediately during playback.

Live playback now reads energy, frequency bands, stereo image, transients and waveform samples directly from the playing audio through Web Audio. It no longer waits for the server to analyze the entire recording before reacting to the music. Frames follow the audio clock, and short motion history is reset after seeks and recording changes. The audio graph is reused across playback, preserving normal pause/resume and navigation.

Full-recording analysis remains available for deterministic video export, paused snapshots and browsers without live audio analysis. Existing motion appearance controls and reduced-motion behavior are retained. No music engine, adapter or recording data is changed.

Validation includes a browser regression that holds the full-recording analysis response while verifying nonzero live audio motion and advancing playback frames, plus playback, seeking, reduced-motion and actual WebGL checks.

The graphical macOS download remains an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development. The ChatGPT plugin is a separate downstream delivery.
