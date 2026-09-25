Code.require_file("native_capabilities.exs", __DIR__)
Code.require_file("native_source_proof.exs", __DIR__)

defmodule Riff.Native.ScoreReplayCheck do
  @moduledoc "CPU-only parser and prefix regression against the original production pipeline."
  @app Path.expand("..", __DIR__)
  @patch "patches/yue2-score-replay.patch"

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
           do: raise("Native check input differs from its pin: #{path}")

    path
  end

  defp run!(root, name, command, arguments, options \\ []) do
    {output, status} =
      System.cmd(command, arguments, Keyword.put(options, :stderr_to_stdout, true))

    File.write!(Path.join(root, name <> ".log"), output)

    write_json(Path.join(root, name <> ".command.json"), %{
      command: command,
      arguments: arguments,
      status: status
    })

    unless status == 0, do: raise("#{name} failed (#{status}); see #{root}/#{name}.log")
    IO.puts("PASS #{name}")
    output
  end

  defp cache!(build, key) do
    pattern = Regex.compile!("^" <> key <> ":[^=]+=(.*)$", "m")
    [_, value] = Regex.run(pattern, File.read!(Path.join(build, "CMakeCache.txt")))
    value
  end

  defp flags!(build) do
    path = Path.join(build, "CMakeFiles/engine_model_yue2.dir/flags.make")
    unless File.regular?(path), do: raise("Use the native Unix Makefiles build for this check")
    text = File.read!(path)

    Enum.flat_map(["CXX_DEFINES", "CXX_INCLUDES", "CXX_FLAGS"], fn key ->
      [_, value] = Regex.run(Regex.compile!("^#{key} = (.*)$", "m"), text)
      OptionParser.split(value)
    end)
  end

  defp tokenizer!(options, root, manifest) do
    relative = "sidecars/yue2-qwen.tiktoken"
    pin = manifest["model"]["files"][relative]
    path = Keyword.get(options, :tokenizer)

    cond do
      path ->
        verify!(Path.expand(path), pin)

      options[:download_tokenizer] ->
        path = Path.join(root, "yue2-qwen.tiktoken")

        url =
          "https://huggingface.co/#{manifest["model"]["repo"]}/resolve/#{manifest["model"]["revision"]}/#{relative}"

        run!(root, "fetch-tokenizer", "curl", [
          "--fail",
          "--location",
          "--silent",
          "--show-error",
          "--proto",
          "=https",
          "--proto-redir",
          "=https",
          "--retry",
          "2",
          "--connect-timeout",
          "20",
          "--max-time",
          "120",
          "--output",
          path,
          url
        ])

        verify!(path, pin)

      true ->
        raise("Provide --tokenizer PATH or --download-tokenizer; no model weights are needed")
    end
  end

  defp counterexample!(path, tokenizer) do
    fixture = json(path)

    unless hash(tokenizer) == fixture["tokenizer_sha256"],
      do: raise("Counterexample tokenizer differs")

    requested = MapSet.new(fixture["original_tokens"] ++ fixture["reencoded_tokens"])

    pieces =
      File.stream!(tokenizer)
      |> Enum.reduce(%{}, fn line, acc ->
        [encoded, rank] = line |> String.trim() |> String.split(" ", parts: 2)
        id = String.to_integer(rank)
        if MapSet.member?(requested, id), do: Map.put(acc, id, Base.decode64!(encoded)), else: acc
      end)

    for key <- ["original_tokens", "reencoded_tokens"] do
      text = fixture[key] |> Enum.map(&Map.fetch!(pieces, &1)) |> IO.iodata_to_binary()

      unless text == fixture["visible_abc"],
        do: raise("Counterexample does not decode to the claimed text")
    end
  end

  def main do
    {options, [], []} =
      OptionParser.parse(System.argv(),
        strict: [
          build: :string,
          output: :string,
          source_repository: :string,
          tokenizer: :string,
          download_tokenizer: :boolean
        ]
      )

    workspace = System.get_env("RIFF_WORKSPACE") || @app

    configured_build = fn ->
      Path.join(workspace, "data/engine.json")
      |> json()
      |> Map.fetch!("binary")
      |> Path.dirname()
      |> Path.dirname()
    end

    build = (options[:build] || configured_build.()) |> Path.expand()

    root =
      (options[:output] ||
         Path.join(build, "score-replay-check-#{System.unique_integer([:positive])}"))
      |> Path.expand()

    if File.exists?(root), do: raise("Use a fresh check output directory")
    File.mkdir_p!(root)
    File.mkdir!(Path.join(root, "fixtures"))
    source = cache!(build, "CMAKE_HOME_DIRECTORY")
    repository = Path.expand(options[:source_repository] || source)
    manifest = json(Path.join(@app, "sources.json"))
    index_env = [{"GIT_INDEX_FILE", Path.join(root, "source.index")}]
    git = fn name, args -> run!(root, name, "git", ["-C", repository | args], env: index_env) end

    unless String.trim(git.("source-revision", ["rev-parse", "HEAD"])) ==
             manifest["runtime_commit"],
           do: raise("Native repository revision differs from sources.json")

    git.("source-index", ["read-tree", manifest["runtime_commit"]])
    patches = Path.wildcard(Path.join(@app, "patches/*.patch")) |> Enum.sort()

    unless Enum.map(patches, &Path.relative_to(&1, @app)) ==
             Map.keys(manifest["local_patches"]) |> Enum.sort(),
           do: raise("Native patch set differs from sources.json")

    downstream = Enum.drop_while(patches, &(&1 != Path.join(@app, @patch)))

    # The acoustic checkpoint patch changes runtime ownership, not symbolic
    # sampling/prefix construction. Keep the pre-score-replay production oracle
    # and compare it with the complete current pipeline. New downstream patches
    # still require this explicit review. The warm-engine patch adds an opt-in
    # yue2.keep_resident session option and the CLI job loop; with the option
    # unset (as in this oracle comparison) the pipeline releases AR/NAR exactly
    # as before.
    unless downstream ==
             Enum.map(
               [@patch, "patches/yue2-vae-checkpoint.patch", "patches/yue2-warm-engine.patch"],
               &Path.join(@app, &1)
             ),
           do: raise("Review the original-pipeline oracle after changing downstream patches")

    for patch <- patches do
      verify!(patch, manifest["local_patches"][Path.relative_to(patch, @app)])

      if patch == Path.join(@app, @patch) do
        for {relative, target} <- [
              {"src/models/yue2/pipeline.cpp", "original_pipeline.cpp"},
              {"include/engine/models/yue2/pipeline.h", "original_pipeline.h"}
            ] do
          body = git.("read-" <> target, ["show", ":" <> relative])

          File.write!(
            Path.join(root, target),
            String.replace(body, "Yue2PipelineRuntime", "OriginalYue2PipelineRuntime")
          )
        end
      end

      git.("apply-" <> Path.basename(patch), ["apply", "--cached", patch])
    end

    Riff.Native.SourceProof.verify_tree!(repository, index_env, source)
    tokenizer = tokenizer!(options, root, manifest)
    counterexample = Path.join(@app, "tests/native_score_replay/counterexample.json")
    counterexample!(counterexample, tokenizer)
    binary = Path.join(build, "bin/audiocpp_cli")
    binary_before = hash(binary)
    capabilities = Riff.Native.Capabilities.verify!(binary, manifest["native_capabilities"], root)
    compiler = cache!(build, "CMAKE_CXX_COMPILER")
    flags = flags!(build)
    tests = Path.join(@app, "tests/native_score_replay")

    units = [
      {"original_pipeline", Path.join(root, "original_pipeline.cpp"),
       ["-DYue2PipelineRuntime=OriginalYue2PipelineRuntime"]},
      {"pipeline", Path.join(source, "src/models/yue2/pipeline.cpp"), []},
      {"request", Path.join(source, "src/models/yue2/request.cpp"), []},
      {"types", Path.join(source, "src/models/yue2/types.cpp"), []},
      {"tokenizer_text", Path.join(source, "src/models/yue2/tokenizer_text.cpp"), []},
      {"backend_doubles", Path.join(tests, "backend_doubles.cpp"), []},
      {"score_replay", Path.join(tests, "score_replay.cpp"), []}
    ]

    objects =
      for {name, file, extra} <- units do
        target = Path.join(root, name <> ".o")

        run!(
          root,
          "compile-" <> name,
          compiler,
          ["-I#{root}", "-I#{tests}"] ++ extra ++ flags ++ ["-c", file, "-o", target],
          cd: build
        )

        target
      end

    # Retain the actual native linker's platform flags and library order. Only
    # CLI objects and its output path are replaced; test doubles never enter it.
    [linker | link_args] =
      Path.join(build, "CMakeFiles/audiocpp_cli.dir/link.txt")
      |> File.read!()
      |> String.trim()
      |> OptionParser.split()

    {_output, link_args} = List.pop_at(link_args, Enum.find_index(link_args, &(&1 == "-o")) + 1)
    link_args = Enum.reject(link_args, &(&1 == "-o" or String.ends_with?(&1, ".o")))
    executable = Path.join(root, "score-replay-test")
    run!(root, "link-regression", linker, objects ++ link_args ++ ["-o", executable], cd: build)

    output =
      run!(root, "cpu-regression", executable, [
        tokenizer,
        counterexample,
        Path.join(root, "fixtures"),
        Path.join(root, "prefix-equality.jsonl")
      ])

    result = output |> String.split("\n", trim: true) |> List.last() |> :json.decode()

    unless result["passed"] == 92 and result["failed"] == 0 and
             result["native_pipeline_pairs"] == 16 and
             result["backend_initialized"] == false and result["model_weights_loaded"] == false,
           do: raise("Native score regression did not finish all expected checks")

    symbols = run!(root, "production-symbols", "nm", [binary])

    if String.contains?(symbols, ["u07_test", "OriginalYue2PipelineRuntime"]),
      do: raise("Production binary contains test-double symbols")

    Riff.Native.SourceProof.verify_tree!(repository, index_env, source)
    unless hash(binary) == binary_before, do: raise("Production binary changed during testing")

    write_json(Path.join(root, "receipt.json"), %{
      tests: result,
      capabilities: capabilities,
      runtime_commit: manifest["runtime_commit"],
      production_binary_sha256: binary_before,
      test_binary_sha256: hash(executable),
      tokenizer_sha256: hash(tokenizer),
      counterexample_sha256: hash(counterexample),
      original_source: "verified index before score-replay patch",
      source_and_production_binary_unchanged: true,
      model_weights_loaded: false,
      backend_initialized: false
    })

    IO.puts("Native score replay checks passed: #{root}")
  end
end

Riff.Native.ScoreReplayCheck.main()
