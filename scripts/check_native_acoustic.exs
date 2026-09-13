Code.require_file("native_capabilities.exs", __DIR__)
Code.require_file("native_source_proof.exs", __DIR__)

defmodule Riff.Native.AcousticCheck do
  @moduledoc "CPU format and stage-boundary checks against the fully built production engine."
  @app Path.expand("..", __DIR__)

  defp json(path), do: path |> File.read!() |> :json.decode()
  defp write_json(path, value), do: File.write!(path, [:json.encode(value), "\n"])

  defp hash(path) do
    File.stream!(path, 1_048_576)
    |> Enum.reduce(:crypto.hash_init(:sha256), &:crypto.hash_update(&2, &1))
    |> :crypto.hash_final()
    |> Base.encode16(case: :lower)
  end

  defp verify!(path, pin) do
    unless File.regular?(path) and File.stat!(path).size == pin["bytes"] and
             hash(path) == pin["sha256"],
           do: raise("Native input differs from sources.json: #{path}")

    path
  end

  defp pins(paths),
    do: Map.new(Enum.uniq(paths), &{&1, %{bytes: File.stat!(&1).size, sha256: hash(&1)}})

  defp run!(root, label, command, arguments, options \\ []) do
    {text, status} = System.cmd(command, arguments, Keyword.put(options, :stderr_to_stdout, true))
    File.write!(Path.join(root, label <> ".log"), text)

    write_json(Path.join(root, label <> ".command.json"), %{
      command: command,
      arguments: arguments,
      status: status
    })

    unless status == 0, do: raise("#{label} failed; see #{root}/#{label}.log")
    IO.puts("PASS #{label}")
    text
  end

  defp cache!(build, key) do
    [_, value] =
      Regex.run(
        Regex.compile!("^#{key}:[^=]+=(.*)$", "m"),
        File.read!(Path.join(build, "CMakeCache.txt"))
      )

    value
  end

  defp flags!(build) do
    text = File.read!(Path.join(build, "CMakeFiles/engine_model_yue2.dir/flags.make"))

    Enum.flat_map(~w(CXX_DEFINES CXX_INCLUDES CXX_FLAGS), fn key ->
      [_, value] = Regex.run(Regex.compile!("^#{key} = (.*)$", "m"), text)
      OptionParser.split(value)
    end)
  end

  def main do
    {options, [], []} =
      OptionParser.parse(System.argv(),
        strict: [build: :string, output: :string, source_repository: :string, tokenizer: :string]
      )

    workspace = System.get_env("RIFF_WORKSPACE") || @app

    build =
      (options[:build] ||
         json(Path.join(workspace, "data/engine.json"))["binary"]
         |> Path.dirname()
         |> Path.dirname())
      |> Path.expand()

    root =
      (options[:output] ||
         Path.join(build, "acoustic-check-#{System.unique_integer([:positive])}"))
      |> Path.expand()

    if File.exists?(root), do: raise("Use a fresh check output directory")
    File.mkdir_p!(root)
    manifest = json(Path.join(@app, "sources.json"))

    tokenizer =
      options
      |> Keyword.fetch!(:tokenizer)
      |> Path.expand()
      |> verify!(manifest["model"]["files"]["sidecars/yue2-qwen.tiktoken"])

    source = cache!(build, "CMAKE_HOME_DIRECTORY")
    repository = Path.expand(options[:source_repository] || source)
    environment = [{"GIT_INDEX_FILE", Path.join(root, "source.index")}]

    git = fn label, args ->
      run!(root, label, "git", ["-C", repository | args], env: environment)
    end

    unless String.trim(git.("source-revision", ["rev-parse", "HEAD"])) ==
             manifest["runtime_commit"],
           do: raise("Native source revision differs from sources.json")

    git.("source-index", ["read-tree", manifest["runtime_commit"]])
    patches = Path.wildcard(Path.join(@app, "patches/*.patch")) |> Enum.sort()

    unless Enum.map(patches, &Path.relative_to(&1, @app)) ==
             Enum.sort(Map.keys(manifest["local_patches"])),
           do: raise("Native patch set differs from sources.json")

    for patch <- patches do
      verify!(patch, manifest["local_patches"][Path.relative_to(patch, @app)])
      git.("apply-" <> Path.basename(patch), ["apply", "--cached", patch])
    end

    Riff.Native.SourceProof.verify_tree!(repository, environment, source)
    binary = Path.join(build, "bin/audiocpp_cli")
    capabilities = Riff.Native.Capabilities.verify!(binary, manifest["native_capabilities"], root)

    [linker | link_args] =
      Path.join(build, "CMakeFiles/audiocpp_cli.dir/link.txt")
      |> File.read!()
      |> String.trim()
      |> OptionParser.split()

    original_objects =
      link_args
      |> Enum.filter(&String.ends_with?(&1, [".o", ".a"]))
      |> Enum.map(&Path.expand(&1, build))

    tests = Path.join(@app, "tests/native_acoustic")
    test_sources = Path.wildcard(Path.join(tests, "*")) |> Enum.filter(&File.regular?/1)

    controls =
      Enum.map(
        ~w(scripts/check_native_acoustic.exs scripts/native_source_proof.exs scripts/native_capabilities.exs sources.json),
        &Path.join(@app, &1)
      )

    watched =
      original_objects ++
        test_sources ++
        controls ++
        patches ++
        [
          binary,
          tokenizer,
          Path.join(build, "CMakeCache.txt"),
          Path.join(build, "CMakeFiles/engine_model_yue2.dir/flags.make"),
          Path.join(build, "CMakeFiles/audiocpp_cli.dir/link.txt")
        ]

    before = pins(watched)
    write_json(Path.join(root, "input-pins.json"), before)
    compiler = cache!(build, "CMAKE_CXX_COMPILER")
    flags = flags!(build)

    compile = fn name, input, extra ->
      object = Path.join(root, name <> ".o")

      run!(
        root,
        "compile-" <> name,
        compiler,
        ["-I#{tests}"] ++ extra ++ flags ++ ["-c", input, "-o", object],
        cd: build
      )

      object
    end

    implementation = Path.join(source, "src/models/yue2/acoustic_checkpoint.cpp")
    native = compile.("checkpoint", implementation, [])

    portable =
      compile.("checkpoint-portable", implementation, ["-DRIFF_CHECKPOINT_PORTABLE_SHA=1"])

    format = compile.("format-test", Path.join(tests, "format_test.cpp"), [])

    format_results =
      for {name, object} <- [{"format-native", native}, {"format-portable", portable}] do
        executable = Path.join(root, name)
        run!(root, "link-" <> name, compiler, [object, format, "-o", executable])
        text = run!(root, name, executable, [Path.join(root, name <> "-fixtures")])
        result = text |> String.split("\n", trim: true) |> List.last() |> :json.decode()

        unless result == %{
                 "passed" => 73,
                 "failed" => 0,
                 "model_weights_loaded" => false,
                 "backend_initialized" => false
               },
               do: raise("Incomplete format checks")

        {name, result}
      end

    for name <- ~w(hashes.jsonl roundtrip.yac) do
      unless File.read!(Path.join(root, "format-native-fixtures/#{name}")) ==
               File.read!(Path.join(root, "format-portable-fixtures/#{name}")),
             do: raise("Native and portable SHA or payload bytes differ")
    end

    for line <- File.stream!(Path.join(root, "format-native-fixtures/hashes.jsonl")) do
      sample = :json.decode(line)

      unless hash(Path.join(root, "format-native-fixtures/hash-#{sample["bytes"]}.bin")) ==
               sample["sha256"],
             do: raise("Independent OTP SHA check differs")
    end

    boundary = compile.("backend-doubles", Path.join(tests, "backend_doubles.cpp"), [])
    regression = compile.("pipeline-test", Path.join(tests, "pipeline_test.cpp"), [])
    {_output, link_args} = List.pop_at(link_args, Enum.find_index(link_args, &(&1 == "-o")) + 1)
    libraries = Enum.reject(link_args, &(&1 == "-o" or String.ends_with?(&1, ".o")))
    executable = Path.join(root, "pipeline-test")

    run!(
      root,
      "link-pipeline",
      linker,
      [native, boundary, regression] ++ libraries ++ ["-o", executable],
      cd: build
    )

    text = run!(root, "pipeline", executable, [tokenizer, Path.join(root, "pipeline-fixtures")])
    result = text |> String.split("\n", trim: true) |> List.last() |> :json.decode()

    unless result["passed"] == 41 and result["failed"] == 0 and
             result["backend_initialized"] == false and result["model_weights_loaded"] == false,
           do: raise("Incomplete stage-boundary checks")

    symbols = run!(root, "production-symbols", "nm", [binary])

    if String.contains?(symbols, ["e06_test", "u07_test", "OriginalYue2PipelineRuntime"]),
      do: raise("The production CLI contains test operator doubles")

    Riff.Native.SourceProof.verify_tree!(repository, environment, source)
    unless pins(watched) == before, do: raise("A production/test input changed during checking")

    write_json(Path.join(root, "receipt.json"), %{
      format: Map.new(format_results),
      pipeline: result,
      capabilities: capabilities,
      production_binary_sha256: hash(binary),
      test_binary_sha256: hash(executable),
      tokenizer_sha256: hash(tokenizer),
      native_runtime_commit: manifest["runtime_commit"],
      independent_sha_crosscheck: true,
      source_and_production_inputs_unchanged: true,
      backend_initialized: false,
      neural_model_weights_loaded: false,
      note:
        "Actual parser, model-file selection, stage ownership and checkpoint implementation with substituted neural operators; no GPU/model execution."
    })

    IO.puts("Native acoustic checks passed: #{root}")
  end
end

Riff.Native.AcousticCheck.main()
