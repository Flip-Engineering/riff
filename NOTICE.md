# Third-party notices

Riff application code is MIT licensed. Models and separately installed components
retain their own licenses.

- **YuE2**, Multimodal Art Projection: [CC BY-NC 4.0](https://huggingface.co/m-a-p/YuE2-3B).
  The installer fetches separately hosted GGUF weights from
  [audio-cpp/Yue2-3B-GGUF](https://huggingface.co/audio-cpp/Yue2-3B-GGUF).
  Weights are not included in Riff releases.
- **audio.cpp**: [Apache 2.0](https://github.com/0xShug0/audio.cpp/blob/fbe3eedbf6c504e45189e2cdcf1b257740a28863/LICENSE).
  The installer fetches its pinned source and dependencies, retaining their license
  files. Patches in this repository identify changes for Riff.
- **abcjs 6.7.0**, Paul Rosen and contributors: MIT. The unmodified browser bundle
  and its license are in `web/vendor/`.
- **Qwen3-0.6B-MLX-4bit**, optional writer: Apache 2.0. The pinned model and its
  license are downloaded only by the optional writer setup.
- **MLX / MLX LM**, optional writer runtime: MIT. Versions are pinned in
  `requirements-writer.lock`.
- **CMake**, when setup installs it: BSD 3-Clause, distributed through the pinned
  Python CMake package with its notices.
- **Playwright**, development tests only: Apache 2.0.

Flip character geometry and Flip-Default branding are supplied by Flip Engineering
for Riff. Source versions and checksums for installed model/runtime assets are
recorded in `sources.json`.
