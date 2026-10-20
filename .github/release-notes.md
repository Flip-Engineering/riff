Riff 0.6.26 replaces per-frame video uploads with bounded batches.

Video exports group up to 16 consecutive PNG or H.264 frames into each request, capped at 32 MiB. Drawing, encoding and uploading continue to overlap. The browser resumes from the server's received frame index after a lost response, including a partly accepted batch, without duplicating frames. Cancellation stops the owned encoder. Older clients and servers retain the single-frame protocol.

Export quality is unchanged: requested dimensions, frame rate and passage, browser H.264 quantizer 14, final libx264 slow/animation at CRF 19 with BT.709 colour, and AAC 320 kbps. The original full-recording WAV remains available unchanged beside each MP4. Matched benchmarks retain frame hashes and request counts rather than inferring speed from the transport alone; see docs/export-performance.md.

Artwork rendering also retries temporary graphics initialization failures and rebuilds resources on the restored context without reloading the page. This recovery fix is separate from the cross-device export throughput improvement.

Validation covers malformed and oversized batches, sequence enforcement, partial acceptance, lost acknowledgements, byte budgets, cancellation, backward compatibility, and real 4K/60 MP4 dimensions, frame count, timing, artwork and audio.

The graphical macOS download remains an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development. The ChatGPT plugin is a separate downstream delivery.
