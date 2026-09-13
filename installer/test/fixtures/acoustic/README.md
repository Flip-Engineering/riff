# Native acoustic checkpoint fixture

`checkpoint-v1.bin` was produced by the native `save_completed` implementation in the E06 format test, rather than by the Elixir parser. File SHA256: `0ea98dc66911a532434c0b628b1653e781defe2bfb82b1402531e242a5ecff11`.

Three frames × 64 F32LE latent channels; 48,000 Hz stereo, stride 1920, encoder latent dimension from the native VAE default, core 1024 and halo16. Seed `9223372036854775807`; AB2, four steps, float32 guidance1.1, context24576, truncated semantic stage. VAE storage2(F16), main storage5(Q4_0). The payload includes signed zero, finite subnormal/normal boundaries and maximum finite magnitudes. The nine source provenance hashes are SHA256 of `fixture-provenance-1` through `fixture-provenance-9` in format order.

This is a synthetic format fixture, not generated music. No model weights or private paths are contained in it. The native producer is `native/e06/tests/format_test.cpp`; cross-language fixture generation is retained in the private E06 validation evidence until integration.
