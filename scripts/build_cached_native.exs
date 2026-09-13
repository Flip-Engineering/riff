Code.require_file("native_cache.exs", __DIR__)

defmodule Riff.CachedNativeBuild do
  alias Riff.NativeCache, as: Cache

  @launchers ~w(CMAKE_C_COMPILER_LAUNCHER CMAKE_CXX_COMPILER_LAUNCHER CMAKE_CUDA_COMPILER_LAUNCHER)

  def run(argv) do
    {options, rest, invalid} =
      OptionParser.parse(argv,
        strict: [
          backend: :string,
          jobs: :integer,
          cuda_arch: :string,
          compile_only: :boolean,
          build_only: :boolean
        ]
      )

    unless rest == [] and invalid == [] and options[:backend] in ~w(cpu metal cuda),
      do: raise("Choose a native backend and normal setup options")

    unless options[:compile_only] || options[:build_only],
      do: raise("Cached CI builds require --build-only or --compile-only")

    app = Path.expand("..", __DIR__)
    workspace = Path.expand(System.get_env("RIFF_HOME", app))
    source = Cache.source!(app)

    build =
      Path.join([
        workspace,
        "engines",
        source.engine_fingerprint <> "-" <> options[:backend],
        "build"
      ])

    if File.exists?(Path.join(build, "CMakeCache.txt")),
      do: raise("Compiler-cache validation requires a fresh native build directory")

    Cache.query!(build)

    python =
      System.find_executable("python3") || raise("Python is needed by the native setup driver")

    args = [Path.join(app, "setup_engine.py") | argv]
    environment = @launchers |> Enum.map(&{&1, nil})
    # Configure first without a launcher. File API records the compiler and SDK
    # CMake actually selected; no compiler cache daemon runs during discovery.
    {text, status} =
      System.cmd(python, args ++ ["--configure-only"], env: environment, stderr_to_stdout: true)

    IO.write(text)
    if status != 0, do: System.halt(status)
    configuration = text |> String.trim() |> String.split("\n") |> List.last() |> :json.decode()

    unless configuration["backend"] == options[:backend],
      do: raise("Native setup and cache backends differ")

    expected = File.stat!(build)
    actual = File.stat!(configuration["build_directory"])

    unless {expected.inode, expected.major_device, expected.minor_device} ==
             {actual.inode, actual.major_device, actual.minor_device},
           do: raise("Native setup and cache build paths differ")

    identity = Cache.configured!(app, configuration, options[:backend])
    output = Path.join(workspace, "native-cache-receipts")
    File.mkdir_p!(output)
    Cache.write_json(Path.join(output, "inputs.json"), identity.descriptor)

    owned =
      Path.join(
        System.tmp_dir!(),
        "riff-cache-" <> Integer.to_string(System.unique_integer([:positive]))
      )

    File.mkdir!(owned)
    cache = start_cache(app, workspace, owned, identity)
    started = System.monotonic_time(:millisecond)

    result =
      try do
        if cache.enabled, do: launchers!(configuration, cache.tool.path, cache.environment)
        build_environment = environment ++ cache.environment

        {_, result} =
          System.cmd(python, args,
            env: build_environment,
            into: IO.stream(:stdio, :line),
            stderr_to_stdout: true
          )

        elapsed = System.monotonic_time(:millisecond) - started
        # A failed compile remains a failed build. Never retry compilation under a
        # different cache mode and accidentally turn a source failure green.
        final = Cache.configured!(app, configuration, options[:backend])

        unless final.sha256 == identity.sha256,
          do: raise("Native source or compiler configuration changed during the build")

        stats = if cache.enabled, do: statistics(cache), else: %{}

        receipt = %{
          "schema" => "riff.native-cache-build.v1",
          "backend" => options[:backend],
          "native_fingerprint" => source.engine_fingerprint,
          "source_sha256" => source.sha256,
          "inputs_sha256" => identity.sha256,
          "namespace_sha256" => identity.namespace_sha256,
          "cache_enabled" => cache.enabled,
          "cache_mode" => cache.mode,
          "cache_status" => cache.reason,
          "tool" => if(cache.enabled, do: cache.tool.receipt, else: nil),
          "statistics" => stats,
          "build_milliseconds" => elapsed,
          "build_exit_status" => result
        }

        Cache.write_json(Path.join(output, "build.json"), receipt)
        result
      after
        if cache.enabled,
          do:
            System.cmd(cache.tool.path, ["--stop-server"],
              env: cache.environment,
              stderr_to_stdout: true
            )

        File.rm_rf!(owned)
      end

    if result != 0, do: System.halt(result)
  end

  defp launchers!(configuration, value, environment) do
    [cmake | args] = configuration["configuration"]

    {_, status} =
      System.cmd(cmake, args ++ Enum.map(@launchers, &("-D" <> &1 <> "=" <> value)),
        env: environment,
        into: IO.stream(:stdio, :line),
        stderr_to_stdout: true
      )

    unless status == 0, do: raise("Cannot configure verified compiler launchers")
  end

  defp start_cache(app, workspace, owned, identity) do
    mode = Cache.cache_mode(System.get_env())

    if System.get_env("ACTIONS_RESULTS_URL") in [nil, ""] or
         System.get_env("ACTIONS_RUNTIME_TOKEN") in [nil, ""] do
      %{
        enabled: false,
        mode: "DISABLED",
        reason: "GitHub compiler cache is unavailable; compiling normally",
        environment: []
      }
    else
      tool =
        try do
          Cache.install_tool!(app, Path.join(workspace, ".compiler-cache-tool"))
        rescue
          e in RuntimeError ->
            if e.message == "Compiler cache tool download unavailable",
              do: nil,
              else: reraise(e, __STACKTRACE__)
        end

      if tool == nil do
        %{
          enabled: false,
          mode: "DISABLED",
          reason: "Compiler cache download is unavailable; compiling normally",
          environment: []
        }
      else
        environment = Cache.cache_environment(owned, identity.namespace_sha256, mode)

        {_, status} =
          System.cmd(tool.path, ["--start-server"], env: environment, stderr_to_stdout: true)

        if status == 0 do
          %{
            enabled: true,
            mode: mode,
            reason: "Verified compiler-object cache",
            environment: environment,
            tool: tool
          }
        else
          System.cmd(tool.path, ["--stop-server"], env: environment, stderr_to_stdout: true)

          %{
            enabled: false,
            mode: "DISABLED",
            reason: "Compiler cache service is unavailable; compiling normally",
            environment: []
          }
        end
      end
    end
  end

  defp statistics(cache) do
    case System.cmd(cache.tool.path, ["--show-stats", "--stats-format", "json"],
           env: cache.environment,
           stderr_to_stdout: true
         ) do
      {text, 0} ->
        # The statistics object excludes cache endpoints, credentials and paths.
        text
        |> :json.decode()
        |> Map.get("stats", %{})
        |> Map.take(
          ~w(compile_requests requests_executed cache_hits cache_misses cache_errors cache_timeouts non_cacheable_compilations compilation_failures)
        )

      _ ->
        %{"unavailable" => true}
    end
  end
end

Riff.CachedNativeBuild.run(System.argv())
