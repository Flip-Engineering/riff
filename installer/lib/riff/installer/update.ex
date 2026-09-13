defmodule Riff.Installer.Update do
  @moduledoc "The desktop updater and agent entry point. It never starts a GUI or calls the running studio."
  alias Riff.Installer.{Desktop, Download, Lock, Manifest, Payload, Transaction}

  def main do
    # `release eval` loads code without starting the graphical application.
    {:ok, _} = Application.ensure_all_started(:finch)
    {:ok, _} = Application.ensure_all_started(:jason)

    {:ok, _http} =
      Finch.start_link(
        name: Riff.Installer.HTTP,
        pools: %{default: [size: 1, count: 1, protocols: [:http1]]}
      )

    result =
      try do
        case IO.gets(:stdio, "") do
          value when is_binary(value) -> execute(Jason.decode!(value), &emit/1)
          _ -> raise ArgumentError, "No update request was supplied."
        end
      rescue
        error in Download.Error ->
          %{protocol: 1, type: "result", status: "error", message: error.message}

        _ ->
          %{
            protocol: 1,
            type: "result",
            status: "error",
            message:
              "Riff could not finish preparing this update. Its existing files have been kept."
          }
      end

    emit(result)
    if result.status == "error", do: System.halt(1)
  end

  def execute(request, progress, options \\ []) do
    unless is_map(request) and request["protocol"] == 1 and
             request["action"] in ["prepare", "activate"] and
             is_binary(request["payload"]) and is_binary(request["root"]) and
             Path.type(request["root"]) == :absolute and
             Path.type(request["payload"]) == :absolute do
      raise ArgumentError, "The update request is invalid."
    end

    root = Path.expand(request["root"])
    payload = Payload.load(request["payload"])
    manifest = Payload.model_manifest(payload)
    models = Payload.model_directory(root, manifest)
    writer = Payload.writer_set(payload, root, options)

    lock =
      case Lock.acquire(root) do
        {:ok, port} -> port
        {:error, message} -> raise Download.Error, reason: :busy, message: message
      end

    try do
      Transaction.recover!(root)
      Download.safe_target!(root, Path.relative_to(Path.join(models, ".model-set"), root))

      report = fn phase, id, bytes, total ->
        progress.(%{
          protocol: 1,
          type: "progress",
          stage: phase,
          current_file: id,
          message: progress_message(phase, id),
          downloaded_bytes: bytes,
          total_bytes: total
        })
      end

      if request["action"] == "prepare" do
        for asset <- Manifest.assets(manifest, options) do
          Download.ensure(
            asset,
            models,
            fn phase, bytes -> report.(phase, asset.id, bytes, asset.bytes) end,
            options
          )
        end

        Download.write_manifest!(models, manifest)
      else
        # Rehash, but never download during activation while the studio holds its
        # idle boundary. Corruption sends the update back through preparation.
        Payload.verify_models!(models, manifest, report)
      end

      if writer do
        if request["action"] == "prepare" do
          Payload.prepare_writer(
            writer,
            root,
            fn phase, bytes ->
              report.(phase, "local-writer", bytes, writer.bytes)
            end,
            options
          )
        else
          Payload.verify_models!(writer.directory, writer.manifest, report)
        end
      end

      installed =
        Payload.stage(
          payload,
          root,
          models,
          fn phase, bytes ->
            report.(phase, "riff-application", bytes, payload.bytes)
          end,
          options
        )

      status =
        if request["action"] == "activate" do
          Desktop.activate_prepared(installed, options)
          "activated"
        else
          "prepared"
        end

      %{
        protocol: 1,
        type: "result",
        status: status,
        version: installed.version,
        path: installed.app,
        runtime_id: installed.runtime_id,
        model_directory: models
      }
    after
      Lock.release(lock)
    end
  end

  defp emit(event), do: IO.puts(Jason.encode!(event))
  defp progress_message(_phase, "riff-application"), do: "Preparing Riff"
  defp progress_message(:checking, _id), do: "Checking saved music models"
  defp progress_message(:verified, _id), do: "Music model verified"
  defp progress_message(_phase, _id), do: "Downloading music models"
end
