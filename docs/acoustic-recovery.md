# Saved sound synthesis

Riff retains YuE2's completed acoustic tensor before the audio decoder runs.
An interrupted decode can therefore finish without repeating composition,
performance generation or acoustic synthesis. **Finish audio** creates a new
queued take; the original job, its seed and musical inputs remain intact.

The same operation is available to the studio, agents and the structured
producer. `GET /api/acoustics/{acoustic_source}` returns the recorded inputs,
captured decoder settings and compatibility. Submit its opaque reference to
`POST /api/generations`:

```json
{
  "acoustic_source": "riff-acoustic-v1:<library reference>",
  "decoder": {"core_frames": 512, "halo_frames": 24, "storage": 1}
}
```

Omit `decoder` to restore the settings captured with the sound. Optional core
and halo controls change the decoding tiles and their overlap. Storage values
are `0` native, `1` F32, `2` F16, `3` BF16, `4` Q8_0, `5` Q4_0 and `6` Q4_K;
small convolution kernels can still be materialized as F32. These are explicit
refinements, with potentially different output. They do not change the already
sampled score, music codes or acoustic tensor. To revise those, start a fresh
performance or re-synthesize a saved performance. `render_mode: "sound"`
stops after saving synthesis, before audio decoding.

The Elixir control runtime verifies header and payload integrity, dimensions,
finite samples and complete publication. References bind immutable launch
inputs and completed stages. Queueing and execution verify the owned artifact
and selected VAE; the native decoder checks compatibility again before use.
Original decoder/model/configuration identities are recorded separately from
which files decoding actually requires. Neither latent arrays nor local paths
are sent to the producer.

Cancellation retains completed stages. Shutdown and a transient control-service
failure leave a durable capture marker; restarting retries that capture while
preserving the original job result and timestamps. Changed or partial files are
retained for diagnosis and are never offered as complete sound.

The decoder has its own memory reservation, without transformer weights, KV
cache or NAR scratch. Its present native Oobleck estimate is a conservative
unfused allocation envelope. It can substantially exceed measured physical
footprint and limit overlap; exact backend allocation planning remains ongoing.
The estimate is not an empirical memory or speed guarantee.

Validation includes real Elixir/queue/HTTP failure and restart cases, and actual
Metal replay of 8-second and 45-second saved performances. Original-setting
replay is PCM-identical. Explicit 512-frame core, 24-frame halo and F32 replay
matches a full render using those settings. This validates saved-stage reuse;
it does not establish numerical parity on untested GPUs or an end-to-end speedup
for fresh music generation.
