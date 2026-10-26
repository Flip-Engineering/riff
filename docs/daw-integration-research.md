# DAW integration research brief

Tracking issue: [Riff #78](https://github.com/Flip-Engineering/riff/issues/78).
Branch: [research/riff-daw-integration-20261025](https://github.com/Flip-Engineering/riff/tree/research/riff-daw-integration-20261025), targeting main.
Initial source base: c4c2e23eb933daebc8772bfb34ec372afb5c75ae.

The user authorized selecting an existing high-quality open-source DAW and integrating it into Riff for agent-controlled composition and production beyond YuE2, including planned ABC score composition per instrument. This brief records the assignment; no DAW or detailed integration architecture has been selected.

The first deliverable is an evidence-backed comparison, selection, and integration proposal, using actual Riff source and official upstream documentation/source. Present it for root review before detailed architecture or substantial implementation. Reversible feasibility investigations may substantiate it. Preserve existing music workflows and the independent musician's ongoing creative work.

Compare credible systems across licensing/distribution, programmatic and headless operation, per-instrument score and MIDI handling, sound generation and instrument hosting, arrangement and editing, mixing, automation, project persistence/reopening, and audio export. Trace ABC voices, note/rest timing, pitch, key/meter/tempo, repeats, articulations, dynamics and instrument intentions as supported by actual converters; distinguish preserved semantics, explicit conversions, approximations and unsupported information. Do not claim unsupported lossless conversion or equate a score voice label with verified instrument timbre.

Inspect Riff's symbolic planning, artifact and lineage contracts, operation/queue surface, capabilities, source/release packaging, UI workflow and resource admission. Relevant context includes #17's YuE2 limits and earlier deferral, #14's workflow aims, #15's resource work, merged PR #44, and pending #47/#75. The new explicit user authorization opens this separate integration investigation; it does not change the musician's direction or silently replace existing YuE2 behavior.

## Ensemble

Tight-coupled ensemble: riff-daw-integration-20261025, owned by riff-backlog-conductor-20261019.

- Conductor: riff-daw-conductor-kimi-20261025, omp / kimi-code/k3 / max.
- Worker: riff-daw-deepseek-20261025, dsh / deepseek-flash / max.
- Worker: riff-daw-glm-20261025, omp / zai/glm-5.3-flash / high.

All share the full problem in the isolated checkout. Researchers should choose useful lines of inquiry, exchange evidence and challenge one another's findings; no narrow specialist roles or product component layout are imposed. Coordinate file ownership before concurrent edits. The conductor synthesizes disagreements and submits the joint proposal to the parent/root.

Use official source links and version/commit evidence, distinguish claims from experiments, retain reproducible commands and receipts, and record authored findings/limits in Baton. Commit implementation progress as it occurs and cross-reference #78; use plain branch publication, never force or push to main. Return validated completed work for root review, merge and deployment.

Existing host rules remain: use owned NVMe temporary/output paths, one heavy process at a time under nice/idle I/O, targeted verification, and the shared compile queue for native engine builds. Preserve active checkouts, explicit stops, shared models and production services. This setup makes no DAW selection or detailed architecture commitment. Reversible feasibility investigations are authorized within the research scope; deployment remains with root review.
