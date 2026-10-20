# YuE2 LoRA research and training handoff

Checked **October 19, 2026** against primary repositories, model cards, live
Hugging Face revision/file metadata and the headers of five published adapter
files. This supersedes the September 10–12 adapter availability findings and
the premise of issues #7 and #23 that a compatible real-audio encoder or native
adapter path was still unavailable.

**The software path now exists.** Public weights cover AR, NAR and combined
adaptation; native inference can apply them; community training can turn a
folder of recordings into an adapter. A quality claim for a newly trained Riff
adapter still requires source recordings, a GPU training run and held-out
listening evidence. This work does not report such a run.

## 1. Model and release identity

The relevant model is [`m-a-p/YuE2-3B`][base], with a 28-layer AR branch that
generates an ABC plan and semantic tokens, a 28-layer NAR branch that predicts
VAE latents with flow matching, and a separate 48 kHz stereo VAE. This is not the
older YuE `YuE-s1-7B` / `YuE-s2-1B` pipeline. Old YuE PEFT training recipes do not
establish YuE2 adapter compatibility.

| Component | Revision checked |
| --- | --- |
| Official YuE repository | `1647252d68b70cbead046ffd3f7caf036dde47ee` |
| Official YuE2-3B model repository | `8f24312f187763b9854adeec87e437e2b03bbff1` |
| Comfy-Org YuE2 repack | `2f76ca75e6ee094169de899cc7fc99d6887e2196` |
| Current audio.cpp main inspected | `548e5afc26a8a21300412cebb8528f3329fd3e0a` |
| AI Toolkit inspected | `04733873cf1da8060fce01316ec45daedbd4a918` |

The official model card describes inference rather than a released official
audio-to-semantic-token training encoder. The absence of that official encoder
is no longer a universal training blocker: the community encoder below
implements a compatible approximate path. The [AI Toolkit source][trainer]
explicitly makes this distinction.

### Native support is already upstream

| Change | Commit / release |
| --- | --- |
| Initial AR LoRA | [`0d67b8d03c14d1329da70e24ba8966e3c6dc8304`](https://github.com/0xShug0/audio.cpp/commit/0d67b8d03c14d1329da70e24ba8966e3c6dc8304), September 16 |
| Cache merged adapter weights within a session | [`b09ba02f14f8783427e2bf069595dcd80ce5af4e`](https://github.com/0xShug0/audio.cpp/commit/b09ba02f14f8783427e2bf069595dcd80ce5af4e) |
| Independent AR and NAR options | [`828640e86f1994b0508bc4c6e965ff1583761b49`](https://github.com/0xShug0/audio.cpp/commit/828640e86f1994b0508bc4c6e965ff1583761b49), September 17; [PR #586](https://github.com/0xShug0/audio.cpp/pull/586) |
| First release containing both roles | [v0.8.1](https://github.com/0xShug0/audio.cpp/releases/tag/v0.8.1), September 17 at 22:34 UTC |
| Generic adapter importer, ComfyUI and matrix LoKr work | [`fcb461939f6da2262f574eb038d6e8a3bbdc9a6e`](https://github.com/0xShug0/audio.cpp/commit/fcb461939f6da2262f574eb038d6e8a3bbdc9a6e), October 19; [PR #974](https://github.com/0xShug0/audio.cpp/pull/974) |

The [GGUF publisher's model card][gguf] also identifies v0.8.1 as the adapter
support release. Riff's old `fbe3eed...` pin predates it. Riff supplies
`patches/yue2-weight-adapters.patch` over its known runtime, preserving the
existing score, recovery and warm-engine patches.

The upstream [model bindings][native] and [serialization implementation][importer]
were inspected separately. At the checked main revision, a broad statement of
ComfyUI support does not cover every actual NAR export: the generic importer
does not parse the community NAR `.diff` / `.diff_b` projection entries, and its
YuE2 binding table lacks the `diffusion_model.*` branch mapping. A combined
AR/NAR file also needs deliberate role separation. Riff validates actual tensors
rather than accepting a file because its extension or model card says LoRA.

## 2. Published artifacts with inspected headers

### Instrumental AR

[`Mothersuperior/YuE2-instrumental-cot-full-loras`][instrumental], revision
`947f2f4b28978b2b6c3e316e6a87925c76bf3c4b`:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `ar_lora_inst_v3abc.bf16.safetensors` | 139,502,088 | `e408fd3148b75b1165f7ddbf63db575d83bb6402a0b5f876fcb767dbcb2c5414` |
| `ar_lora_inst_v3abc_comfyui.safetensors` | 212,891,736 | `de6a11d5701df103a191c87dc73115266e2420f3834739319dec5c24d2119f31` |

The raw file contains 392 BF16 tensors: A/B factors for seven projections in
each of 28 AR layers, rank 64, direct scale 1. Its metadata names the YuE2-3B
base, the score-first recipe and the delta rule. The ComfyUI file contains 224
BF16 tensors, with Q/K/V and gate/up fused. The publisher recommends `cot=full`
and structural cues in the lyrics field. It can run with the stock NAR renderer;
the publisher's demos use a community NAR companion.

### Real-audio NAR and semantic heads

[`Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4`][realaudio], revision
`e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5`, contains v4, v5, v8 and v9 pairs;
the repository name is not a limit to v4.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `nar_lora_joint_v9.bf16.safetensors` | 70,301,896 | `ce57f98e9e0e65c724c2a00aa23dea2568952590504a428c588d82f00bc61c55` |
| `nar_lora_joint_v9_comfyui.safetensors` | 107,518,800 | `cd2bf0d3ce4bf68697daa016671da7d70e34e2f5545617ab9094c96b14a581db` |
| `tokenizer_head_joint_v9.safetensors` | 171,278,808 | `06440f25605c6c12c4e517ef033d7b70b8d9e961b3b06aa3848b1f8028fd7eb8` |

The raw NAR file has 396 tensors: rank-32 A/B factors for seven projections in
28 layers plus four full projection replacement tensors. Its metadata names
`tokenizer_head_joint_v9` as its companion. The ComfyUI NAR file has 228 tensors:
224 fused LoRA factors plus F32 `diff`/`diff_b` projection changes.

The v9 pair adds audio-domain loss to the earlier latent-domain training. Its
publisher reports improvements on three held-out tracks from rock, metal and
bass material. This is a small, publisher-run evaluation, not universal
superiority or accurate reconstruction in every language and genre. The card
still reports occasional token-choice errors. Retain each pair's version when
encoding and reconstructing real recordings: the matching semantic head encodes
audio, and its paired NAR adapter reconstructs those codes. The head is not
loaded when generating new music from text and an adapter.

### Combined artist/style LoRA

[`becausereasons/yue2-trbdr-folk-troubadour`][trbdr], revision
`078b19e52baebaa71463ea94afac5ce13abfe29a`:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `trbdr_broadside.safetensors` | 117,500,816 | `68e3aca03e7aa7ddf8a2ee5cf46ce68c2c18fc9fe432cc60b838b9f0e9b8171f` |

Its header contains 448 BF16 tensors across AR and NAR transformer blocks,
with no projection diffs. Metadata identifies AI Toolkit 0.13.16 and step 700.
The model card describes an English folk/protest-ballad adapter with trigger
`trbdr`, and distinguishes these AI Toolkit files from its FS_Audio files
that include projection diffs. Other public sets include
[Italian canzone](https://huggingface.co/becausereasons/yue2-cnzn-canzone-italiana),
[French chanson](https://huggingface.co/becausereasons/yue2-chnsn-chanson-francaise)
and [raspy rock/soul](https://huggingface.co/becausereasons/yue2-grvl-raspy-rock-soul).
Those are model-card availability findings; their other weight headers were not
all inspected in this audit.

All three inspected adapter repositories label their releases CC BY-NC 4.0.
Record each downloaded artifact's actual source license and attribution.
Dataset permission is a separate provenance field and has no automatic default.

## 3. Tensor and numerical contract

For YuE2-3B, hidden width is 2048, intermediate width 6144, Q output width 2048,
and K/V output width 1024. There are 28 layers. The actual files match the
[model configuration and implementation][model].

| Branch/layout | Example target before factor suffix |
| --- | --- |
| Native AR | `layers.0.self_attn.q_proj`, `layers.0.mlp.gate_proj` |
| Native NAR | `layers.0.nar_self_attn.q_proj`, `layers.0.nar_mlp.gate_proj` |
| Comfy / AI Toolkit AR | `text_encoders.model.layers.0.self_attn.qkv_proj` |
| Comfy / AI Toolkit NAR | `diffusion_model.model.layers.0.self_attn.qkv_proj` |

Native factors end in `.lora_A` and `.lora_B`; PEFT factors normally add
`.weight` or `.default.weight`; Comfy factors end in `.lora_down.weight` and
`.lora_up.weight`. A has shape `[rank, input]`, B `[output, rank]`. The direct
native convention is `W + strength * B@A`. PEFT may add alpha/rank or rsLoRA's
alpha/sqrt(rank), which must survive normalization. Comfy uses per-target alpha
when present; absence means a direct unit coefficient.

The checked AI Toolkit producer [forces alpha equal to rank][network] for
transformer PEFT export, then [omits alpha and renames its factors][exporter].
This verifies a direct unit coefficient for the producer-identified TRBDR
A/B `.weight` file. It does not establish scaling for arbitrary PEFT exports
without an adapter configuration.

Header validation establishes YuE2 architecture and format compatibility; it
does not establish the exact base checkpoint used during training. Source
metadata and model cards supply that lineage. In particular, a delta trained
after premerging a companion can pass the same shape checks while still
requiring that companion at inference.

Fused QKV uses output slices `[0:2048]`, `[2048:3072]`, `[3072:4096]`; fused
gate/up uses `[0:6144]` and `[6144:12288]`. Slicing the up/B factor while
retaining its paired down/A factor preserves the full update. Do not assume a
fused file's rank equals the original separate rank: the inspected instrumental
file uses rank 192 for QKV and 128 for gate/up, from separate rank-64 factors.

Native NAR `vae2llm` and `llm2vae` weights/biases are complete replacements and
remain unscaled for any nonzero adapter strength. Comfy projection diffs are
additive and scaled. Zero must disable both forms entirely. A pair with no
matching targets, missing factors, unexpected dimensions, duplicate/overlapping
targets or non-finite scales must fail before model state is changed.

Upstream YuE2's merge policy rounds the scaled delta to BF16, adds it to the
base and rounds again: `BF16(W + BF16(strength * B@A))`. Quantized inference
then follows its upload dtype path. Applying this to an already-quantized base
does not recreate a BF16-first merge. Immutable base weights, per-session
merged caches and new sessions for adapter changes avoid cumulative application
and stale model state.

## 4. The real-audio semantic encoder

The released community encoder is not simply the VAE encoder. It supplies the
discrete semantic stream that the AR model learns to predict. Its checked
[AI Toolkit implementation][tokenizer] performs:

1. Downmix to mono and resample to **24 kHz**.
2. Extract **MERT-v2-FullSong layer-20**, 1024-dimensional features, in 30-second
   chunks; interpolate to **25 frames/second**.
3. Normalize each feature dimension over the whole recording.
4. Run an **8-layer, width-512, 8-head** transformer classifier over 512-frame
   overlapping windows, trimming overlapping edges.
5. Emit integers **0–32767**, one semantic code per audio frame. These are raw
   codec IDs, not already-offset language-model token IDs.

The model's `CODEC_OFFSET` is **151853**; AR training/generation offsets the raw
codes when inserting them into the joint vocabulary. `MUSIC_START=151851`,
`MUSIC_END=151852`, `ABC_START=151847`, `ABC_END=151848`. Mixing raw codec IDs,
text token IDs and 64-channel VAE latents silently would corrupt conditioning;
the [model code][model] states their separate roles.

The community's staged recipe includes MERT/VAE preprocessing, lyric alignment,
semantic tokenization, optional head+NAR adaptation and an AR LoRA with a 50/50
mixture of artist tracks and a minted regularizer corpus. Its published scripts
contain pod-specific paths and pinned assumptions. A fresh clone is not
automatically a portable one-command trainer. The [Mothersuperior card][realaudio]
documents a 24 GB GPU configuration, with 14–18 GB reported for its checkpointed
stages. These are publisher measurements for that recipe, not a memory guarantee
for AI Toolkit.

## Training handoff

### Checked training path

AI Toolkit at the pinned commit trains both experts: AR next-token cross entropy
and an optional `KL(base || adapted)` anchor, plus NAR flow loss on random audio
windows. The NAR loss does not train the AR through its key/value cache. It
loads a Comfy-Org BF16 or ConvRot int8 checkpoint, and uses the community semantic
head for latent caching. Export maps `transformer.ar.*` to `text_encoders.*`
and `transformer.nar.*` to `diffusion_model.*`.

The latest YuE2-specific loader change inspected was
[`d0ecde7779417768216c134d017c488a84f8ef50`](https://github.com/ostris/ai-toolkit/commit/d0ecde7779417768216c134d017c488a84f8ef50),
September 21: safetensors v5/v8/v9 heads and NAR adapters, plus head-specific
latent cache naming. Use `.safetensors` for the handoff rather than the old
pickle-based `.pt` defaults.

The provided preparation helper selects:

| Setting | Value / reason |
| --- | --- |
| Network | Rank 32, alpha 32, transformer blocks only; both AR and NAR are inside the trained transformer |
| Base | Pinned ConvRot int8 checkpoint; `quantize=true`, `qtype=convrot8` |
| Semantic head | Pinned v9 F32 safetensors |
| NAR premerge | `false`; exported trained deltas do not acquire an undeclared premerged companion |
| Caption dropout | 0; retain the complete lyric/style prefix |
| Score mode/dropout | `cot=full`, `abc_dropout=0.5`; expose score-first and score-free paths |
| Loss/optimizer | AR weight 1, KL 0.2, NAR flow matching; `adamw8bit`, LR `5e-5`, AR LR multiplier 0.6 |
| Training window | 1500 NAR frames = 60 seconds; AR starts at the song start and `ar_max_tokens=0` retains full-song supervision |
| Sampling | Disabled during training; evaluate saved checkpoints in separate sessions |
| First sweep | At most 20 nominal passes per training song, capped at 2000 steps; an initial research budget, not a proven optimum |

These are conservative integration choices based on checked code and
[reported practical runs][issue1052], not a claim that this exact combination
has been trained here. Full-song AR supervision can use more memory than the
report's 4000-token cap; reduce a cap deliberately only if accepting truncated
supervision. The original voice/style reports used the older head, so the v9
choice also needs dataset-specific evaluation.

`merge_nar_lora=true` is a different recipe: [the trainer rejects it on the
int8 checkpoint][model] and requires BF16 weights. If a companion is merged
before training, every exported delta depends on that adapted base. Record and
reapply the companion, or produce a correctly rebased export; do not label it a
standalone stock-base LoRA.

### Prepare a local run

Use full recordings and accurate full lyrics. The helper accepts either
`song.wav` + `song.txt` containing style and `[Lyrics]`, or `song.wav` +
`song.txt` containing style + `song.lyrics.txt`. It accepts instrumental section
cues too. WAV, FLAC, MP3, M4A, OGG and Opus are recognized; actual decoding and
lyric accuracy remain the trainer/reviewer's responsibility.

```bash
python scripts/prepare_lora_training.py prepare /path/to/songs \
  --output /path/to/my_yue2_run \
  --name my_style \
  --dataset-license "Describe the actual recording license or permission"

python scripts/prepare_lora_training.py verify /path/to/my_yue2_run
```

The output contains independent copies in `train/` and `heldout/`, `train.json`,
`manifest.json` and `evaluation.json`. JSON is directly supported by AI Toolkit;
no YAML library is needed to prepare it. The helper requires at least three
distinct recordings, detects duplicates by file SHA-256 before splitting, and
keeps held-out paths out of the training configuration. It also detects extra
audio later added to either split. Exact-file hashing cannot recognize a
transcoded copy, another excerpt of the same song or an alternate master: remove
those song-family overlaps manually before preparing the run.

The split is deterministic from audio hashes and `--seed`, so renaming files
does not change membership. The seed also defines the evaluation plan; it does
not claim deterministic GPU training. License/permission must be supplied
explicitly. Existing output directories and source/output overlap are rejected.
Moving the prepared folder changes its absolute configuration paths; prepare a
new folder instead.

The three pinned training assets are listed with byte sizes and hashes in the
manifest. Download them explicitly into the prepared `assets/` directory:

```bash
hf download Comfy-Org/YuE2 \
  checkpoints/yue2_3b_int8_convrot.safetensors \
  audio_encoders/sheetsage2_bf16.safetensors \
  --revision 2f76ca75e6ee094169de899cc7fc99d6887e2196 \
  --local-dir /path/to/my_yue2_run/assets

hf download Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4 \
  tokenizer_head_joint_v9.safetensors \
  --revision e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5 \
  --local-dir /path/to/my_yue2_run/assets

python scripts/prepare_lora_training.py verify /path/to/my_yue2_run --verify-assets
```

In a separate GPU training environment, install AI Toolkit using its own
instructions, check out `04733873cf1da8060fce01316ec45daedbd4a918`, and run:

```bash
python run.py /path/to/my_yue2_run/train.json
```

The checked trainer resolves MERT and SheetSage configuration/remote code from
repository defaults, with no per-model revision option in this path. The audit
observed MERT revision `7001f7657276238629e61c7746ce70735e0604c0` and SheetSage2
revision `b83c3d79a6d98bcffaf8f9ed112c90aa887887bc`. Record the actual resolved
code/cache revisions, package lock, CUDA/PyTorch versions, hardware, training
logs and checkpoint hashes. The manifest explicitly says
`dependency_lock_complete=false`: its file pins do not turn unpinned external
code resolution into a fully locked experiment.

### Known training limits to evaluate

AI Toolkit's own [UI/model notes][trainer-ui] label YuE2 training experimental
and warn that a small AR dataset can collapse into memorization. Lower training
loss is insufficient evidence of an improved music model. The
[September 20 Windows report][issue1052] also describes growing shared-memory
use, a post-sampling CUDA graph crash and audible high-frequency degradation in
later checkpoints. Those reports motivate separate checkpoint evaluation and
the short initial sweep; they do not establish a universal 20-pass cutoff.

The stock cache records score mode and derives a cache suffix from a nondefault
head's filename. Replacing a head file in place does not create a reliably
distinct cache. Keep pinned content and use a fresh prepared run when changing
the head, caption data or score mode. Held-out assessment also needs a
real-audio encode/render round-trip: poor semantic reconstruction is a separate
limitation from AR learning and cannot be fixed by more AR training alone.

### Evaluation and acceptance evidence

`evaluation.json` is an uncompleted plan with a held-out prompt and new lyrics,
one fixed seed, and four conditions: base, AR only, NAR only and paired. Render
all conditions with the same base/VAE, runtime, prompt and sampling settings.
Keep both role strengths in every recipe. The new lyrics prevent the only
comparison from being reproduction of familiar training words.

For each saved checkpoint retain adapter/base/VAE hashes, runtime commit and
patch hashes, exact recipes, output audio hashes and human assessment of lyric
accuracy, voice/style, structure/ending, harsh high-frequency artifacts and
recognizable training-song replay. Listen to more than the training prompts.
If either branch fails, retain that finding rather than silently dropping its
weights from an allegedly complete adapter.

The preparation verifier checks dataset/configuration integrity and, optionally,
three known asset files. It does not validate GPU execution, decoded audio,
musical quality, rights clearance or an evaluation result. `trained=false` and
`completed=false` stay explicit until a separate actual run and review exist.

## Scope resolved and still empirical

The previous missing-public-encoder premise is resolved by the community
release, and the native adapter path has upstream implementation evidence.
Riff can implement validated import, application, source identity and a training
handoff against current files. Native tests can establish merge arithmetic,
disabled parity, role isolation and rejection behavior without a multi-gigabyte
model. Device-specific generation/latency/memory benchmarks and a useful new
artist/style adapter still require corresponding hardware and recordings.

[base]: https://huggingface.co/m-a-p/YuE2-3B
[gguf]: https://huggingface.co/audio-cpp/Yue2-3B-GGUF
[instrumental]: https://huggingface.co/Mothersuperior/YuE2-instrumental-cot-full-loras
[realaudio]: https://huggingface.co/Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4
[trbdr]: https://huggingface.co/becausereasons/yue2-trbdr-folk-troubadour
[native]: https://github.com/0xShug0/audio.cpp/blob/548e5afc26a8a21300412cebb8528f3329fd3e0a/src/models/yue2/lora.cpp
[importer]: https://github.com/0xShug0/audio.cpp/blob/548e5afc26a8a21300412cebb8528f3329fd3e0a/src/framework/assets/weight_adapter.cpp
[trainer]: https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/extensions_built_in/audio_models/yue2/yue2_model.py
[trainer-ui]: https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/extensions_built_in/audio_models/ui.tsx
[tokenizer]: https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/extensions_built_in/audio_models/yue2/src/tokenizer.py
[model]: https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/extensions_built_in/audio_models/yue2/src/model.py
[network]: https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/toolkit/lora_special.py
[exporter]: https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/toolkit/network_mixins.py
[issue1052]: https://github.com/ostris/ai-toolkit/issues/1052
