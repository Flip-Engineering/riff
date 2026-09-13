# riff.yue2.acoustic.v1

All integers are little-endian. The header is exactly 512 bytes. Payload bytes follow immediately, with no trailer: `frames * latent_dim` finite IEEE754 binary32 values in `[frame, latent_channel]` order. There is no native struct serialization, compression, padding or tensor-container dependency.

| Offset | Bytes | Field |
| ---: | ---: | --- |
| 0 | 8 | ASCII magic `RIFFYAC1` |
| 8 | 4 | Version 1 |
| 12 | 4 | Header size 512 |
| 16 | 4 | Dtype 1: F32LE |
| 20 | 4 | Layout 1: frame-major |
| 24 | 4 | Completed state 1 |
| 28 | 4 | Semantic truncation flag, 0 or 1 |
| 32 | 8 | Frames |
| 40 | 8 | Latent dimension |
| 48 | 8 | Payload byte count |
| 56 | 8 | Audio sample rate |
| 64 | 8 | Audio channels |
| 72 | 8 | VAE downsampling ratio |
| 80 | 8 | Captured decoder core frames |
| 88 | 8 | Captured decoder halo frames |
| 96 | 8 | Encoder latent dimension |
| 104 | 4 | VAE storage ID |
| 108 | 4 | Main storage ID |
| 112 | 8 | Seed, 0 through INT64_MAX |
| 120 | 8 | Solver steps |
| 128 | 4 | Solver: midpoint 1, AB2 2 |
| 132 | 4 | Decoder contract 1 |
| 136 | 8 | Semantic frames, equal to acoustic frames |
| 144 | 8 | Model context |
| 152 | 4 | Effective guidance IEEE754 F32 bits |
| 156 | 4 | Reserved, zero |
| 160 | 32 | SHA256 payload |
| 192 | 32 | SHA256 selected main GGUF |
| 224 | 32 | SHA256 selected VAE GGUF |
| 256 | 32 | SHA256 parsed main config bytes |
| 288 | 32 | SHA256 parsed VAE config bytes |
| 320 | 32 | SHA256 tokenizer file |
| 352 | 32 | SHA256 generation config bytes; SHA256(empty) if absent |
| 384 | 32 | SHA256 positive conditioning prefix, serialized LE signed int32 IDs |
| 416 | 32 | SHA256 semantic codec IDs, serialized LE signed int32 |
| 448 | 32 | SHA256 producing executable file |
| 480 | 32 | SHA256 header bytes 0 through 479 |

Storage IDs are stable: native 0, F32 1, F16 2, BF16 3, Q8_0 4, Q4_0 5, Q4_K 6. Native means the selected GGUF's storage, whose exact digest is recorded. Captured guidance is finite and follows the existing model range [0,20]. Semantic truncation describes the actual native semantic result; pre-existing performance files may require the application's original job record to retain earlier truncation history.

Dimensions, products and runtime conversion bounds are checked before allocation. Positive fields include frames, latent/rate/channels/ratio/core/encoder dimension/steps/context; halo may be zero. Payload bytes must equal `frames * latent_dim * 4` and fit the signed native runtime range. Audio's existing Oobleck contract produces `frames * downsampling_ratio - 64` frames; invalid/overflowing dimensions are rejected. All provenance digests must be present (nonzero). Header and payload hashes plus exact file length and every float's finiteness are validated.

Compatibility requires exact selected VAE digest and matching latent/encoder/audio dimensions, downsampling ratio and sample rate. Main/executable/config/conditioning digests describe how the completed tensor was created and do not force those original files to exist for decode. Core/halo/storage defaults restore from the header; explicit compatible decoder refinements remain possible.

No path, prompt, lyric or raw score sequence is serialized. Hashes are integrity identities, not a signature or authenticity claim. The application's owned artifact boundary supplies trust and recipe/history links. Before spawning capture/replay, it must require the actual engine's matching feature and format capability records. Older engines must never receive unsupported stage options. See the [native CPU checks](../../tests/native_acoustic/README.md); actual decoder-tile numerical and memory acceptance remains a separate backend gate.
