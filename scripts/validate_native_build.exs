# Re-read actual source and File API/toolchain bytes after queue publication.
# A successful queue exit alone does not prove this is the requested engine.
[app, backend, configuration] = System.argv()
Code.require_file(Path.join(app, "scripts/native_cache.exs"))
alias Riff.NativeCache, as: Cache

receipt = Cache.json(Path.join(app, "native-cache-receipts/build.json"))
inputs = Cache.json(Path.join(app, "native-cache-receipts/inputs.json"))
identity = Cache.configured!(app, :json.decode(configuration), backend)
sha_backend = if receipt["sha256_backend"] == :null, do: nil, else: receipt["sha256_backend"]

unless receipt["schema"] == "riff.native-cache-build.v1" and
         receipt["backend"] == backend and receipt["build_exit_status"] == 0 and
         receipt["inputs_sha256"] == identity.sha256 and
         receipt["namespace_sha256"] == identity.namespace_sha256 and
         receipt["source_sha256"] == identity.source_sha256 and
         Cache.identity(inputs) == identity.sha256 and
         sha_backend == identity.entries["RIFF_CHECKPOINT_SHA_BACKEND"],
       do: raise("Queued native source/toolchain receipts differ from the prepared engine")

for {name, expected} <- [
      {"ENGINE_ENABLE_CUDA", if(backend == "cuda", do: "ON", else: "OFF")},
      {"ENGINE_ENABLE_METAL", if(backend == "metal", do: "ON", else: "OFF")}
    ] do
  unless identity.entries[name] == expected, do: raise("Queued native backend differs")
end
