defmodule RiffInstaller.MixProject do
  use Mix.Project

  def project do
    [
      app: :riff_installer,
      version: "0.1.0",
      elixir: "~> 1.16",
      start_permanent: Mix.env() == :prod,
      deps: deps(),
      compilers: [:riff_lock] ++ Mix.compilers(),
      elixirc_paths: elixirc_paths(Mix.env()),
      releases: [riff_installer: [include_executables_for: [:unix], include_erts: true]],
      preferred_cli_env: [check: :test],
      aliases: [check: ["format --check-formatted", "test"]]
    ]
  end

  def application do
    [extra_applications: [:logger, :crypto], mod: {Riff.Installer.Application, []}]
  end

  defp elixirc_paths(:test), do: ["lib", Path.expand("../runtime/lib", __DIR__), "test/support"]
  defp elixirc_paths(_), do: ["lib", Path.expand("../runtime/lib", __DIR__)]

  defp deps do
    [
      {:bandit, "~> 1.12"},
      {:finch, "~> 0.21"},
      {:jason, "~> 1.4"}
    ]
  end
end

defmodule Mix.Tasks.Compile.RiffLock do
  use Mix.Task.Compiler

  def run(_arguments) do
    built =
      for {source, target} <- [
            {"native/file_lock.rs", "priv/native/riff-file-lock"},
            {"native/studio_launcher.rs", "priv/desktop/Riff.app/Contents/MacOS/Riff"}
          ],
          do: build(Path.expand(source, __DIR__), Path.expand(target, __DIR__))

    plist = """
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>CFBundleName</key><string>Riff</string>
      <key>CFBundleDisplayName</key><string>Riff</string>
      <key>CFBundleIdentifier</key><string>org.flip-engineering.riff</string>
      <key>CFBundleExecutable</key><string>Riff</string>
      <key>CFBundlePackageType</key><string>APPL</string>
      <key>CFBundleVersion</key><string>1</string>
      <key>LSUIElement</key><true/>
      <key>LSMinimumSystemVersion</key><string>15.0</string>
    </dict></plist>
    """

    path = Path.expand("priv/desktop/Riff.app/Contents/Info.plist", __DIR__)
    if File.read(path) != {:ok, plist}, do: File.write!(path, plist)
    if Enum.any?(built, &(&1 == :built)), do: {:ok, []}, else: {:noop, []}
  end

  defp build(source, target) do
    stamp = Path.expand("_build/native-inputs/" <> Path.basename(source) <> ".sha256", __DIR__)

    fingerprint =
      :crypto.hash(:sha256, File.read!(source) <> File.read!(__ENV__.file))
      |> Base.encode16(case: :lower)

    if not File.exists?(target) or File.read(stamp) != {:ok, fingerprint} do
      File.mkdir_p!(Path.dirname(target))

      case System.cmd(
             "rustc",
             [
               "--edition=2021",
               "-O",
               "-C",
               "strip=symbols",
               "--remap-path-prefix",
               Path.expand("..", __DIR__) <> "=riff-src",
               source,
               "-o",
               target
             ],
             stderr_to_stdout: true
           ) do
        {_, 0} ->
          File.mkdir_p!(Path.dirname(stamp))
          File.write!(stamp, fingerprint)
          :built

        {message, _} ->
          Mix.raise(
            "Could not build the bundled native app helper (Rust 1.89+ required for builds):\n" <>
              message
          )
      end
    else
      :unchanged
    end
  end
end
