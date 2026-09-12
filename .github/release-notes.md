Long video exports now upload each animation frame as bytes, release the temporary canvas Blob, and consume the server acknowledgement before continuing. This avoids accumulating upload bodies in Chromium’s Blob storage during full-song renders. Brief connection failures still recover without duplicating frames.

Validation includes a complete 4:23, 1280×990/24fps MP4 over private HTTPS, full audio duration, matching seed artwork, stable browser memory, and interrupted-upload recovery. Release checks also cover 63 backend tests, browser workflows, packaged installation and rollback, and Metal, CPU, and CUDA builds. CUDA inference on NVIDIA hardware remains unverified.

The producer carries source inputs, scores and the edit timeline for assembled recordings, and Keep lyrics preserves the artist’s lyric input directly.

Update through **Studio settings**, or run the latest `install.py`. Your library, engine settings, and saved connection carry over.

Riff is MIT licensed. Separately downloaded YuE2 model weights retain their CC BY-NC 4.0 license. `SHA256SUMS` covers the application archive and installer.
