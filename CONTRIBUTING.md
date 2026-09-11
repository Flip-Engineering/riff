# Contributing

Riff's studio server uses Python's standard library; web assets are served directly.
Run `python3 studio.py --check` for a startup preflight and `python3 -m unittest
discover -s tests -v` for backend tests.

Browser tests use isolated temporary libraries, credential doubles and provider
doubles. Install development dependencies with `npm ci` and
`npx playwright install chromium`, then run `npm run test:all-browser`. No model
weights, personal API keys, or paid requests are needed for these checks.

Native builds use `python3 setup_engine.py --backend metal --build-only` or
`--backend cpu` / `--backend cuda`. CUDA builds need an NVIDIA toolkit; building
without a GPU requires an explicit `--cuda-arch`. CI distinguishes compile/device
probes from inference on real weights.

Do not commit `data/`, `outputs/`, models, keys, local logs or install folders.
Keep UI copy musical, make suggestions editable, and document actual engine limits.
New low-memory claims need measured peak process memory and duration/settings.
Read `DESIGN.md` before changing the studio's interaction model.

Release maintainers increment `VERSION` and package.json together, run the checks,
and create a `vX.Y.Z` tag. The release workflow builds a curated archive, checks
installation in a fresh temporary workspace, and publishes its SHA-256 checksum.
The installer and updater only consume official release assets.
