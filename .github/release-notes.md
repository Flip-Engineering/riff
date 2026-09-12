Instrumental mode now sends YuE2 an explicit instrumental cue when no lyric text is supplied, including when an artist provides a custom sound description. The shared change covers both the studio and CLI. Custom cues and descriptions pass through unchanged, and Free play keeps its open conditioning.

A controlled same-seed brass study produced clean instrumental music with this cue where blank lyrics introduced singing. This improves the default; the model can still introduce voices.

Update through **Studio settings**, or run the latest `install.py`. The library, engine settings, and saved connection carry over. The producer recommendations, animated MP4 exports, and private Tailscale access introduced in v0.2.4 are included.

Validation includes 61 backend tests, browser workflows, packaged installation and rollback, and Metal, CPU, and CUDA builds. CUDA inference on NVIDIA hardware remains unverified.

Riff is MIT licensed. Separately downloaded YuE2 model weights retain their CC BY-NC 4.0 license. `SHA256SUMS` covers the application archive and installer.
