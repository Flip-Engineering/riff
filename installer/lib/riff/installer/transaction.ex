defmodule Riff.Installer.Transaction do
  @moduledoc "Durable metadata activation. An interrupted prepared transaction rolls back before another launch."
  alias Riff.Installer.{Download, Lock, Payload}
  @launch_files ["runtime.json", "launcher.py", "current.json"]
  @files @launch_files ++ ["workspace/data/engine.json", "workspace/data/engine-activations.json"]

  def begin!(root, metadata) do
    recover!(root)
    id = Base.url_encode64(:crypto.strong_rand_bytes(24), padding: false)

    backups =
      Map.new(@files, fn name ->
        original = Download.safe_target!(root, name)

        case File.lstat(original) do
          {:ok, %{type: :regular}} ->
            relative = Path.join([".activation", id, name])
            backup = Download.safe_target!(root, relative)
            File.write!(backup, File.read!(original), [:exclusive, :sync])
            File.chmod!(backup, 0o600)
            Lock.sync_ancestors!(Path.dirname(backup), root)
            {name, relative}

          {:error, :enoent} ->
            {name, nil}

          _ ->
            error!("A launch setting is not a regular file. Its contents have been kept.")
        end
      end)

    gate =
      Map.merge(metadata, %{
        transaction: %{protocol: 1, id: id, phase: "prepared", backups: backups}
      })

    Payload.write_json(Download.safe_target!(root, ".installer-activation.json"), gate)
    gate
  end

  def commit!(root) do
    path = Download.safe_target!(root, ".installer-activation.json")
    gate = path |> File.read!() |> Jason.decode!()
    gate = put_in(gate, ["transaction", "phase"], "committed")
    Payload.write_json(path, gate)
    File.rm!(path)
    Lock.sync_directory!(root)
  end

  def recover!(root) do
    path = Download.safe_target!(root, ".installer-activation.json")

    case File.lstat(path) do
      {:error, :enoent} ->
        :ok

      {:ok, %{type: :regular}} ->
        gate = path |> File.read!() |> Jason.decode!()

        case gate["transaction"] do
          nil ->
            :ok

          %{"protocol" => 1, "phase" => "committed"} ->
            :ok

          %{"protocol" => 1, "phase" => "prepared", "id" => id, "backups" => backups} ->
            unless is_binary(id) and Regex.match?(~r/\A[A-Za-z0-9_-]+\z/, id) and is_map(backups) and
                     Enum.sort(Map.keys(backups)) in [Enum.sort(@launch_files), Enum.sort(@files)],
                   do:
                     error!(
                       "An interrupted update has an invalid recovery journal. Reopen Riff Setup."
                     )

            for name <- Map.keys(backups) do
              target = Download.safe_target!(root, name)

              case backups[name] do
                nil ->
                  case File.lstat(target) do
                    {:error, :enoent} ->
                      :ok

                    {:ok, %{type: :regular}} ->
                      File.rm!(target)

                    _ ->
                      error!("An interrupted update could not restore a linked launch setting.")
                  end

                relative ->
                  unless relative == Path.join([".activation", id, name]),
                    do: error!("An update backup is outside its recovery journal.")

                  source = Download.safe_target!(root, relative)

                  unless File.lstat!(source).type == :regular,
                    do: error!("An update backup is not a regular file.")

                  temporary = target <> ".recovering-" <> id
                  # A retry may find its own previous temporary write; preserve it.
                  if File.exists?(temporary),
                    do:
                      File.rename!(
                        temporary,
                        temporary <>
                          "." <> Base.url_encode64(:crypto.strong_rand_bytes(8), padding: false)
                      )

                  File.write!(temporary, File.read!(source), [:exclusive, :sync])
                  File.rename!(temporary, target)
              end

              Lock.sync_directory!(Path.dirname(target))
            end

          _ ->
            error!(
              "An interrupted update has an unsupported recovery journal. Reopen Riff Setup."
            )
        end

        Lock.sync_directory!(root)
        File.rm!(path)
        Lock.sync_directory!(root)

      _ ->
        error!("An update recovery journal is not a regular file.")
    end
  end

  defp error!(message), do: raise(Download.Error, reason: :activation_recovery, message: message)
end
