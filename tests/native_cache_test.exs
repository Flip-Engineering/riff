Code.require_file("../scripts/native_cache.exs", __DIR__)
ExUnit.start()

defmodule Riff.NativeCacheTest do
  use ExUnit.Case, async: false
  alias Riff.NativeCache, as: Cache
  @repo Path.expand("..", __DIR__)

  setup do
    root =
      Path.join(
        System.tmp_dir!(),
        "riff-cache-test-" <> Integer.to_string(System.unique_integer([:positive]))
      )

    File.mkdir_p!(root)

    for name <-
          ~w(setup_engine.py paths.py platform_support.py scripts/native_cache.exs scripts/build_cached_native.exs scripts/compiler_cache_tool.json .github/workflows/ci.yml) do
      File.mkdir_p!(Path.dirname(Path.join(root, name)))
      File.cp!(Path.join(@repo, name), Path.join(root, name))
    end

    File.mkdir_p!(Path.join(root, "patches"))
    File.write!(Path.join(root, "patches/change.patch"), "verified fixture patch\n")

    manifest = %{
      "runtime_repo" => "https://example.invalid/runtime",
      "runtime_commit" => String.duplicate("a", 40),
      "local_patches" => %{
        "patches/change.patch" =>
          Map.take(Cache.file_record(Path.join(root, "patches/change.patch"), "patch"), [
            "bytes",
            "sha256"
          ])
      }
    }

    Cache.write_json(Path.join(root, "sources.json"), manifest)
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root}
  end

  defp configured(root) do
    cmake = System.find_executable("cmake") || raise("CMake is required for native cache tests")
    native = Path.join(root, "native")
    build = Path.join(root, "build")
    File.mkdir_p!(native)

    File.write!(
      Path.join(native, "CMakeLists.txt"),
      "cmake_minimum_required(VERSION 3.17)\nproject(cache_fixture C CXX)\nadd_subdirectory(assembly)\nadd_executable(fixture main.cpp extra.c $<TARGET_OBJECTS:fixture_asm>)\n"
    )

    File.write!(
      Path.join(native, "main.cpp"),
      "extern \"C\" int answer();\nint main() { return answer() == 42 ? 0 : 1; }\n"
    )

    File.write!(Path.join(native, "extra.c"), "int answer(void) { return 42; }\n")
    File.mkdir_p!(Path.join(native, "assembly"))

    File.write!(
      Path.join(native, "assembly/CMakeLists.txt"),
      "enable_language(ASM)\nadd_library(fixture_asm OBJECT empty.s)\n"
    )

    File.write!(Path.join(native, "assembly/empty.s"), ".text\n")
    Cache.query!(build)
    arguments = ["-S", native, "-B", build, "-DCMAKE_BUILD_TYPE=Release"]
    {_, 0} = System.cmd(cmake, arguments, stderr_to_stdout: true)

    %{
      "build_directory" => build,
      "source_directory" => native,
      "cmake" => cmake,
      "configuration" => [cmake | arguments]
    }
  end

  test "complete patch verification rejects missing, added and changed patch bytes", %{root: root} do
    baseline = Cache.source!(root)
    File.write!(Path.join(root, "README.md"), "application-only edit")
    assert Cache.source!(root) == baseline
    path = Path.join(root, "patches/change.patch")
    original = File.read!(path)
    File.write!(path, "changed")
    assert_raise RuntimeError, ~r/verification failed/, fn -> Cache.source!(root) end
    File.write!(path, original)
    File.write!(Path.join(root, "patches/extra.patch"), "extra")
    assert_raise RuntimeError, ~r/patch set differs/, fn -> Cache.source!(root) end
    File.rm!(Path.join(root, "patches/extra.patch"))
    File.rm!(path)
    assert_raise RuntimeError, ~r/patch set differs/, fn -> Cache.source!(root) end
  end

  test "actual configured compilers are bound and launcher configuration preserves identity", %{
    root: root
  } do
    configuration = configured(root)
    baseline = Cache.configured!(root, configuration, "cpu", %{})

    assert Enum.map(baseline.descriptor["namespace"]["toolchains"], & &1["language"]) == [
             "ASM",
             "C",
             "CXX"
           ]

    for tool <- baseline.descriptor["namespace"]["toolchains"] do
      assert tool["executable"]["sha256"] == Cache.hash_file(tool["compiler"]["path"])
    end

    assembler =
      Enum.find(baseline.descriptor["namespace"]["toolchains"], &(&1["language"] == "ASM"))

    refute Map.has_key?(assembler["compiler"], "id")
    refute Map.has_key?(assembler["compiler"], "version")

    [cmake | args] = configuration["configuration"]

    {_, 0} =
      System.cmd(
        cmake,
        args ++ ["-DCMAKE_C_COMPILER_LAUNCHER=", "-DCMAKE_CXX_COMPILER_LAUNCHER="],
        stderr_to_stdout: true
      )

    assert Cache.configured!(root, configuration, "cpu", %{}).sha256 == baseline.sha256

    {_, 0} =
      System.cmd(cmake, ["--build", configuration["build_directory"]], stderr_to_stdout: true)

    {_, 0} = System.cmd(Path.join(configuration["build_directory"], "fixture"), [])
  end

  test "source changes update complete provenance but retain the toolchain namespace", %{
    root: root
  } do
    configuration = configured(root)
    before = Cache.configured!(root, configuration, "cpu", %{})
    path = Path.join(root, "patches/change.patch")
    File.write!(path, "new verified patch")

    manifest =
      Cache.json(Path.join(root, "sources.json"))
      |> put_in(
        ["local_patches", "patches/change.patch"],
        Map.take(Cache.file_record(path, "patch"), ["bytes", "sha256"])
      )

    Cache.write_json(Path.join(root, "sources.json"), manifest)
    after_change = Cache.configured!(root, configuration, "cpu", %{})
    assert before.namespace_sha256 == after_change.namespace_sha256
    refute before.source_sha256 == after_change.source_sha256
    refute before.sha256 == after_change.sha256
  end

  test "separate fresh same-path configurations retain the complete and namespace identities", %{
    root: root
  } do
    first = configured(root)

    first_index =
      Path.wildcard(Path.join(first["build_directory"], ".cmake/api/v1/reply/index-*.json"))

    before = Cache.configured!(root, first, "cpu", %{})
    # Only this fixture's output is removed. No configured tree or index survives.
    File.rm_rf!(first["build_directory"])

    File.write!(
      Path.join(root, "README.md"),
      "An application-only edit between CI invocations.\n"
    )

    second = configured(root)

    second_index =
      Path.wildcard(Path.join(second["build_directory"], ".cmake/api/v1/reply/index-*.json"))

    after_reconfigure = Cache.configured!(root, second, "cpu", %{})
    refute first_index == second_index
    assert before.namespace_sha256 == after_reconfigure.namespace_sha256
    assert before.source_sha256 == after_reconfigure.source_sha256
    assert before.sha256 == after_reconfigure.sha256
  end

  test "flag and backend changes are represented without publishing raw definitions", %{
    root: root
  } do
    configuration = configured(root)
    baseline = Cache.configured!(root, configuration, "cpu", %{})

    changed =
      Cache.configured!(root, configuration, "cpu", %{"CXXFLAGS" => "-DPRIVATE_EXAMPLE=hidden"})

    assert changed.namespace_sha256 == baseline.namespace_sha256
    refute changed.sha256 == baseline.sha256
    refute inspect(changed.descriptor) =~ "hidden"

    nvcc =
      Cache.configured!(root, configuration, "cpu", %{
        "NVCC_PREPEND_FLAGS" => "-DPRIVATE_NVCC_PREPEND=hidden",
        "NVCC_APPEND_FLAGS" => "-DPRIVATE_NVCC_APPEND=hidden"
      })

    assert nvcc.namespace_sha256 == baseline.namespace_sha256
    refute nvcc.sha256 == baseline.sha256

    assert Map.keys(nvcc.descriptor["compiler_environment_sha256"]) |> Enum.sort() == [
             "NVCC_APPEND_FLAGS",
             "NVCC_PREPEND_FLAGS"
           ]

    refute inspect(nvcc.descriptor) =~ "hidden"
    metal = Cache.configured!(root, configuration, "metal", %{})
    refute metal.namespace_sha256 == baseline.namespace_sha256
    [cmake | args] = configuration["configuration"]

    {_, 0} =
      System.cmd(cmake, args ++ ["-DCMAKE_CXX_FLAGS=-DFIXTURE_VARIANT=1"], stderr_to_stdout: true)

    flags = Cache.configured!(root, configuration, "cpu", %{})
    assert flags.namespace_sha256 == baseline.namespace_sha256
    refute flags.sha256 == baseline.sha256
  end

  test "missing configured compiler metadata is a failure, not a guessed identity", %{root: root} do
    configuration = configured(root)
    File.rm_rf!(Path.join(configuration["build_directory"], ".cmake/api/v1/reply"))

    assert_raise RuntimeError, ~r/metadata is missing/, fn ->
      Cache.configured!(root, configuration, "cpu", %{})
    end
  end

  test "missing C compiler version remains a failure with a valid uncached assembler", %{
    root: root
  } do
    configuration = configured(root)

    [path] =
      Path.wildcard(
        Path.join(configuration["build_directory"], ".cmake/api/v1/reply/toolchains-v1-*.json")
      )

    reply = Cache.json(path)

    changed =
      Map.update!(reply, "toolchains", fn items ->
        Enum.map(items, fn entry ->
          if entry["language"] == "C",
            do: Map.update!(entry, "compiler", &Map.delete(&1, "version")),
            else: entry
        end)
      end)

    Cache.write_json(path, changed)

    assert_raise RuntimeError, ~r/Incomplete C compiler identity/, fn ->
      Cache.configured!(root, configuration, "cpu", %{})
    end
  end

  test "CUDA host identity comes from its literal generated description", %{root: root} do
    directory = Path.join(root, "CMakeFiles/3.28.3")
    File.mkdir_p!(directory)
    path = Path.join(directory, "CMakeCUDACompiler.cmake")
    compiler = System.find_executable("c++")
    line = "set(CMAKE_CUDA_HOST_COMPILER \"#{compiler}\")\n"
    File.write!(path, "set(CMAKE_CUDA_COMPILER_ID \"NVIDIA\")\n" <> line)
    record = Cache.cuda_host!(root)
    assert record["sha256"] == Cache.hash_file(compiler)
    assert record["configuration_source"] == "CMakeFiles/3.28.3/CMakeCUDACompiler.cmake"

    for text <- [
          "",
          line <> line,
          line <> "set(CMAKE_CUDA_HOST_COMPILER \"${HOST}\")\n",
          "set(CMAKE_CUDA_HOST_COMPILER \"${HOST}\")\n",
          "set(CMAKE_CUDA_HOST_COMPILER \"g++\")\n"
        ] do
      File.write!(path, text)
      assert_raise RuntimeError, fn -> Cache.cuda_host!(root) end
    end

    File.rm!(path)
    assert_raise RuntimeError, fn -> Cache.cuda_host!(root) end
  end

  test "optional receipt values remain JSON null and distinct from the string nil", %{root: root} do
    path = Path.join(root, "null.json")
    Cache.write_json(path, %{"tool" => nil, "nested" => [nil]})
    assert Cache.json(path) == %{"tool" => :null, "nested" => [:null]}
    refute Cache.identity(%{"value" => nil}) == Cache.identity(%{"value" => "nil"})
  end

  test "only pushes to the owned main branch can write the remote cache" do
    main = %{
      "GITHUB_EVENT_NAME" => "push",
      "GITHUB_REF" => "refs/heads/main",
      "GITHUB_REPOSITORY" => "Flip-Engineering/riff"
    }

    assert Cache.cache_mode(main) == "READ_WRITE"

    for changes <- [
          %{"GITHUB_EVENT_NAME" => "pull_request"},
          %{"GITHUB_REF" => "refs/tags/v0.6.4"},
          %{"GITHUB_REPOSITORY" => "other/riff"},
          %{"GITHUB_EVENT_NAME" => "workflow_dispatch"}
        ] do
      assert Cache.cache_mode(Map.merge(main, changes)) == "READ_ONLY"
    end

    assert Cache.cache_mode(%{}) == "READ_ONLY"
  end

  test "cache process state and backend are isolated from inherited cache configuration" do
    inherited = %{
      "SCCACHE_SERVER_PORT" => "4226",
      "SCCACHE_BUCKET" => "developer-cache",
      "SCCACHE_LOG" => "debug",
      "UNRELATED" => "kept"
    }

    environment =
      Cache.cache_environment(
        "/tmp/owned-riff-cache",
        "verified-namespace",
        "READ_ONLY",
        inherited
      )
      |> Map.new()

    assert environment["SCCACHE_SERVER_PORT"] == nil
    assert environment["SCCACHE_BUCKET"] == nil
    assert environment["SCCACHE_LOG"] == nil
    refute Map.has_key?(environment, "UNRELATED")
    assert environment["SCCACHE_C_CUSTOM_CACHE_BUSTER"] == "verified-namespace"
    assert environment["SCCACHE_GHA_RW_MODE"] == "READ_ONLY"

    for name <- ~w(SCCACHE_CONF SCCACHE_CACHED_CONF SCCACHE_SERVER_UDS SCCACHE_DIR) do
      assert String.starts_with?(environment[name], "/tmp/owned-riff-cache/")
    end
  end

  test "a corrupt pinned tool archive is rejected before extraction or execution", %{root: root} do
    output = Path.join(root, "tool")
    File.mkdir!(output)

    item =
      Cache.json(Path.join(root, "scripts/compiler_cache_tool.json"))["platforms"]["macos-arm64"]

    File.write!(Path.join(output, item["sha256"] <> ".tar.gz"), "not a compiler cache")

    assert_raise RuntimeError, ~r/archive verification failed/, fn ->
      Cache.install_tool!(root, output, "macos-arm64")
    end

    refute File.exists?(Path.join(output, "sccache"))
  end

  defp setup_driver_fixture(root, program) do
    native = Path.join(root, "fixture-source")
    File.mkdir!(native)

    File.write!(
      Path.join(native, "CMakeLists.txt"),
      "cmake_minimum_required(VERSION 3.17)\nproject(cache_driver_fixture C CXX)\nset(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)\nadd_executable(audiocpp_cli main.cpp)\n"
    )

    File.write!(Path.join(native, "main.cpp"), program)
    {_, 0} = System.cmd("git", ["init", "--quiet", native], stderr_to_stdout: true)
    {_, 0} = System.cmd("git", ["-C", native, "add", "."], stderr_to_stdout: true)

    {_, 0} =
      System.cmd(
        "git",
        [
          "-C",
          native,
          "-c",
          "user.name=Flip Engineering",
          "-c",
          "user.email=contributors@riff.invalid",
          "commit",
          "--quiet",
          "-m",
          "Native compiler fixture"
        ],
        stderr_to_stdout: true
      )

    {commit, 0} = System.cmd("git", ["-C", native, "rev-parse", "HEAD"])
    File.rm!(Path.join(root, "patches/change.patch"))

    Cache.write_json(Path.join(root, "sources.json"), %{
      "runtime_repo" => native,
      "runtime_commit" => String.trim(commit),
      "local_patches" => %{}
    })
  end

  test "unavailable remote cache runs the actual driver, fresh compile and link", %{root: root} do
    setup_driver_fixture(root, "int main() { return 0; }\n")

    environment = [
      {"RIFF_HOME", root},
      {"ACTIONS_RESULTS_URL", nil},
      {"ACTIONS_RUNTIME_TOKEN", nil}
    ]

    {text, result} =
      System.cmd(
        "elixir",
        [
          Path.join(root, "scripts/build_cached_native.exs"),
          "--backend",
          "cpu",
          "--compile-only",
          "--jobs",
          "2"
        ],
        env: environment,
        stderr_to_stdout: true
      )

    assert result == 0, text
    receipt = Cache.json(Path.join(root, "native-cache-receipts/build.json"))
    refute receipt["cache_enabled"]
    assert receipt["build_exit_status"] == 0
    [binary] = Path.wildcard(Path.join(root, "engines/*/build/bin/audiocpp_cli"))
    assert {"", 0} == System.cmd(binary, [])

    {again, status} =
      System.cmd(
        "elixir",
        [
          Path.join(root, "scripts/build_cached_native.exs"),
          "--backend",
          "cpu",
          "--compile-only"
        ],
        env: environment,
        stderr_to_stdout: true
      )

    refute status == 0
    assert again =~ "fresh native build directory"
  end

  test "runtime credentials without a service flag never contact the legacy cache", %{root: root} do
    setup_driver_fixture(root, "int main() { return 0; }\n")

    {text, result} =
      System.cmd(
        "elixir",
        [
          Path.join(root, "scripts/build_cached_native.exs"),
          "--backend",
          "cpu",
          "--compile-only",
          "--jobs",
          "2"
        ],
        env: [
          {"RIFF_HOME", root},
          {"ACTIONS_RESULTS_URL", "https://cache.invalid/"},
          {"ACTIONS_RUNTIME_TOKEN", "fixture-token"},
          {"ACTIONS_CACHE_SERVICE_V2", nil}
        ],
        stderr_to_stdout: true
      )

    assert result == 0, text
    receipt = Cache.json(Path.join(root, "native-cache-receipts/build.json"))
    refute receipt["cache_enabled"]
    assert receipt["cache_backend"] == "disabled"
    assert receipt["cache_service_version"] == :null
    refute File.exists?(Path.join(root, ".compiler-cache-tool"))
    [binary] = Path.wildcard(Path.join(root, "engines/*/build/bin/audiocpp_cli"))
    assert {"", 0} == System.cmd(binary, [])
  end

  test "GitHub v2 storage requires the complete runner context" do
    environment = %{
      "ACTIONS_RESULTS_URL" => "https://cache.invalid/",
      "ACTIONS_RUNTIME_TOKEN" => "fixture-token",
      "ACTIONS_CACHE_SERVICE_V2" => "True"
    }

    assert Cache.github_cache_available?(environment)

    for name <- Map.keys(environment),
        do: refute(Cache.github_cache_available?(Map.delete(environment, name)))

    refute Cache.github_cache_available?(
             Map.put(environment, "ACTIONS_CACHE_SERVICE_V2", "false")
           )

    refute Cache.github_cache_available?(
             Map.put(environment, "GITHUB_SERVER_URL", "https://enterprise.invalid")
           )
  end

  test "actual failed-write statistics stay visible without publishing storage details" do
    # Captured from pinned sccache 0.17.0 against a local fixture which returned
    # cache misses for reads and rejected writes. Read-error counters stayed zero.
    raw = Cache.json(Path.join(@repo, "tests/fixtures/native-cache-write-failure.json"))
    result = Cache.cache_statistics(raw)
    assert result.backend == "github-actions"
    assert result.counters["cache_write_errors"] == 1
    assert result.counters["cache_writes"] == 0
    assert result.counters["cache_read_errors"] == 0
    assert result.counters["cache_errors"]["counts"] == %{}

    private = Map.put(raw, "cache_location", "local: /fixture/private/cache")
    safe = Cache.cache_statistics(private)
    assert safe.backend == "unknown"
    refute inspect(safe) =~ "/fixture/private"
    refute Map.has_key?(safe.counters, "cache_location")
  end

  test "a native compile failure stays failed when the remote cache is absent", %{root: root} do
    setup_driver_fixture(root, "#error intentional_compile_failure\n")

    {text, status} =
      System.cmd(
        "elixir",
        [
          Path.join(root, "scripts/build_cached_native.exs"),
          "--backend",
          "cpu",
          "--compile-only",
          "--jobs",
          "2"
        ],
        env: [{"RIFF_HOME", root}, {"ACTIONS_RESULTS_URL", nil}, {"ACTIONS_RUNTIME_TOKEN", nil}],
        stderr_to_stdout: true
      )

    refute status == 0
    assert text =~ "intentional_compile_failure"
    receipt = Cache.json(Path.join(root, "native-cache-receipts/build.json"))
    assert receipt["build_exit_status"] != 0
    refute receipt["cache_enabled"]
    assert Path.wildcard(Path.join(root, "engines/*/build/bin/audiocpp_cli")) == []
  end
end
