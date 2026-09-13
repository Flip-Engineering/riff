Riff 0.6.3 lets you reuse a composition exactly while developing its sound and performance.

- Use a saved score in another take, short study or variation without asking YuE2 to compose it again. Its native score tokens stay intact, including empty scores and interrupted plans.
- Editing notation starts a new score input. Undo restores the saved composition and its planning mode. Direct generation preserves the readable draft.
- AI writers and producer reviews can recommend an actual saved score, revised notation or a new composition alongside the complete generation settings. Agent discovery exposes the same operation.
- Captured tokenizer snapshots and verified job inputs preserve scores through interruption and engine changes. Score file work runs in the existing Elixir control runtime, with independent cancellation and scheduling.

The desktop package includes the music engine, media tools and local writer runtime. Riff Setup downloads and verifies the music and writer models, installs the Riff application and supports app-managed updates. Existing music, models and settings are retained.

The graphical macOS download is an ad-hoc signed preview. It has no Developer ID signature or notarization, so macOS may block its first launch under default security settings. The source archive is a separate developer download. NVIDIA desktop packaging remains in development.

Validation covers native score parsing and conditioning prefixes, exact-score capture/recovery, provider recipes, editor attachment/Undo, direct generation, studies, playback and video export. Paired short neural renders with and without guidance retained identical score tokens, music codes and PCM samples when replaying the saved plan. This verifies those inputs and runs; it is not a guarantee of identical audio across hardware or engine changes. Desktop build and installation evidence is recorded separately.
