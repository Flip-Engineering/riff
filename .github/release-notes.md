Video export now recovers from brief connection failures while uploading animation frames. Before retrying, Riff checks whether the previous frame was already received, so a lost response cannot duplicate a frame. Access-denied responses and invalid frames still stop the export, and cancellation remains available.

Browser validation interrupts uploads before and after delivery and injects a temporary server error, then verifies the finished H.264/AAC recording has its complete duration and matching artwork. It also checks access-denied handling, cancellation, and mobile layouts.

The producer fixes from v0.2.6 and v0.2.7 are included: assembled recordings carry source inputs, scores and their edit timeline into reviews, and Keep lyrics retains the original lyric input directly.

Update through **Studio settings**, or run the latest `install.py`. Your library, engine settings, and saved connection carry over. Release checks cover 63 backend tests, browser workflows, packaged installation and rollback, and Metal, CPU, and CUDA builds. CUDA inference on NVIDIA hardware remains unverified.

Riff is MIT licensed. Separately downloaded YuE2 model weights retain their CC BY-NC 4.0 license. `SHA256SUMS` covers the application archive and installer.
