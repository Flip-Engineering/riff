defmodule Riff.Installer.Lock do
  @moduledoc "A bundled Rust port holds an OS lock, released even after an installer crash."
  alias Riff.Installer.Download

  def acquire(destination), do: acquire_named(destination, ".riff-installer.lock")

  @doc "Reserve setup through launch readiness without preventing the launcher's metadata transaction."
  def acquire_operation(destination), do: acquire_named(destination, ".riff-setup.lock")

  def acquire_installation(destination) do
    with {:ok, operation} <- acquire_operation(destination) do
      case acquire(destination) do
        {:ok, activation} ->
          {:ok, activation, operation}

        error ->
          release(operation)
          error
      end
    end
  end

  defp acquire_named(destination, name) do
    path = Download.safe_target!(destination, name)

    case File.lstat(path) do
      {:ok, %{type: :regular}} -> acquire_port(path)
      {:error, :enoent} -> acquire_port(path)
      _ -> {:error, "The setup lock is not a regular file. Choose a different destination."}
    end
  end

  def sync_directory!(path) do
    executable = Application.app_dir(:riff_installer, "priv/native/riff-file-lock")

    case System.cmd(executable, ["--sync-directory", path], stderr_to_stdout: true) do
      {_, 0} ->
        :ok

      _ ->
        raise Download.Error,
          reason: :filesystem,
          message:
            "The update could not save its recovery state. Its existing files have been kept."
    end
  end

  def sync_ancestors!(path, root) do
    unless path == root or String.starts_with?(path, root <> "/"),
      do:
        raise(Download.Error,
          reason: :filesystem,
          message: "An update recovery directory is outside the installation."
        )

    sync_directory!(path)
    if path != root, do: sync_ancestors!(Path.dirname(path), root)
    :ok
  end

  def release(nil), do: :ok

  def release(port) do
    if Port.info(port) do
      Port.command(port, "release")

      receive do
        {^port, {:data, {:eol, "RELEASED"}}} -> :ok
        {^port, {:exit_status, _}} -> :ok
      after
        startup_timeout() -> Port.close(port)
      end
    end

    :ok
  end

  defp acquire_port(path) do
    executable = Application.app_dir(:riff_installer, "priv/native/riff-file-lock")

    port =
      Port.open({:spawn_executable, executable}, [
        :binary,
        :exit_status,
        {:line, byte_size("RELEASED\n")},
        args: [path]
      ])

    receive do
      {^port, {:data, {:eol, "LOCKED"}}} ->
        {:ok, port}

      {^port, {:data, {:eol, "BUSY"}}} ->
        {:error,
         "Another Riff setup window is using these downloads. Continue in that window, or close it before trying again."}

      {^port, {:exit_status, _}} ->
        {:error, "The setup folder could not be reserved. Check that it is writable."}
    after
      startup_timeout() ->
        Port.close(port)
        {:error, "The setup helper did not start. Reopen the installer and try again."}
    end
  rescue
    _ -> {:error, "The setup helper is missing from this package. Download the installer again."}
  end

  defp startup_timeout, do: Application.get_env(:riff_installer, :helper_startup_timeout, 30_000)
end
