Tailscale HTTPS can now keep both the default studio address and an explicit port
such as 7878 usable. The setup helper preserves earlier Riff routes and continues
to require the connected Tailscale account for each address.

Producer review turns a recording into a complete recommended generation.
The selected multimodal model receives the audio, prior generation controls,
and supplied/generated YuE2 score. Its recommendation includes lyrics, direction,
score, seed, duration, and sampling settings. Generate it directly, edit it with
undo, or download its recipe.

Live sound animates each recording’s original seed artwork with a subtle riff
wordmark. The player’s **MP4** button exports that same animation with the
recording’s audio. Width, height,
and frame rate are editable; progress and cancellation are included. **WAV**
remains beside it. Reviews and MP4 exports require FFmpeg.

Optional Tailscale Serve access brings the studio to your other devices through
private HTTPS, restricted to your connected Tailscale account. The setup helper
preserves existing Serve routes; see the installation guide for instructions.
Custom HTTPS ports, including 7878, keep earlier Riff addresses usable.

Download `install.py` and run `python3 install.py`, or open **Studio settings**
in an installed copy of Riff to check for the update. Your library and engine
settings carry over. See the [installation guide](https://github.com/Flip-Engineering/riff/blob/main/docs/install.md).

Release checks cover Python 3.9 and 3.14, browser workflows, packaged installation
and rollback, real MP4 encoding and cancellation, and Metal, CPU, and CUDA builds. CUDA inference on NVIDIA hardware
remains unverified.

Riff is MIT licensed. Separately downloaded YuE2 model weights retain their
CC BY-NC 4.0 license. `SHA256SUMS` covers the application archive and installer.
