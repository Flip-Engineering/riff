# Native compiler cache

CI uses a checksum-pinned Rust sccache executable to reuse C/C++ and CUDA object
compilation. The Elixir driver still invokes the normal native setup path: pinned
upstream checkout, every verified local patch, fresh CMake configuration, compile,
link, and the existing backend-specific validation. Neither a build directory nor
a finished engine executable is restored from a cache. Desktop packaging remains
separate and keeps its existing independent build and provenance checks.

`native_cache.exs` obtains compiler identities from CMake File API replies after
configuration. It records the selected compiler executables, CMake executable,
platform, and selected macOS SDK/Xcode or pinned CUDA image and explicit host
compiler. The full input receipt also binds every patch and setup driver and
hashes relevant configuration/environment values. It does not export the process
environment, cache credentials, or unredacted compiler definitions.

The cache namespace is the backend/toolchain/cache-policy identity. Native source
edits change the complete provenance receipt; individual preprocessed input and
compiler command hashes decide which objects can be reused. An application-only
edit can therefore retain native objects. No path normalization is enabled in
sccache itself; source-path changes can reduce hits without changing build
semantics. The namespace uses `SCCACHE_C_CUSTOM_CACHE_BUSTER`.

Only a push to `Flip-Engineering/riff`'s `main` branch writes the GitHub cache.
Pull requests, tags, and other invocations use `READ_ONLY`; the workflows never
use `pull_request_target`. GitHub's branch cache scopes remain an additional
boundary. The Node action forwards the runtime-selected v2 service flag as well
as the cache URL and token. Without that complete context, the driver compiles uncached
instead of sending legacy requests to the v2 service. It also confirms that the
started cache actually selected GitHub storage.

Each invocation owns a separate Unix socket and cache configuration;
it does not use or stop an existing developer cache daemon.

Missing GitHub cache access, an unavailable tool download, or a cache service
startup failure leaves the normal compiler path active. Cache communication
failure can fall back to local compilation. A bad downloaded checksum, incomplete
compiler identity, changed build inputs, or a compiler failure remains a failure.
The driver refuses an existing CMake build directory so hosted acceptance always
starts with fresh configuration and link outputs.

The uploaded `native-cache-*` receipts report actual cache status, object hit/miss
statistics, native source and toolchain identities, and elapsed build time.
They distinguish the selected backend and service version, cache writes, and
write errors. Compilation success and read-miss counts alone do not prove remote
persistence; a later run must demonstrate actual hits.
`build_milliseconds` includes the normal second configure, compile and link but
excludes initial source fetch/configuration, tool download and post-build identity
verification. Compare the same metric and backend when measuring cold and warm
runs. CUDA validation in this workflow is compilation only, not GPU execution.

Local tests exercise actual CMake/compiler selection, patch integrity, source and
flag changes, read-only policy, corrupt archive rejection, uncached fallback,
fresh output refusal, and a real compiler failure. Hosted cache service reuse and
complete cold/warm engine timing need their own run receipts; a tiny object-cache
fixture is not an engine speed measurement.

References:

- [sccache 0.17.0 configuration and cache separation](https://github.com/mozilla/sccache/blob/v0.17.0/README.md)
- [GitHub cache backend](https://github.com/mozilla/sccache/blob/v0.17.0/docs/GHA.md)
- [GitHub cache scope](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [CMake File API](https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html)

- [Pinned GitHub cache protocol selection](https://github.com/apache/opendal/blob/v0.55.0/core/src/services/ghac/core.rs#L397-L425)
