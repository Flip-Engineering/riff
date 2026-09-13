defmodule Riff.Installer.Payload do
  @moduledoc "Verify and stage the application and its bundled runtimes without touching the live workspace."
  alias Riff.Installer.{Download, Lock, Manifest}

  def load(nil), do: nil

  def load(directory) do
    manifest = directory |> Path.join("manifest.json") |> File.read!() |> Jason.decode!()

    unless is_map(manifest) and manifest["format_version"] == 1 and
             matches?(manifest["version"], ~r/\A\d+(?:\.\d+)*\z/) and
             matches?(manifest["runtime_id"], ~r/\A[a-f0-9]{24}\z/) do
      error!("The application package has an invalid version or runtime identity.")
    end

    files = manifest["files"]
    unless is_list(files) and files != [], do: error!("The application package is empty.")

    paths =
      Enum.map(files, fn item ->
        unless is_map(item),
          do: error!("An application file has invalid verification information.")

        path = Manifest.validate_path!(item["path"])

        unless String.starts_with?(path, ["app/", "runtime/"]) and is_integer(item["bytes"]) and
                 item["bytes"] >= 0 and
                 is_boolean(item["executable"]) and
                 matches?(item["sha256"], ~r/\A[a-f0-9]{64}\z/) do
          error!("An application file has invalid verification information.")
        end

        path
      end)

    if MapSet.size(MapSet.new(paths)) != length(paths),
      do: error!("The application package lists a file more than once.")

    entries = manifest["entries"]

    for key <- ["python", "studio", "launcher", "engine", "ffmpeg", "ffprobe", "control"] do
      unless is_map(entries) and entries[key] in paths,
        do: error!("The application package is missing a required entry point.")

      prefix = if key in ["studio", "launcher"], do: "app/", else: "runtime/"

      unless String.starts_with?(entries[key], prefix),
        do: error!("An application entry point is outside its runtime.")
    end

    runtime_digest =
      files
      |> Enum.filter(&String.starts_with?(&1["path"], "runtime/"))
      |> :json.encode()
      |> IO.iodata_to_binary()
      |> then(&:crypto.hash(:sha256, &1))
      |> Base.encode16(case: :lower)

    unless manifest["runtime_sha256"] == runtime_digest and
             manifest["runtime_id"] == String.slice(runtime_digest, 0, 24),
           do: error!("The application runtime identity does not match its files.")

    environment = manifest["environment"] || %{}

    allowed = %{
      "SSL_CERT_FILE" => "/etc/ssl/cert.pem",
      "PYTHONNOUSERSITE" => "1",
      "PYTHONDONTWRITEBYTECODE" => "1"
    }

    unless is_map(environment) and
             Enum.all?(environment, fn {key, value} -> allowed[key] == value end),
           do: error!("The application runtime has unsupported environment settings.")

    %{
      directory: Path.expand(directory),
      manifest: manifest,
      bytes: Enum.reduce(files, 0, &(&1["bytes"] + &2))
    }
  end

  def model_manifest(payload, kind \\ "model") do
    item = Enum.find(payload.manifest["files"], &(&1["path"] == "app/sources.json"))
    unless item, do: error!("The application package is missing its model sources.")
    source = checked_source!(payload.directory, item["path"])

    unless verified?(source, item),
      do: error!("The application's model sources did not pass verification.")

    manifest = source |> File.read!() |> Jason.decode!() |> Map.fetch!(kind)
    Manifest.assets(manifest)
    manifest
  end

  def model_directory(root, manifest) do
    models = Path.join([root, "workspace", "models"])
    versioned_models(models, manifest)
  end

  def writer_set(nil, _root, _options), do: nil

  def writer_set(payload, root, options) do
    if get_in(payload.manifest, ["features", "local_writer"]) == true do
      manifest = model_manifest(payload, "writer")
      assets = Manifest.assets(manifest, options)

      %{
        manifest: manifest,
        assets: assets,
        directory:
          versioned_models(Path.join([root, "workspace", "models", "lyric-writer"]), manifest),
        bytes: Enum.reduce(assets, 0, &(&1.bytes + &2))
      }
    end
  end

  def prepare_writer(nil, _root, _progress, _options), do: :ok

  def prepare_writer(writer, root, progress, options) do
    Download.safe_target!(root, Path.relative_to(Path.join(writer.directory, ".model-set"), root))

    Enum.reduce(writer.assets, 0, fn asset, completed ->
      Download.ensure(
        asset,
        writer.directory,
        fn phase, bytes ->
          # A writer consists of weights plus vocabulary/configuration sidecars.
          # Keep this one intelligible progress item in the graphical installer.
          progress.(if(phase == :verified, do: :checking, else: phase), completed + bytes)
        end,
        options
      )

      completed + asset.bytes
    end)

    Download.write_manifest!(writer.directory, writer.manifest)
    progress.(:verified, writer.bytes)
  end

  defp versioned_models(models, manifest) do
    case File.read(Path.join(models, "manifest.json")) do
      {:error, :enoent} ->
        models

      {:ok, contents} ->
        if Jason.decode(contents) == {:ok, manifest},
          do: models,
          else: Path.join([models, "sets", manifest["revision"]])

      _ ->
        Path.join([models, "sets", manifest["revision"]])
    end
  end

  def verify_models!(directory, manifest, progress) do
    for asset <- Manifest.assets(manifest) do
      path = checked_source!(directory, asset.id)
      progress.(:checking, asset.id, 0, asset.bytes)

      unless verified?(path, %{"bytes" => asset.bytes, "sha256" => asset.sha256}),
        do: error!("The prepared music models have changed. Prepare the update again.")

      progress.(:verified, asset.id, asset.bytes, asset.bytes)
    end
  end

  def stage(payload, root, model_directory, progress, options \\ []) do
    validate_platform!(payload.manifest["platform"], options)
    root = Path.expand(root)
    version = payload.manifest["version"]
    runtime_id = payload.manifest["runtime_id"]
    app = Path.join([root, "releases", version])
    runtime = Path.join([root, "runtimes", runtime_id])

    stage =
      Download.safe_target!(root, ".staging/" <> version <> "-" <> runtime_id <> "/.receipt")
      |> Path.dirname()

    installed = %{
      app: app,
      runtime: runtime,
      root: root,
      version: version,
      runtime_id: runtime_id,
      manifest: payload.manifest,
      writer: writer_set(payload, root, options)
    }

    Enum.reduce(payload.manifest["files"], 0, fn item, completed ->
      source = checked_source!(payload.directory, item["path"])
      final = installed_path(installed, item["path"])
      Download.safe_target!(root, Path.relative_to(final, root))
      target = Download.safe_target!(stage, item["path"])

      if File.exists?(final) do
        unless verified?(final, item),
          do:
            error!(
              "An installed version contains different files. Use a newer Riff installer; the existing files have been kept."
            )
      else
        copy_verified!(source, target, item, fn bytes ->
          progress.(:installing, completed + bytes)
        end)
      end

      total = completed + item["bytes"]
      progress.(:installing, total)
      total
    end)

    # Both trees are complete before either is selected by the stable launcher.
    publish_tree(stage, "runtime", runtime)
    publish_tree(stage, "app", app)
    write_json(Path.join(runtime, ".payload-manifest.json"), payload.manifest)

    write_json(Path.join(app, ".desktop-payload.json"), %{
      version: version,
      runtime_id: runtime_id,
      provenance: payload.manifest["provenance"]
    })

    write_json(Path.join(app, ".runtime.json"), runtime_config(installed))

    write_json(Path.join(app, ".engine.json"), %{
      backend: if(payload.manifest["platform"] == "macos-arm64", do: "metal", else: "cpu"),
      binary: entry(installed, "engine"),
      model_root: Path.expand(model_directory),
      model_file: "yue2-3b-q4_0.gguf",
      vae_file: "yue2-vae-f16.gguf"
    })

    check!(installed, model_directory, options)
    progress.(:verified, payload.bytes)
    installed
  end

  @doc "Copy only manifest-listed, verified package files into the outer installer bundle."
  def copy_package!(payload, destination) do
    unless not File.exists?(destination),
      do: error!("The packaged payload destination already exists.")

    for item <- payload.manifest["files"] do
      source = checked_source!(payload.directory, item["path"])
      target = Download.safe_target!(destination, item["path"])
      copy_verified!(source, target, item, fn _ -> :ok end)
    end

    write_json(Path.join(destination, "manifest.json"), payload.manifest)
  end

  def verified_source!(root, item) do
    source = checked_source!(root, Manifest.validate_path!(item["path"]))

    unless verified?(source, item),
      do: error!("A packaged component does not match its build receipt.")

    source
  end

  def entry(installed, key),
    do: installed_path(installed, Map.fetch!(installed.manifest["entries"], key))

  def runtime_config(installed) do
    prefix = "runtime/"
    entries = installed.manifest["entries"]

    value = %{
      format_version: 1,
      runtime_id: installed.runtime_id,
      python: String.replace_prefix(entries["python"], prefix, ""),
      media: entries["ffmpeg"] |> String.replace_prefix(prefix, "") |> Path.dirname(),
      control: String.replace_prefix(entries["control"], prefix, ""),
      lock_helper: control_helper(installed.runtime),
      environment:
        Map.take(installed.manifest["environment"] || %{}, [
          "SSL_CERT_FILE",
          "PYTHONNOUSERSITE",
          "PYTHONDONTWRITEBYTECODE"
        ])
    }

    if installed.writer,
      do:
        Map.put(
          value,
          :writer_model,
          Path.relative_to(installed.writer.directory, installed.root)
        ),
      else: value
  end

  def environment(installed) do
    media = Path.dirname(entry(installed, "ffmpeg"))
    python = Path.dirname(entry(installed, "python"))

    fixed = [
      {"RIFF_HOME", Path.join(installed.root, "workspace")},
      {"RIFF_INSTALL_ROOT", installed.root},
      {"RIFF_RUNTIME_CONTROL", entry(installed, "control")},
      {"PATH", Enum.join([media, python, "/usr/bin", "/bin", "/usr/sbin", "/sbin"], ":")}
    ]

    fixed =
      if installed.writer,
        do:
          fixed ++
            [
              {"RIFF_WRITER_PYTHON", entry(installed, "python")},
              {"RIFF_WRITER_MODEL", installed.writer.directory}
            ],
        else: fixed

    Map.merge(installed.manifest["environment"] || %{}, Map.new(fixed))
    |> Map.merge(%{"PYTHONHOME" => nil, "PYTHONPATH" => nil})
    |> Map.to_list()
  end

  def write_json(path, value) do
    File.mkdir_p!(Path.dirname(path))

    temporary =
      path <> "." <> Base.url_encode64(:crypto.strong_rand_bytes(8), padding: false) <> ".tmp"

    File.write!(temporary, Jason.encode_to_iodata!(value, pretty: true), [:exclusive, :sync])
    File.chmod!(temporary, 0o600)
    File.rename!(temporary, path)
    Lock.sync_directory!(Path.dirname(path))
  end

  defp control_helper(runtime) do
    case Path.wildcard(
           Path.join(runtime, "control/lib/riff_installer-*/priv/native/riff-file-lock")
         ) do
      [path] -> Path.relative_to(path, runtime)
      _ -> error!("The application package is missing its control runtime.")
    end
  end

  defp check!(installed, _models, options) do
    if Keyword.get(options, :preflight, true) do
      run!(
        entry(installed, "python"),
        [entry(installed, "studio"), "--check"],
        environment(installed)
      )

      run!(entry(installed, "engine"), ["--list-devices"], environment(installed))
      run!(entry(installed, "ffmpeg"), ["-version"], environment(installed))
      run!(entry(installed, "ffprobe"), ["-version"], environment(installed))

      if installed.writer do
        run!(
          entry(installed, "python"),
          ["-s", "-B", "-c", "import mlx.core, mlx_lm, tokenizers, transformers"],
          environment(installed)
        )
      end

      run!(
        entry(installed, "control"),
        [
          "eval",
          "true = Code.ensure_loaded?(Riff.Runtime.Admission); IO.puts(Base.encode16(:crypto.hash(:sha256, \"riff\")))"
        ],
        environment(installed)
      )
    end
  end

  defp run!(executable, args, environment) do
    case System.cmd(executable, args, env: environment, stderr_to_stdout: true) do
      {_, 0} ->
        :ok

      _ ->
        error!(
          "A bundled application component could not start. Your current installation has been kept."
        )
    end
  end

  defp installed_path(installed, "app/" <> rest), do: Path.join(installed.app, rest)
  defp installed_path(installed, "runtime/" <> rest), do: Path.join(installed.runtime, rest)

  defp publish_tree(stage, name, target) do
    staged = Path.join(stage, name)

    if File.dir?(staged) do
      File.mkdir_p!(Path.dirname(target))

      if File.exists?(target) do
        # An existing tree was verified file by file; only missing files from the
        # same pinned payload may be added (for interrupted prior preparation).
        merge_verified_tree(staged, target)
      else
        File.rename!(staged, target)
      end
    end
  end

  defp merge_verified_tree(source, target) do
    File.mkdir_p!(target)

    for name <- File.ls!(source) do
      from = Path.join(source, name)
      to = Path.join(target, name)
      if File.dir?(from), do: merge_verified_tree(from, to), else: File.rename!(from, to)
    end
  end

  defp copy_verified!(source, target, item, progress) do
    if verified?(target, item) do
      progress.(item["bytes"])
    else
      partial = target <> ".part"

      for path <- [target, partial] do
        case File.lstat(path) do
          {:ok, %{type: :regular}} ->
            File.rename!(
              path,
              path <>
                ".unverified." <> Base.url_encode64(:crypto.strong_rand_bytes(8), padding: false)
            )

          {:error, :enoent} ->
            :ok

          _ ->
            error!("An application destination is not a regular file.")
        end
      end

      {:ok, input} = File.open(source, [:read, :binary, :raw])
      {:ok, output} = File.open(partial, [:write, :exclusive, :binary, :raw])

      {size, hash} =
        try do
          copy_chunks(input, output, 0, :crypto.hash_init(:sha256), progress)
        after
          File.close(input)
          File.close(output)
        end

      unless size == item["bytes"] and digest(hash) == item["sha256"],
        do: error!("An application file did not pass verification. Download the installer again.")

      File.chmod!(partial, if(item["executable"], do: 0o755, else: 0o644))
      File.rename!(partial, target)
    end
  end

  defp copy_chunks(input, output, size, hash, progress) do
    case :file.read(input, Application.get_env(:riff_installer, :hash_chunk_bytes, 1_048_576)) do
      {:ok, bytes} ->
        :ok = :file.write(output, bytes)
        progress.(size + byte_size(bytes))

        copy_chunks(
          input,
          output,
          size + byte_size(bytes),
          :crypto.hash_update(hash, bytes),
          progress
        )

      :eof ->
        :ok = :file.sync(output)
        {size, hash}

      {:error, _} ->
        error!("An application file could not be read.")
    end
  end

  defp verified?(path, item) do
    expected_size = item["bytes"]

    case File.lstat(path) do
      {:ok, %{type: :regular, size: size}} when size == expected_size ->
        hash =
          File.stream!(path, 1_048_576)
          |> Enum.reduce(:crypto.hash_init(:sha256), &:crypto.hash_update(&2, &1))

        digest(hash) == item["sha256"]

      _ ->
        false
    end
  end

  defp checked_source!(root, path) do
    result =
      Enum.reduce(Path.split(path), root, fn segment, parent ->
        next = Path.join(parent, segment)

        case File.lstat(next) do
          {:ok, %{type: type}} when type in [:directory, :regular] -> next
          _ -> error!("The application package contains a missing or linked file.")
        end
      end)

    unless File.regular?(result),
      do: error!("The application package contains a folder where a file is expected.")

    result
  end

  defp validate_platform!(platform, options) do
    architecture = :erlang.system_info(:system_architecture) |> List.to_string()

    actual =
      if :os.type() == {:unix, :darwin} and String.starts_with?(architecture, "aarch64"),
        do: "macos-arm64",
        else: "unsupported"

    unless platform == actual or Keyword.get(options, :allow_test_platform, false),
      do: error!("This installer does not match this computer's platform.")
  end

  defp digest(state), do: state |> :crypto.hash_final() |> Base.encode16(case: :lower)
  defp matches?(value, pattern), do: is_binary(value) and Regex.match?(pattern, value)
  defp error!(message), do: raise(Download.Error, reason: :payload, message: message)
end
