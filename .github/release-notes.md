Riff 0.6.23 fixes the community LoRA browser showing base models and unrelated adapters.

Discovery now requires YuE2 model association and actual safetensors adapter weight files. A LoRA mention in a README, an example directory, or a repository owner's name is no longer enough. GGUF-only models, merged full-model weights, quantized training bases, empty adapter repositories, and unrelated image adapters are excluded. Repositories containing both base models and adapter weights remain discoverable. GGUF files are omitted from adapter details.

Adapter headers and complete downloads still undergo compatibility and integrity checks before installation. Discovery does not itself establish engine compatibility or musical quality. The native engine is unchanged, and existing recordings, adapters, recipes, and settings are preserved.

The graphical macOS download remains an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development. The ChatGPT plugin is a separate downstream delivery.
