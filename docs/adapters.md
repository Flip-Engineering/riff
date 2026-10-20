# YuE2 adapters and reusable conditioning

Updated October 19, 2026. The September 10–12 audit correctly described the
artifacts found then; its conclusion that no compatible public YuE2 LoRAs were
available is now superseded. **Published AR, NAR and combined AR/NAR adapters
exist, and audio.cpp has supported native YuE2 adapters since version 0.8.1.**
The [research and training handoff](yue2-lora-research.md) records exact revisions,
file hashes, tensor contracts and remaining evaluation work.

## What an adapter changes

Riff's model is **YuE2-3B**, the AR/NAR mixture of transformers released by
[M-A-P](https://huggingface.co/m-a-p/YuE2-3B). It is separate from the older
YuE stage-1/stage-2 family. Use adapters trained for YuE2-3B.

| Adapter role | Effect | Native session option |
| --- | --- | --- |
| AR / composition | Changes the planner that writes ABC and semantic music tokens; can affect song form, arrangement and vocal choices | `yue2.ar_lora` and `yue2.ar_lora_scale` |
| NAR / sound | Changes the acoustic renderer that turns the semantic stream into audio latents | `yue2.nar_lora` and `yue2.nar_lora_scale` |
| Combined AR + NAR | Contains weights for both roles; import separates and validates the two branches | Both option pairs |

The native backport is `patches/yue2-weight-adapters.patch`. Riff retains its
pinned runtime and existing patches, and admits the adapter capability explicitly.
Adapters are merged when a native session loads. Changing the selected adapter
or its strength requires a new session; a running session must not silently keep
the previous weights. A zero strength disables that role completely.

Saved **sounds** continue to be reusable text conditioning. Their style text can
be useful with or without learned adapters, and changing a saved sound does not
rewrite an existing recording's recipe.

## Use an adapter in the studio

1. Open **Style adapters** under **The sound**, then **Browse LoRAs**.
   The first studio load reads your installed library only. Opening Browse
   searches the public Hugging Face Hub through the studio backend.
2. Search or filter by genre, license, stage and header compatibility. Open
   **Listen & details** to inspect the model card, demo audio and individual
   files. Header checks do not establish musical quality or the integrity of
   undownloaded tensor data.
3. Choose one checkpoint or a nonoverlapping AR/NAR pair and install it.
   Download progress and cancellation are visible. A revision is activated
   only after size, checksum, tensor and scaling checks pass. To import a
   local export, choose **Import a file** and select its safetensors; PEFT
   files may also require their trainer's `adapter_config.json`.
4. Open **Installed**, then **Use this style**. Set **Composition** and **Sound**
   strengths independently. Two separate installed entries may supply the
   two roles, including opposite roles from two combined exports. Use zero
   to bypass a selected stage. Trigger insertion is an explicit action.
5. Generate normally. The new take records source revision, original and
   normalized hashes, effective strengths, and the captured name/license.
   Renaming an entry or changing its default does not rewrite older takes.

If Use is disabled, update the music engine from Studio settings. Names,
notes, tags, trigger words and defaults can be edited locally. Checking for an
update never replaces a pinned version: installing the new revision creates
another entry. Deletion is refused while queued/running takes need the adapter;
saved recordings trigger a reference warning. Restore the same files and
revision to rerun a recording after deleting its adapter. Already synthesized
sound can still be decoded without that adapter.

For command-line use, obtain the installed ID from `GET /api/loras`:

```sh
python3 run.py --free --max-seconds 8 --steps 8 --lora INSTALLED_ID \
  --ar-lora-strength 0.7 --nar-lora-strength 1.0
```

Only specify stage overrides present in the selected adapters. The
[API reference](loras-api.md) covers recipes, filter parameters, cache and
upload configuration, references and installation tasks.

## Published examples checked

These are concrete public artifacts with matching headers, not names inferred
from a search result. Their musical characteristics are the publishers'
descriptions; Riff's tests establish import and runtime behavior, not a listening
quality rating.

| Source | Files / role | Starting point |
| --- | --- | --- |
| [Mothersuperior instrumental](https://huggingface.co/Mothersuperior/YuE2-instrumental-cot-full-loras) | `ar_lora_inst_v3abc.bf16.safetensors`; AR | `cot=full`, ordinary style tags and `[instrumental]` or section cues in lyrics; publisher uses scale 1 |
| [Mothersuperior real-audio tooling](https://huggingface.co/Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4) | `nar_lora_joint_v4`, `v5`, `v8`, `v9`; NAR | Encode recordings with the matching semantic head and reconstruct those codes with its paired NAR adapter; text-to-music generation does not require a head |
| [TRBDR folk](https://huggingface.co/becausereasons/yue2-trbdr-folk-troubadour) | `trbdr_broadside.safetensors` and other checkpoints; combined AR/NAR | Publisher's `trbdr` trigger and model-card prompts; compare role strengths independently |

The instrumental adapter and TRBDR examples declare `m-a-p/YuE2-3B` lineage.
ComfyUI exports may identify `Comfy-Org/YuE2`, the repackaged model. The model,
the published example adapters and the real-audio toolkit are labeled
**CC BY-NC 4.0**. Preserve the source license and attribution when importing an
adapter; an absent or unclear license must remain unknown rather than acquiring
the base model's license automatically.

## Formats and strength

Native Mothersuperior files contain separate `lora_A` and `lora_B` matrices.
Their update is `strength * (B @ A)`, with no implicit division by rank.
PEFT exports can carry `lora_alpha / rank` or rsLoRA scaling in
`adapter_config.json`; that coefficient must be preserved. ComfyUI files contain
`lora_down.weight` and `lora_up.weight`, sometimes with an `alpha` tensor. Their
QKV and gate/up matrices can be fused. Import validates the names, shapes, roles
and scaling convention before the file can be activated.

These checks establish architecture and format compatibility. They cannot prove
which exact base weights were used in training, or that a declared companion
adapter can be omitted. Check the publisher's base and companion requirements
even when the file passes import validation.

AI Toolkit's checked YuE2 exporter uses A/B `.weight` names, forces alpha equal
to rank and omits alpha tensors. Those producer-identified exports have a unit
coefficient. A generic PEFT file with missing scaling metadata does not inherit
that assumption just because its suffix looks the same.

Two NAR projection formats have different meanings:

- Native `vae2llm.weight`, `vae2llm.bias`, `llm2vae.weight` and `llm2vae.bias`
  are **complete replacement weights**. At any nonzero NAR strength they stay at
  their saved value while the LoRA deltas change strength. Zero disables both
  the replacements and the deltas.
- ComfyUI `.diff` and `.diff_b` entries are **additive projection changes**.
  Their change is scaled with the adapter. They cannot be renamed to native
  replacement tensors without changing their meaning.

This means a native NAR adapter at strength 0.01 does not necessarily sound almost
like the unadapted model. Use zero for an actual bypass comparison. Converting
between the two projection conventions also requires the correct base weights.

Riff's usual Q4 main model is a quantized base. Applying an adapter to its
dequantized weights and quantizing the result is not numerically equivalent to
merging into the original BF16 model first. Use a BF16 main model for a reference
comparison if adapter fidelity is in question. An adapter is not itself a GGUF
checkpoint, VAE, semantic tokenizer or voice-conversion model.

## Reproduction and local training

Retain the exact adapter file identity, role, source revision, source license,
scaling metadata and selected strengths with the recipe. Hash checking must
cover the imported files and normalized output; a same-named replacement must
not quietly change a queued job or a warm session. Score/prefix replay also needs
the correct adapter identity because an AR adapter changes the model that
produced that conditioning.

The community has now released a usable real-audio semantic encoder and several
training implementations. See [the training handoff](yue2-lora-research.md#training-handoff)
for the checked AI Toolkit path. Riff includes a standard-library-only
`scripts/prepare_lora_training.py` helper to validate sidecars, copy distinct
train/held-out snapshots, write a training configuration and establish an
evaluation plan. It performs no training or downloads. A completed preparation
manifest is not evidence that a useful adapter has been trained.

## Conditioning still works without an adapter

Free play leaves lyrics and style empty unless a sound description is supplied.
Riff's optional-conditioning patch preserves the model pipeline while allowing
empty conditioning. No artificial section marker is inserted for free play.

Instrumental mode supplies an `[Instrumental]` cue when lyric text is empty and a
basic instrumental direction when the sound is blank. Artist-written style and
lyric cues are retained. This is a prompt cue, not a guarantee of vocal
suppression. The learned instrumental AR adapter is an additional, distinct
option; its model card recommends its own section cues and score-first mode.
