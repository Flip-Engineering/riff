Riff 0.6.21 adds YuE2 LoRA styles to the studio and native generation pipeline.

- Browse Hugging Face styles, inspect model cards and licenses, listen to previews, or import compatible safetensors/PEFT files.
- Keep versioned adapters in your local library. Search and filter styles, edit their names and notes, and explicitly add trigger words to your direction.
- Select independent Composition/AR and Sound/NAR adapters and strengths. Queued and saved takes retain the exact adapter revisions, file hashes, and effective strengths for reproduction.
- Downloads support verification, cancellation, and recovery. Referenced adapters are protected from deletion, and adapter changes restart warm generation sessions.
- A training preparation helper creates dataset/configuration handoffs and an evaluation plan. This release does not include a newly trained Riff adapter.

Validation includes Linux Python 3.9/3.14 tests, the complete browser suite with LoRA navigation and desktop/phone layouts, native CPU checks, and source archive installation checks. A short real-model CPU comparison with a public adapter is documented in docs/lora-validation.md. These checks do not establish full-song musical quality, Metal/CUDA LoRA inference, or completed training; those remain separate acceptance work.

The ChatGPT plugin is a separate downstream delivery and is not included in this Riff release. Main's homelab validation no longer requires the plugin's tests or package builders.

Existing recordings, models, recipes, and settings remain outside replaceable application releases. This release retains the fixed high-quality video exports, saved-synthesis recovery, library search, and updater recovery from earlier versions.

The graphical macOS download remains an ad-hoc signed preview with no Developer ID signature or notarization. Desktop installer/payload availability and platform evidence must be checked separately from the source archive; NVIDIA desktop packaging remains in development. YuE2 weights and third-party adapters retain their own license terms.
