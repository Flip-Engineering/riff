# Native acoustic checkpoint checks

Run these after the ordinary full native build:

```sh
elixir scripts/check_native_acoustic.exs --tokenizer models/sidecars/yue2-qwen.tiktoken
```

Use `--build PATH` for another build, `--source-repository PATH` when its source is a verified export, and `--output NEW_PATH` to choose an evidence directory. Only the small pinned tokenizer is required; no neural model weights or backend are loaded. CI reuses the tokenizer already verified by the score-replay checks.

The script verifies the full exported source against the pinned upstream revision and every ordered patch, and checks the real CLI's capability/discovery records. It exercises 73 format cases twice (normal and forced portable SHA), cross-checks block/padding digests with OTP, and runs 41 actual request/session/pipeline cases using tiny GGUF metadata fixtures and operator doubles. The latter prove stage ownership and failure recovery, not neural numerical parity. Doubles are confined to the test executable and checked absent from the production CLI.

File cases cover finite F32 bit preservation, complete/invalid headers, hashes, exact lengths, overflow, output collision, dangling symlinks, FIFO rejection, short writes and concurrent no-replace publication. Stage cases cover VAE-only source selection, captured defaults and explicit decoder refinements, completed-NAR-before-save, VAE failure followed by replay, NAR exceptions/partial output, source changes and ordinary generation/empty-score planning.

The shipping native patch also exposes these without weights:

```text
feature.yue2.acoustic_checkpoint=1
feature.yue2.acoustic_decode=1
format.yue2.acoustic=riff.yue2.acoustic.v1
```

`acoustic_latents_out` saves the completed frame-major F32 tensor before decoding. Optional `acoustic_only=true` stops there. `acoustic_latents_file` requires only the compatible VAE and skips main-model loading, planning, AR and NAR. It restores captured tile/halo/storage by default; `acoustic_decode_core_frames`, `acoustic_decode_halo_frames` and an explicit VAE storage option can refine decoding. Active symbolic/performance inputs and outputs conflict with decode-only. The application must create a dedicated decode request and retain the source recipe separately.
