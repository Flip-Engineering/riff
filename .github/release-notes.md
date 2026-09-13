Riff 0.6.4 makes video export independent of pointer activity and gives the living artwork a fuller musical response.

- Video frames encode in a dedicated worker, keeping a single frame in flight and preserving the requested resolution, frame rate and audio. Cancel stops the worker and export together. Browsers without the worker capabilities retain the canvas export path.
- Bass and phrasing open the connected folds more clearly, with the existing mineral colors, polished highlights and quiet center. Movement, surface, color and texture remain adjustable.
- GPU drawing skips duplicate Canvas shading while preserving depth ordering and fallback appearance. Matched images remain identical through GPU loss and recovery.
- Setup identifies the size of the included music models and downloads them as needed. Existing music, models and settings are retained.

The desktop package includes the music engine, media tools and local writer runtime. Open Riff Setup, install, then open Riff. Subsequent updates belong to the app. The graphical macOS download is an ad-hoc signed preview; Developer ID signing and notarization remain separate improvements. NVIDIA desktop packaging remains in development.

Validation covers actual 4K/60 fps exports with stationary and moving pointers, frame ordering and cancellation, renderer comparisons, and the existing studio workflows. Measurements describe the tested browser and device; they are not an overall inference-speed claim.
