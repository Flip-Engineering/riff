defmodule Riff.Installer.Session do
  @moduledoc "The shared setup operation used by the graphical installer and local agents."
  use GenServer
  alias Riff.Installer.{Desktop, Download, Lock, Manifest, Payload, Transaction}

  def start_link(options \\ []) do
    {name, options} = Keyword.pop(options, :name, __MODULE__)
    GenServer.start_link(__MODULE__, options, if(name, do: [name: name], else: []))
  end

  def status(server \\ __MODULE__), do: GenServer.call(server, :status)
  def token(server \\ __MODULE__), do: GenServer.call(server, :token)
  def endpoint(server \\ __MODULE__), do: GenServer.call(server, :endpoint)
  def set_endpoint(url, server \\ __MODULE__), do: GenServer.call(server, {:endpoint, url})
  def install(server \\ __MODULE__), do: GenServer.call(server, :install)
  def cancel(server \\ __MODULE__), do: GenServer.call(server, :cancel)
  def open(server \\ __MODULE__), do: GenServer.call(server, :open)

  @impl true
  def init(options) do
    root =
      Keyword.get(options, :install_root) || Application.get_env(:riff_installer, :install_root) ||
        default_root()

    payload =
      Payload.load(
        Keyword.get(options, :payload) || Application.get_env(:riff_installer, :payload)
      )

    manifest =
      if payload,
        do: Payload.model_manifest(payload),
        else: Keyword.get(options, :manifest, Manifest.pinned())

    assets = Manifest.assets(manifest, options)
    writer = Payload.writer_set(payload, root, options)

    destination =
      Keyword.get(options, :destination) || Application.get_env(:riff_installer, :destination) ||
        Payload.model_directory(root, manifest)

    {:ok,
     %{
       manifest: manifest,
       payload: payload,
       writer: writer,
       install_root: root,
       pinned_assets: assets,
       assets:
         Enum.map(
           assets,
           &Map.merge(Map.take(&1, [:id, :label]), %{
             state: :pending,
             downloaded_bytes: 0,
             total_bytes: &1.bytes
           })
         ) ++
           if(writer,
             do: [
               %{
                 id: "local-writer",
                 label: "Local writer",
                 state: :pending,
                 downloaded_bytes: 0,
                 total_bytes: writer.bytes
               }
             ],
             else: []
           ) ++
           if(payload,
             do: [
               %{
                 id: "riff-application",
                 label: "Riff studio",
                 state: :pending,
                 downloaded_bytes: 0,
                 total_bytes: payload.bytes
               }
             ],
             else: []
           ),
       destination: destination,
       options: options,
       token: Base.url_encode64(:crypto.strong_rand_bytes(32), padding: false),
       endpoint: nil,
       state: :idle,
       stage: :checking,
       current_file: nil,
       error: nil,
       message: nil,
       task: nil,
       lock: nil,
       operation_id: nil,
       app: nil
     }}
  end

  @impl true
  def handle_call(:status, _from, state), do: {:reply, public(state), state}
  def handle_call(:token, _from, state), do: {:reply, state.token, state}
  def handle_call(:endpoint, _from, state), do: {:reply, state.endpoint, state}
  def handle_call({:endpoint, url}, _from, state), do: {:reply, :ok, %{state | endpoint: url}}

  def handle_call(:install, _from, %{task: task} = state) when task != nil,
    do: {:reply, {:error, :busy, public(state)}, state}

  def handle_call(:install, _from, state) do
    case Lock.acquire(if(state.payload, do: state.install_root, else: state.destination)) do
      {:ok, lock} ->
        server = self()
        operation_id = Base.url_encode64(:crypto.strong_rand_bytes(16), padding: false)

        task =
          Task.Supervisor.async_nolink(Riff.Installer.Tasks, fn ->
            run(server, operation_id, state)
          end)

        next = %{
          state
          | state: :installing,
            stage: :checking,
            error: nil,
            message: nil,
            task: task,
            lock: lock,
            operation_id: operation_id,
            app: nil
        }

        {:reply, {:ok, public(next)}, next}

      {:error, message} ->
        next = %{state | state: :error, error: message}
        {:reply, {:ok, public(next)}, next}
    end
  end

  def handle_call(:cancel, _from, %{task: nil} = state), do: {:reply, {:ok, public(state)}, state}

  def handle_call(:cancel, _from, state) do
    # Only this operation's worker is stopped. Completed files and synchronous partial writes remain.
    Process.exit(state.task.pid, :shutdown)
    next = %{state | state: :cancelling, message: "Stopping setup…"}
    {:reply, {:ok, public(next)}, next}
  end

  def handle_call(
        {:progress, operation_id, id, phase, bytes},
        _from,
        %{operation_id: operation_id, state: :installing} = state
      ) do
    assets =
      Enum.map(state.assets, fn asset ->
        if asset.id == id, do: %{asset | state: phase, downloaded_bytes: bytes}, else: asset
      end)

    next = %{state | assets: assets, stage: phase, current_file: id}
    {:reply, :ok, next}
  end

  def handle_call({:progress, _, _, _, _}, _from, state), do: {:reply, :cancelled, state}

  def handle_call(
        {:message, operation_id, phase, message},
        _from,
        %{operation_id: operation_id, state: :installing} = state
      ) do
    {:reply, :ok, %{state | stage: phase, message: message}}
  end

  def handle_call({:message, _, _, _}, _from, state), do: {:reply, :cancelled, state}

  def handle_call(:open, _from, %{state: :ready} = state) do
    with {:ok, app} <- check_app(state),
         opener when is_function(opener, 1) <-
           Keyword.get(state.options, :app_opener, &Desktop.open/1),
         :ok <- opener.(app) do
      closing =
        Keyword.get(
          state.options,
          :finish_after_open,
          Application.get_env(:riff_installer, :finish_after_open, false)
        )

      if closing,
        do:
          Process.send_after(
            self(),
            :finish_session,
            Application.get_env(:riff_installer, :response_flush_timeout, 1_000)
          )

      {:reply, {:ok, public(state) |> Map.put(:opened, true) |> Map.put(:closing, closing)},
       state}
    else
      _ -> {:reply, {:error, :app_unavailable, public(state)}, state}
    end
  end

  def handle_call(:open, _from, state),
    do: {:reply, {:error, :app_unavailable, public(state)}, state}

  @impl true
  def handle_info(:finish_session, %{state: :ready} = state) do
    System.stop()
    {:noreply, state}
  end

  def handle_info({reference, result}, %{task: %{ref: reference}} = state) do
    Process.demonitor(reference, [:flush])
    Lock.release(state.lock)
    next = if state.state == :cancelling, do: cancelled(state), else: finish(result, state)
    {:noreply, %{next | task: nil, lock: nil}}
  end

  def handle_info({:DOWN, reference, :process, _pid, _reason}, %{task: %{ref: reference}} = state) do
    Lock.release(state.lock)

    next =
      if state.state == :cancelling do
        cancelled(state)
      else
        %{
          state
          | state: :error,
            error:
              state.error ||
                "Setup was interrupted. Choose Continue to resume the saved downloads."
        }
      end

    {:noreply, %{next | task: nil, lock: nil}}
  end

  def handle_info({port, {:exit_status, _}}, %{lock: port, task: task} = state)
      when task != nil do
    Process.exit(task.pid, :shutdown)

    {:noreply,
     %{
       state
       | state: :error,
         lock: nil,
         error: "Setup lost access to its download folder. Choose Continue to resume it."
     }}
  end

  def handle_info(_, state), do: {:noreply, state}

  @impl true
  def terminate(_reason, state) do
    # Wait for the writer to close before allowing another installer to claim the folder.
    if state.task, do: Task.shutdown(state.task)
    Lock.release(state.lock)
    :ok
  end

  defp run(server, operation_id, state) do
    if state.payload do
      Transaction.recover!(state.install_root)

      Download.safe_target!(
        state.install_root,
        Path.relative_to(Path.join(state.destination, ".model-set"), state.install_root)
      )
    end

    Enum.each(state.pinned_assets, fn asset ->
      progress = fn phase, bytes ->
        case GenServer.call(server, {:progress, operation_id, asset.id, phase, bytes}, :infinity) do
          :ok -> :ok
          :cancelled -> exit(:shutdown)
        end
      end

      Download.ensure(asset, state.destination, progress, state.options)
    end)

    Download.write_manifest!(state.destination, state.manifest)

    Payload.prepare_writer(
      state.writer,
      state.install_root,
      fn phase, bytes ->
        case GenServer.call(
               server,
               {:progress, operation_id, "local-writer", phase, bytes},
               :infinity
             ) do
          :ok -> :ok
          :cancelled -> exit(:shutdown)
        end
      end,
      state.options
    )

    if state.payload do
      progress = fn phase, bytes ->
        case GenServer.call(
               server,
               {:progress, operation_id, "riff-application", phase, bytes},
               :infinity
             ) do
          :ok -> :ok
          :cancelled -> exit(:shutdown)
        end
      end

      installed =
        Payload.stage(
          state.payload,
          state.install_root,
          state.destination,
          progress,
          state.options
        )

      message = fn phase, text ->
        case GenServer.call(server, {:message, operation_id, phase, text}, :infinity) do
          :ok -> :ok
          :cancelled -> exit(:shutdown)
        end
      end

      Desktop.activate(installed, message, state.options)
    else
      :ok
    end
  rescue
    error in Download.Error ->
      {:error, error.message}

    error in File.Error ->
      {:error,
       "A setup file could not be saved: #{:file.format_error(error.reason)}. Your existing files have been kept."}

    _ ->
      {:error,
       "Setup could not finish. Your existing files have been kept; choose Try again to continue."}
  end

  defp finish({:ready, app}, state),
    do: %{
      state
      | state: :ready,
        stage: :complete,
        current_file: nil,
        message: "Riff is ready.",
        app: app
    }

  defp finish({:prepared, _installed}, state),
    do: %{
      state
      | state: :prepared,
        stage: :activation_required,
        current_file: nil,
        message: "Riff and its music models are verified and prepared."
    }

  defp finish(:ok, state) do
    case check_app(state) do
      {:ok, app} ->
        %{
          state
          | state: :ready,
            stage: :complete,
            current_file: nil,
            message: "Riff is ready.",
            app: app
        }

      _ ->
        %{
          state
          | state: :prepared,
            stage: :app_required,
            current_file: nil,
            message: "Music models are verified. The Riff application package is still needed."
        }
    end
  end

  defp finish({:error, message}, state), do: %{state | state: :error, error: message}

  defp cancelled(state),
    do: %{
      state
      | state: :cancelled,
        message: "Saved downloads are kept. Continue whenever you’re ready.",
        error: nil
    }

  defp check_app(state) do
    case Keyword.get(state.options, :app_check) do
      check when is_function(check, 1) -> check.(state.destination)
      _ -> if(state.app, do: Desktop.ready?(state.app), else: :unavailable)
    end
  end

  defp public(state) do
    %{
      state: state.state,
      stage: state.stage,
      operation_id: state.operation_id,
      progress: %{
        downloaded_bytes: Enum.reduce(state.assets, 0, &(&1.downloaded_bytes + &2)),
        total_bytes: Enum.reduce(state.assets, 0, &(&1.total_bytes + &2)),
        completed_files: Enum.count(state.assets, &(&1.state == :verified)),
        total_files: length(state.assets)
      },
      assets: state.assets,
      current_file: state.current_file,
      message: state.message,
      error: state.error,
      can_retry: state.state in [:error, :cancelled, :prepared],
      app: %{
        available: state.app != nil or state.payload != nil,
        version:
          (state.app && state.app[:version]) ||
            (state.payload && state.payload.manifest["version"])
      },
      app_url: state.app && state.app[:url],
      model: %{
        name: "YuE2",
        license: "CC BY-NC 4.0",
        license_url: "https://creativecommons.org/licenses/by-nc/4.0/"
      }
    }
  end

  defp default_root do
    case :os.type() do
      {:unix, :darwin} ->
        Path.join(System.user_home!(), "Library/Application Support/Riff")

      _ ->
        Path.join(
          System.get_env("XDG_DATA_HOME") || Path.join(System.user_home!(), ".local/share"),
          "riff"
        )
    end
  end
end
