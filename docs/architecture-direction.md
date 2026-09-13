# Application and distribution direction

Riff's long-term application target is **Elixir**. Prefer Rust where native
integration is needed. The existing Python implementation remains operational
during migration; its presence is not a reason to establish new permanent Python
requirements for installation or application services.

Elixir should own the library, generation queue and recovery, model management,
provider integration, private-network access, installation and updates. Preserve
the existing contracts and user data while replacing these boundaries. Keep
native inference behind a measured, versioned interface; moving orchestration to
Elixir does not itself accelerate the model. The shared browser artwork remains
driven by the same audio clock for live playback and exported video.

## Installation experience

The intended flow is **Download Riff → open the installer → Install → Open Riff**.
Installation includes the required models, their sidecars, the native engine,
media tools and the application runtime. People should not have to obtain model
files separately or install Python, developer tools or command-line utilities.

The installer owns model download, resumable transfer, integrity verification and
progress. Reuse verified existing files. Preserve a working app, its library and
its settings until the new installation is ready. Completion means generation is
ready, not merely that the application files were copied. Subsequent updates
belong to the app and activate when current work has finished.

The Elixir installer work starts with this download and installation contract.
Native packaging, verified web releases, clean-host acceptance and the equivalent
NVIDIA distribution remain delivery requirements. Web previews use ad-hoc
signatures; organization signing and notarization are separate improvements.

## Migration acceptance

For each replaced boundary, compare real behavior: saved recordings and recipes,
interrupted-stage recovery, job cancellation, protected provider keys, authorized
network access and model compatibility. Preserve current public APIs where
practical so the studio and agents can continue to use the same operations.
Keep GUI and model execution changes independently verifiable. Temporary
compatibility components should have explicit ownership and a replacement path.
