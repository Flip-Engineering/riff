defmodule Riff.Installer.AppEntry do
  @moduledoc "Install the small persistent graphical entry without replacing foreign applications."
  alias Riff.Installer.{Download, Payload}
  @identifier "org.flip-engineering.riff"

  def install(installed, url, options \\ []) do
    default_root = Path.join(System.user_home!(), "Library/Application Support/Riff")

    target =
      Keyword.get(
        options,
        :desktop_app_path,
        Path.join(System.user_home!(), "Applications/Riff.app")
      )

    enabled = Keyword.get(options, :desktop_app, installed.root == default_root)

    if enabled do
      validate_existing!(target)

      source =
        case Path.wildcard(
               Path.join(installed.runtime, "control/lib/riff_installer-*/priv/desktop/Riff.app")
             ) do
          [path] -> path
          _ -> error!("The application package is missing its Riff app entry.")
        end

      unless File.dir?(source),
        do: error!("The application package is missing its Riff app entry.")

      File.mkdir_p!(Path.dirname(target))
      suffix = Base.url_encode64(:crypto.strong_rand_bytes(10), padding: false)
      staged = target <> ".installing-" <> suffix
      File.cp_r!(source, staged)

      if File.exists?(target),
        do: File.rename!(target, Path.join(Path.dirname(target), ".riff-app-previous-" <> suffix))

      File.rename!(staged, target)
      write_text(Download.safe_target!(installed.root, "studio-url.txt"), url <> "\n")
      label = Keyword.get(options, :service_label, "org.flip-engineering.riff")
      write_text(Download.safe_target!(installed.root, "studio-service.txt"), label <> "\n")

      timeout =
        Keyword.get(
          options,
          :startup_timeout,
          Application.get_env(:riff_installer, :studio_startup_timeout, 30_000)
        )

      write_text(
        Download.safe_target!(installed.root, "startup-timeout-ms.txt"),
        Integer.to_string(timeout) <> "\n"
      )

      Payload.write_json(Download.safe_target!(installed.root, "desktop-entry.json"), %{
        format_version: 1,
        path: target
      })
    end

    :ok
  end

  def refresh(installed, options \\ []) do
    case File.read(Path.join(installed.root, "studio-url.txt")) do
      {:ok, url} -> install(installed, String.trim(url), options)
      {:error, :enoent} -> :ok
      _ -> error!("Riff's app entry settings could not be read.")
    end
  end

  defp validate_existing!(target) do
    case File.lstat(target) do
      {:error, :enoent} ->
        :ok

      {:ok, %{type: :directory}} ->
        case System.cmd(
               "/usr/bin/plutil",
               [
                 "-extract",
                 "CFBundleIdentifier",
                 "raw",
                 "-o",
                 "-",
                 Path.join(target, "Contents/Info.plist")
               ],
               stderr_to_stdout: true
             ) do
          {value, 0} ->
            unless String.trim(value) == @identifier,
              do:
                error!(
                  "Another app already uses the Riff name in Applications. It has been kept."
                )

          _ ->
            error!("An existing Riff app could not be identified. It has been kept.")
        end

      _ ->
        error!("An existing application path is not a regular folder. It has been kept.")
    end
  end

  defp write_text(path, text) do
    temporary =
      path <> "." <> Base.url_encode64(:crypto.strong_rand_bytes(8), padding: false) <> ".tmp"

    File.write!(temporary, text, [:exclusive, :sync])
    File.chmod!(temporary, 0o600)
    File.rename!(temporary, path)
  end

  defp error!(message), do: raise(Download.Error, reason: :app_entry, message: message)
end
