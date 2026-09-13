Riff 0.5.1 lets you create a performance before rendering its sound, return to completed stages after an interruption, and explore acoustic variations from the same phrasing.

- **Performance to render later** saves YuE2's music codes and score. Open it from history, shape its sound, and render a new take. Undo restores the draft you had open. Producer proposals and agent recipes use the same operation.
- **Multistep synthesis** reuses earlier velocity estimates to reduce acoustic network calls. A 32-second, 16-step comparison rendered in 31.81 seconds versus 49.08 with midpoint, using 17 calls instead of 32. The method is selectable in advanced controls, producer recipes and the CLI. Midpoint remains the default; their acoustic results can differ.
- Native synthesis writes its retained conditioning cache directly and releases the completed prefill graph. A 201.5-second saved-performance render fell from 6.59 GB to 3.39 GB peak process footprint on an M4, with byte-identical audio. The acoustic stage used about 2.63 GB. This is a one-step engineering comparison, separate from musical-quality assessment.
- A specialized Metal attention kernel reuses loaded keys and values across more queries. Alternating measurements of the long acoustic shape improved the kernel by about 2%, with exact output. These runs do not establish an overall rendering speedup.
- Signed waveform pressure travels through the artwork's spacing, curvature and light. The opening stays clear; mineral hues vary by seed, with the sole riff wordmark in the corner. Live playback and MP4 share the renderer.

Validation includes application and browser workflows, native attention comparisons, solver convergence and evaluation counts, saved-stage recovery, draft undo, keyboard focus, mobile layouts and decoded MP4 frames. The release workflow also checks packaged installation and Metal, CPU and CUDA compilation. NVIDIA inference remains unverified.

The Metal benchmark checks operation support on virtual GPUs and reports unavailable flash attention explicitly. The preceding v0.5.0 tag did not produce a release because that check assumed every Metal device supported SIMD matrix operations.
