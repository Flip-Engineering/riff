Riff 0.6.1 fixes macOS desktop packaging and makes update availability clearer.

- The installer builds its cryptography module against the bundled OpenSSL library and verifies the library actually loaded by the packaged application.
- Each package starts with a fresh Erlang runtime. Its native files are checked against the selected toolchain, with exact identities retained through assembly and relocation. Rust dependency notices are preserved across supported toolchain layouts.
- Studio shows “An update is being prepared” while a newer desktop download is being built. Checking for updates preserves active music and writing sessions; invalid release metadata still produces an error.

The desktop preview includes the music engine, media tools and local writer runtime. Riff Setup downloads and verifies the music and writer models, installs a persistent Riff application and supports app-managed updates. The studio retains its reflective, audio-driven artwork, 4K/60 fps video export, detailed YuE2 controls and resource-aware overlap between writing and music generation.

The graphical macOS download is an ad-hoc signed preview without Developer ID signing or notarization. macOS may block its first launch under default security settings. It is distributed through the web; signing and NVIDIA desktop packaging remain follow-up work. The source archive is a separate developer download. Experimental native cache reuse is still under evaluation and is not included in this release.

Validation covers 130 application tests, 52 Elixir installer tests, 13 desktop packaging tests and 11 launcher recovery tests, plus scheduling, resource estimation and browser checks. The relocated packaged runtime passes actual cryptographic operations, HTTPS downloads, certificate and hostname rejection, native dependency inspection and source identity checks. Source installation passes real HTTP startup, failed-update fallback and library preservation. Managed 0.6.0 installation, concurrent local writing/music and actual 4K/60 fps export provide the existing desktop integration baseline; 0.6.1 desktop build receipts are supplied with the preview assets.
