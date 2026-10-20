# Native YuE2 LoRA verification

Build the pinned runtime with all Riff patches, then run:

```sh
python3 setup_engine.py --backend cpu --compile-only
python3 scripts/check_native_lora.py --build /path/to/build --output /tmp/riff-lora-check
```

For a configured engine, `--build` can be omitted. The checks always initialize the
CPU backend, including when the production build also supports Metal. No base-model
weights, public adapters, tokenizer downloads, credentials or paid requests are needed.
The same command runs in native CI. CUDA remains a compile gate; CPU tensor results
are not CUDA or Metal inference evidence.

## What is checked

The script verifies every patch against `sources.json`, reconstructs the complete
pinned source tree with a temporary Git index, and checks the production CLI's exact
capability records and session options. It also sends the real CLI a warm job with a
request-scoped adapter change and requires an explicit error before GGUF loading.
LoRA paths and strengths belong to a session; Riff restarts its warm process when
that selection changes.

It compiles the current native reader, binder and source into an executable linked
with the production runtime and ggml libraries. Forty-four synthetic cases cover:

- Native F32, BF16 and F16 A/B factors, explicit alpha/r, and PEFT configuration.
- Real published ComfyUI AR/NAR prefix conventions, grouped-query QKV slicing and
  fused gate/up slicing, with unequal Q and K/V output widths.
- Independent positive/negative AR and NAR strengths and unchanged opposite-role
  weights.
- NAR full projection replacements and ComfyUI dense weight/bias differences.
- An independent BF16 rounding reference and real Q4 GGUF dequantization.
- CPU uploads and reloads in F32, F16, BF16, Q8_0, Q4_0 and native storage.
- Unmodified base bytes, repeated reads, cold storage release/reopen, changed
  strength isolation, and bit-exact zero-strength Q4 upload.
- Missing pairs, wrong roles/layers/shapes, nonfinite factors/replacements/strengths,
  unsupported full weights, fused/split conflicts, duplicate names, malformed
  offsets, truncated data, oversized headers/integer dimensions, unsupported LoKr algorithms and dtypes.

`receipt.json` binds source/test inputs, the production and test binaries, runtime
revision and capabilities to the result. `numerical.log` lists each case.

These checks establish tensor arithmetic, import validation and weight ownership.
They do not establish full-song numerical equivalence, perceptual quality, hardware
latency, peak process memory or the musical effect of any public adapter. Those
require the matching base weights and a real listening/measurement run. In
particular, an update merged over a Q4 base starts from the **dequantized Q4 values**;
it cannot recreate an unquantized training checkpoint exactly.

A separate measured short generation with the pinned Q4 model and a public
AR/NAR adapter is documented in [LoRA validation](../../docs/lora-validation.md).

## Native contract

`patches/yue2-weight-adapters.patch` is the final ordered patch on the existing
`fbe3eedbf6c504e45189e2cdcf1b257740a28863` runtime. It advertises
`feature.yue2.lora=1` and `format.yue2.lora=riff.yue2.lora.v1`.

The four CLI **session** options are:

```text
--session-option yue2.ar_lora=/absolute/path/ar.safetensors
--session-option yue2.ar_lora_scale=1.0
--session-option yue2.nar_lora=/absolute/path/nar.safetensors
--session-option yue2.nar_lora_scale=1.0
```

Relative paths resolve beneath the model root. Native keys are
`layers.N.self_attn.PROJECTION.lora_A` / `.lora_B` and
`layers.N.mlp.PROJECTION.lora_A` / `.lora_B`; NAR adds `nar_` to `self_attn`/`mlp`.
An optional `model.` prefix is accepted. A has `[rank, input]` shape and B has
`[output, rank]` shape. The binder verifies actual base-model tensor dimensions and
layer count. Q/K/V/O and gate/up/down projections are supported; fused `qkv_proj`
and `gate_up_proj` outputs are split into the correct output rows.

Native A/B uses direct strength unless an explicit scalar `.alpha` is provided.
Then the coefficient is `strength * alpha / rank`. PEFT A/B uses sibling
`adapter_config.json` scaling (including declared rank and alpha patterns);
unsupported DoRA, transposed, QALoRA and bias modes fail explicitly. Riff's library
normalizes public imports and preserves their effective scaling in each stage file.
ComfyUI AR `text_encoders.model.layers.*` and NAR
`diffusion_model.model.layers.*` bindings are accepted with `lora_down.weight` and
`lora_up.weight` factors. F32, F16 and BF16 safetensors are accepted.

NAR may include full `vae2llm.weight`, `vae2llm.bias`, `llm2vae.weight` and
`llm2vae.bias` replacements. At **any nonzero strength**, these replace the base
projection without scaling, matching native YuE2 adapters. ComfyUI `.diff` and
`.diff_b` are **dense differences** and are multiplied by NAR strength before
addition to the base. An update and full replacement targeting the same tensor
are rejected instead of silently overriding one another.

A zero stage strength bypasses its entire adapter, including full projections and
file opening, and preserves the original source and quantized bytes. All strengths
must be finite. Selection is immutable for one session; per-request attempts to
change a path or strength are rejected. Acoustic VAE-only replay does not load
main-model adapters; adapter provenance remains in Riff's saved run/lineage data.
The existing native acoustic header continues to identify the base GGUF.

## Backport and memory decisions

The reader/binder and YuE2 mappings derive from Apache-2.0 audio.cpp revision
[`548e5afc26a8a21300412cebb8528f3329fd3e0a`](https://github.com/0xShug0/audio.cpp/tree/548e5afc26a8a21300412cebb8528f3329fd3e0a),
including the generic importer introduced by
[`fcb461939f6da2262f574eb038d6e8a3bbdc9a6e`](https://github.com/0xShug0/audio.cpp/commit/fcb461939f6da2262f574eb038d6e8a3bbdc9a6e).
The implementation retains upstream attribution and Riff change notices;
`patches/LICENSE` contains the full license. It does not upgrade the pinned runtime
or alter Riff's existing score, solver, checkpoint, attention or warm-engine patches.

The merge preserves upstream's BF16-rounded delta convention. Riff adds bounded
safetensors validation, nonfinite-value checks, full/delta conflict detection,
ComfyUI NAR projection differences and exact zero bypass. The source retains adapter
factors as F32 and merges each requested weight over the immutable base. The low-rank
product uses one row of scratch; no full adapted model or precision-variant cache is
retained on the host. Existing warm AR/NAR runtimes own uploaded resident weights.
Cold reloads recompute a merge, trading startup work for lower host retention.

For the pinned YuE2 architecture, a 6144 by 2048 F32 base-weight merge buffer is
48 MiB, plus a row of product scratch and the requested upload conversion buffer.
This is an allocation-size calculation, not a measured peak-process-memory claim.
Adapter F32 expansion, fused-factor copies, base-source mapping and runtime/device
allocations must also be included in any admission or memory measurement.
