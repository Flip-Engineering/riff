Riff 0.6.31 improves composition workflows, saved-material recovery and engine setup.

Studio and agents now share discoverable operations for planning, generation, replay, development, selection and review, with the same queue, validation and receipts. Saved scores and sound material retain their lineage when reused, and concurrent saved-sound preparation no longer rejects an unchanged file during temporary-copy cleanup.

Warm engines account for resident weights during memory admission. They stay resident between manual takes by default when warm mode is enabled; releasing the engine when the queue becomes empty is a separate opt-in setting. Source verification reuses unchanged file identities and can use the system SHA-256 implementation while still checking for changed or substituted models.

Some virtual macOS Metal devices lack operations required by YuE2. Setup and Settings now identify those devices and offer CPU recovery before a generation starts. Hosted checks distinguish Metal compilation and CPU synthetic tests from model inference; the hosted LoRA workflow records an explicit not-qualified result on unsupported devices.

LoRA storage reports retained disk bytes and offers explicit cleanup. Seed artwork updates correctly in the Canvas fallback, and video export and shared-runner checks include reliability fixes.

Updates keep the library, recordings, saved material and settings in the persistent workspace. Verified model downloads are reused; source installations may rebuild a changed engine. Music, writing, reviews and exports must finish or be cancelled before activation.
