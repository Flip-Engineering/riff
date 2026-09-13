# Exact score replay

The native request option `score_tokens_file` accepts a saved score JSON object:

```json
{"tokens": [55, 25, 16], "truncated": false}
```

`tokens` is required and may be empty. Its values are JSON integer lexemes in the
ABC sampler's range, `0 <= id < 151643`. `truncated` is optional and defaults to
false. Unknown metadata does not act as a score field, while duplicate top-level field
names and malformed score fields are rejected. Tokenizer and prefix compatibility
must be checked by the caller before passing an artifact to the engine.

Replay uses `cot=melody` or `cot=full`. It excludes `abc` and `abc_file`, including
explicit empty text options. Existing generated-score, supplied-text, and direct
generation modes keep their behavior. Repeated generic CLI options retain the
upstream CLI's last-value-wins behavior; this differs from duplicate JSON fields.

Model help and inspection advertise these additive capability lines without
loading model weights or creating an execution backend:

```text
feature.yue2.score_tokens=1
format.yue2.score_tokens=riff.yue2.score-tokens.v1
format.yue2.prefix=riff.yue2.prefix.v1
```

An older engine does not advertise this contract. Callers can detect that before
submitting an exact-score request. The original raw score IDs, including an empty
score, are separate from display text: the fixture demonstrates 37 IDs that
decode to the same text that the native tokenizer re-encodes as 29 IDs.

Run the regression against a fresh native Unix Makefiles build:

```sh
elixir scripts/check_native_score_replay.exs --build /path/to/build --tokenizer /path/to/yue2-qwen.tiktoken
```

For a desktop source export, also pass `--source-repository /path/to/audio.cpp`
pointing to the pinned Git revision. CI uses `--download-tokenizer` to fetch only
the size- and SHA-verified tokenizer sidecar; the test does not need model weights.
An optional `--output` selects a fresh directory for preserved logs and receipts.

The checker applies the pinned patch set to its own Git index, verifies the full
built source tree, and compiles the original production pipeline from immediately
before this patch. A test-only AR sampler boundary returns declared ABC IDs and
records the subsequent semantic sampler inputs. The test compares original and
replay positive/CFG-negative prefix arrays, delimiters, truncation, seed and
guidance arguments, as well as empty scores and changed lyrics, style and mode.
Separate cases exercise the actual native file parser, integer precision, full
Unicode/NUL field identity, invalid ranges and preserved existing modes.

The test double forbids NAR/VAE and backend operations, and the checker verifies
it is absent from the production binary. These CPU checks establish parser and
conditioning behavior. Neural numerical acceptance, latency and device coverage
are separate checks; they are not inferred from these tests.

When `semantic_codes_file` is supplied, the existing pipeline returns the saved
performance before writing a new `score_tokens_out` file. Applications should
carry the verified score artifact forward for that operation.
