defmodule Riff.NativeCache do
  @moduledoc "Compiler-object cache inputs; cached build trees and model files are never restored."
  @schema "riff.native-cache.v1"
  @drivers ~w(setup_engine.py paths.py platform_support.py)
  @policy ~w(scripts/native_cache.exs scripts/build_cached_native.exs scripts/compiler_cache_tool.json .github/workflows/ci.yml)
  @flags ~w(CC CXX CUDACXX CUDAHOSTCXX CFLAGS CXXFLAGS CPPFLAGS CUDAFLAGS NVCC_PREPEND_FLAGS NVCC_APPEND_FLAGS LDFLAGS SDKROOT MACOSX_DEPLOYMENT_TARGET CMAKE_GENERATOR CMAKE_TOOLCHAIN_FILE CMAKE_OSX_ARCHITECTURES)

  def json(path), do: path |> File.read!() |> :json.decode()
  def write_json(path, data), do: File.write!(path, [:json.encode(data), "\n"])
  def digest(data), do: :crypto.hash(:sha256, data) |> Base.encode16(case: :lower)

  def hash_file(path),
    do:
      path
      |> File.stream!(1_048_576)
      |> Enum.reduce(:crypto.hash_init(:sha256), &:crypto.hash_update(&2, &1))
      |> :crypto.hash_final()
      |> Base.encode16(case: :lower)

  def identity(value), do: value |> canonical() |> :json.encode() |> digest()

  def normalize(value, roots) when is_binary(value),
    do: Enum.reduce(roots, value, fn {path, label}, text -> String.replace(text, path, label) end)

  def normalize(value, roots) when is_map(value),
    do: Map.new(value, fn {k, v} -> {k, normalize(v, roots)} end)

  def normalize(value, roots) when is_list(value), do: Enum.map(value, &normalize(&1, roots))
  def normalize(value, _), do: value

  defp canonical(value) when is_map(value),
    do: ["map", value |> Enum.sort() |> Enum.map(fn {k, v} -> [k, canonical(v)] end)]

  defp canonical(value) when is_list(value), do: ["list", Enum.map(value, &canonical/1)]
  defp canonical(value), do: value

  def file_record(path, name) do
    unless File.regular?(path), do: raise("Missing native input: #{name}")
    %{"path" => name, "bytes" => File.stat!(path).size, "sha256" => hash_file(path)}
  end

  def source!(app) do
    manifest = json(Path.join(app, "sources.json"))
    declared = manifest |> Map.fetch!("local_patches") |> Map.keys() |> Enum.sort()

    actual =
      Path.wildcard(Path.join(app, "patches/*.patch"))
      |> Enum.map(&Path.relative_to(&1, app))
      |> Enum.sort()

    unless declared == actual, do: raise("Native patch set differs from sources.json")

    patches =
      Enum.map(actual, fn name ->
        record = file_record(Path.join(app, name), name)

        unless Map.take(record, ["bytes", "sha256"]) == manifest["local_patches"][name],
          do: raise("Native patch verification failed: #{name}")

        record
      end)

    descriptor = %{
      "runtime_repo" => manifest["runtime_repo"],
      "runtime_commit" => manifest["runtime_commit"],
      "patches" => patches,
      "drivers" => Enum.map(@drivers, &file_record(Path.join(app, &1), &1))
    }

    legacy =
      digest([manifest["runtime_commit"] | Enum.map(actual, &File.read!(Path.join(app, &1)))])
      |> binary_part(0, 16)

    %{descriptor: descriptor, sha256: identity(descriptor), engine_fingerprint: legacy}
  end

  def query!(build) do
    query = Path.join(build, ".cmake/api/v1/query/client-riff-cache")
    File.mkdir_p!(query)
    for name <- ["toolchains-v1", "cache-v2"], do: File.write!(Path.join(query, name), "")
  end

  defp reply!(reply_root, index, kind, version) do
    record = get_in(index, ["reply", "client-riff-cache", "#{kind}-v#{version}"])

    unless is_map(record) and record["kind"] == kind and
             get_in(record, ["version", "major"]) == version,
           do: raise("CMake did not provide configured #{kind} metadata")

    name = record["jsonFile"]
    unless is_binary(name) and Path.basename(name) == name, do: raise("Invalid CMake reply path")
    result = json(Path.join(reply_root, name))

    unless result["kind"] == kind and get_in(result, ["version", "major"]) == version,
      do: raise("CMake reply identity mismatch")

    result
  end

  def configured!(app, configuration, backend, environment \\ System.get_env()) do
    build = Map.fetch!(configuration, "build_directory")
    root = Path.join(build, ".cmake/api/v1/reply")

    index_path =
      root |> Path.join("index-*.json") |> Path.wildcard() |> Enum.sort() |> List.last()

    unless index_path, do: raise("CMake configuration metadata is missing")
    index = json(index_path)
    toolchains = reply!(root, index, "toolchains", 1)["toolchains"]
    languages = Enum.map(toolchains, & &1["language"])
    required = if backend == "cuda", do: ["C", "CXX", "CUDA"], else: ["C", "CXX"]

    unless Enum.all?(required, &(&1 in languages)),
      do: raise("CMake did not configure every required native compiler")

    tools =
      toolchains
      |> Enum.map(fn entry ->
        compiler = Map.fetch!(entry, "compiler")

        unless Enum.all?(~w(path id version), &(is_binary(compiler[&1]) and compiler[&1] != "")),
          do: raise("Incomplete compiler identity")

        entry =
          Map.put(
            entry,
            "executable",
            file_record(compiler["path"], Path.basename(compiler["path"]))
          )

        if :os.type() == {:unix, :darwin} and compiler["id"] == "AppleClang" and
             compiler["path"] in ~w(/usr/bin/cc /usr/bin/c++ /usr/bin/clang /usr/bin/clang++) do
          case System.cmd("xcrun", ["--find", Path.basename(compiler["path"])],
                 stderr_to_stdout: true
               ) do
            {resolved, 0} ->
              Map.put(
                entry,
                "selected_xcode_compiler",
                file_record(String.trim(resolved), "selected-" <> entry["language"])
              )

            _ ->
              raise("Cannot resolve the selected Xcode compiler")
          end
        else
          entry
        end
      end)
      |> Enum.sort_by(& &1["language"])

    entries = reply!(root, index, "cache", 2)["entries"] |> Map.new(&{&1["name"], &1["value"]})

    roots = [
      {configuration["source_directory"], "$NATIVE_SOURCE"},
      {build, "$NATIVE_BUILD"},
      {app, "$RIFF_SOURCE"}
    ]

    flags =
      entries
      |> Enum.filter(fn {name, _} ->
        String.starts_with?(name, ["CMAKE_", "ENGINE_", "GGML_", "AUDIOCPP_"])
      end)
      |> Enum.reject(fn {name, _} ->
        String.contains?(name, [
          "_BINARY_DIR",
          "_SOURCE_DIR",
          "_CACHEFILE_DIR",
          "_HOME_DIRECTORY",
          "COMPILER_LAUNCHER"
        ])
      end)
      |> Map.new(fn {name, value} -> {name, digest(normalize(value, roots))} end)

    # Values may contain private compiler definitions. Hash the allowlist rather
    # than publishing the process environment or arbitrary CMake cache values.
    env =
      @flags
      |> Enum.filter(&Map.has_key?(environment, &1))
      |> Map.new(&{&1, digest(environment[&1])})

    policy = Enum.map(@policy, &file_record(Path.join(app, &1), &1))

    platform = %{
      "os" => :os.type() |> Tuple.to_list() |> Enum.map(&Atom.to_string/1),
      "architecture" => :erlang.system_info(:system_architecture) |> to_string(),
      "cuda_image" => environment["RIFF_CUDA_IMAGE"]
    }

    sdk =
      if :os.type() == {:unix, :darwin}, do: macos_sdk!(entries["CMAKE_OSX_SYSROOT"]), else: %{}

    host =
      if backend == "cuda" do
        path = entries["CMAKE_CUDA_HOST_COMPILER"]

        unless is_binary(path) and path != "",
          do: raise("CUDA host compiler must be selected explicitly for compiler caching")

        file_record(path, path)
      end

    namespace =
      %{
        "schema" => @schema,
        "backend" => backend,
        "platform" => platform,
        "toolchains" => tools,
        "cmake" => index["cmake"],
        "cmake_executable" => file_record(get_in(index, ["cmake", "paths", "cmake"]), "cmake"),
        "cmake_driver" => file_record(configuration["cmake"], "cmake-driver"),
        "cuda_host_compiler" => host,
        "sdk" => sdk,
        "policy" => policy
      }
      |> normalize(roots)

    source = source!(app)

    descriptor = %{
      "schema" => @schema,
      "native_source" => source.descriptor,
      "namespace" => namespace,
      "configuration_arguments_sha256" =>
        identity(normalize(configuration["configuration"], roots)),
      "configuration_values_sha256" => flags,
      "compiler_environment_sha256" => env
    }

    %{
      descriptor: descriptor,
      sha256: identity(descriptor),
      namespace_sha256: identity(namespace),
      source_sha256: source.sha256,
      entries: entries
    }
  end

  defp macos_sdk!(configured) do
    sdk = if is_binary(configured) and configured != "", do: ["--sdk", configured], else: []

    info =
      for {name, command, args} <- [
            {"xcode", "xcodebuild", ["-version"]},
            {"sdk", "xcrun", sdk ++ ["--show-sdk-version"]},
            {"sdk_build", "xcrun", sdk ++ ["--show-sdk-build-version"]},
            {"sdk_path", "xcrun", sdk ++ ["--show-sdk-path"]}
          ],
          into: %{} do
        case System.cmd(command, args, stderr_to_stdout: true) do
          {text, 0} -> {name, String.trim(text)}
          _ -> raise("Cannot identify selected macOS toolchain")
        end
      end

    Map.put(info, "configured_sysroot", configured)
    |> Map.put(
      "sdk_settings",
      file_record(Path.join(info["sdk_path"], "SDKSettings.json"), "SDKSettings.json")
    )
  end

  def cache_environment(owned, namespace, mode, inherited \\ System.get_env()) do
    # Isolate both daemon state and backend selection from a developer's cache.
    cleared =
      inherited
      |> Map.keys()
      |> Enum.filter(&String.starts_with?(&1, "SCCACHE_"))
      |> Map.new(&{&1, nil})

    settings = %{
      "SCCACHE_GHA_ENABLED" => "on",
      "SCCACHE_GHA_RW_MODE" => mode,
      "SCCACHE_SERVER_UDS" => Path.join(owned, "server.sock"),
      "SCCACHE_IDLE_TIMEOUT" => "600",
      "SCCACHE_CONF" => Path.join(owned, "absent.toml"),
      "SCCACHE_CACHED_CONF" => Path.join(owned, "cached-config"),
      "SCCACHE_DIR" => Path.join(owned, "objects"),
      "SCCACHE_CACHE_SIZE" => "128M",
      "SCCACHE_DIRECT" => "false",
      "SCCACHE_IGNORE_SERVER_IO_ERROR" => "1",
      "SCCACHE_C_CUSTOM_CACHE_BUSTER" => namespace
    }

    Map.merge(cleared, settings) |> Map.to_list()
  end

  def cache_mode(environment) do
    if environment["GITHUB_EVENT_NAME"] == "push" and
         environment["GITHUB_REF"] == "refs/heads/main" and
         environment["GITHUB_REPOSITORY"] == "Flip-Engineering/riff",
       do: "READ_WRITE",
       else: "READ_ONLY"
  end

  def platform do
    case {:os.type(), :erlang.system_info(:system_architecture) |> to_string()} do
      {{:unix, :darwin}, architecture} ->
        if String.starts_with?(architecture, "aarch64"), do: "macos-arm64", else: nil

      {{:unix, :linux}, architecture} ->
        if String.starts_with?(architecture, "x86_64"), do: "linux-x86_64", else: nil

      _ ->
        nil
    end
  end

  def install_tool!(app, output, platform \\ platform()) do
    manifest = json(Path.join(app, "scripts/compiler_cache_tool.json"))
    item = Map.fetch!(manifest["platforms"], platform)
    File.mkdir_p!(output)

    unless File.lstat!(output).type == :directory,
      do: raise("Compiler cache tool directory must be an owned directory")

    archive = Path.join(output, item["sha256"] <> ".tar.gz")

    unless File.exists?(archive) do
      unless System.find_executable("curl"), do: raise("Compiler cache tool download unavailable")
      temporary = archive <> ".download-" <> Integer.to_string(System.unique_integer([:positive]))

      try do
        {_, status} =
          System.cmd(
            "curl",
            [
              "--fail",
              "--location",
              "--silent",
              "--show-error",
              "--proto",
              "=https",
              "--proto-redir",
              "=https",
              "--connect-timeout",
              "15",
              "--max-time",
              "120",
              "--max-filesize",
              to_string(item["bytes"]),
              "--output",
              temporary,
              item["url"]
            ],
            stderr_to_stdout: true
          )

        unless status == 0, do: raise("Compiler cache tool download unavailable")
        verify!(temporary, item)
        File.rename!(temporary, archive)
      after
        File.rm(temporary)
      end
    end

    verify!(archive, item)

    {:ok, [{_name, binary}]} =
      :erl_tar.extract(String.to_charlist(archive), [
        :compressed,
        :memory,
        {:files, [String.to_charlist(item["member"])]}
      ])

    unless byte_size(binary) in 1..64_000_000,
      do: raise("Compiler cache executable has an unexpected size")

    target = Path.join(output, "sccache")

    case File.lstat(target) do
      {:ok, %{type: :regular}} -> :ok
      {:error, :enoent} -> :ok
      _ -> raise("Compiler cache executable must be an owned regular file")
    end

    if File.exists?(target) and File.read!(target) != binary,
      do: raise("Compiler cache executable verification failed")

    unless File.exists?(target), do: File.write!(target, binary, [:exclusive])
    File.chmod!(target, 0o700)

    case System.cmd(target, ["--version"], stderr_to_stdout: true) do
      {text, 0} ->
        unless String.trim(text) == "sccache " <> manifest["version"],
          do: raise("Compiler cache tool version mismatch")

      _ ->
        raise("Compiler cache tool cannot run")
    end

    %{
      path: target,
      receipt:
        Map.merge(item, %{
          "version" => manifest["version"],
          "binary_sha256" => digest(binary),
          "binary_bytes" => byte_size(binary)
        })
    }
  end

  defp verify!(path, item) do
    unless File.lstat!(path).type == :regular and File.stat!(path).size == item["bytes"] and
             hash_file(path) == item["sha256"],
           do: raise("Compiler cache archive verification failed")
  end
end
