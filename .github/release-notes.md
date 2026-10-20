Riff 0.6.24 makes LoRA selection and installation visible in the community browser.

Every catalog card now has a Select & install button. Opening it immediately shows an installation panel above the results, with a loading message while compatibility checks run. Adapter file checkboxes and Install selected files appear before audio previews and the model card. Installation is enabled when a supported file is selected.

Failed checks stay in the panel with a Retry file check action. Slow checks time out, and opening another adapter or returning to results cancels the pending browser request so stale responses cannot replace the current picker.

Existing compatibility and download integrity checks remain required. The native engine is unchanged. Existing recordings, adapters, recipes and settings are preserved.

The graphical macOS download remains an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development. The ChatGPT plugin is a separate downstream delivery.
