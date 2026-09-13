Riff 0.5.2 fixes Direct generation and short studies, adds subtle color and texture controls to living artwork, and raises MP4 exports to 4K at 60 fps.

- Direct generation now omits a score from the native request while keeping it in your draft. Switching back to melody or harmony restores it.
- Generate study uses its own duration and solver detail. Selected lyrics are shown before generation; editing the words clears a stale selection. A selected passage gets its own composition, and the complete draft stays intact. Errors appear beside the study action.
- Shape & light adds Color and Texture controls. Neighboring mineral hues move through one surface, with fine fibres, light and contour tension responding to the sound. Live playback and export share the renderer; only the corner riff wordmark appears inside the artwork.
- MP4 defaults to 3840×2160 at 60 fps, with studio, portrait, square and custom framing. Motion analysis runs at 60 fps; improved H.264 quality and 320 kbps AAC preserve fine detail and the complete audio. Larger exports take more time.

Validation includes 89 application tests, browser workflows, actual native Direct and study generations, same-seed artwork comparisons, and an actual 32-second 4K/60 fps export. The native engine is unchanged from 0.5.1. Release CI checks packaged installation and Metal, CPU and CUDA compilation; NVIDIA inference remains unverified.
