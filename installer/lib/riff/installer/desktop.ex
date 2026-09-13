defmodule Riff.Installer.Desktop do
  @moduledoc "Activate a verified desktop payload through the studio's existing idle/restart boundary."
  alias Riff.Installer.{AppEntry, Download, Payload, Transaction}
  @label "org.flip-engineering.riff"

  def activate(installed, progress, options \\ []) do
    if Keyword.get(options, :activation, :launch_agent) == :stage_only do
      {:prepared, installed}
    else
      unless :os.type() == {:unix, :darwin}, do: error!("This desktop package needs macOS.")
      check_launcher!(installed)
      config = launch_config(installed, options)
      before = current_selection(installed.root)
      before_runtime = current_runtime(installed.root)
      File.mkdir_p!(Path.join(installed.root, "workspace/data"))
      progress.(:starting, "Preparing your studio")

      Transaction.begin!(installed.root, %{
        kind: "external-installer",
        installer_id: Base.url_encode64(:crypto.strong_rand_bytes(24), padding: false),
        version: installed.version,
        previous: before["version"],
        owner: System.pid()
      })

      # The running app never reads these launch-only files. Its work continues.
      Payload.write_json(
        Path.join(installed.root, "runtime.json"),
        Payload.runtime_config(installed)
      )

      copy_launcher(installed)

      case system(config.url) do
        {:ok, state} ->
          unless state["managed"] == true,
            do:
              error!(
                "A different Riff session is using this address. Close it before finishing setup."
              )

          wait_and_restart(installed, config, state, progress)
          wait_until_closed(config.url, progress)

        :closed ->
          if loaded?(config.domain), do: wait_for_existing(installed, config, progress)

        {:error, _} ->
          error!(
            "Riff's current session is not responding. Your files are prepared; try setup again when it responds."
          )
      end

      progress.(:starting, "Opening your studio")
      if loaded?(config.domain), do: launchctl!(["bootout", config.domain])
      install_launch_agent(config)

      Payload.write_json(Path.join(installed.root, "current.json"), %{
        version: installed.version,
        previous_runtime:
          if(before["version"] == installed.version,
            do: before["previous_runtime"],
            else: before_runtime
          ),
        previous:
          if(before["version"] == installed.version,
            do: before["previous"],
            else: before["version"]
          )
      })

      launchctl!(["bootstrap", "gui/#{config.uid}", config.path])
      Transaction.commit!(installed.root)
      wait_ready(installed, config.url, progress, options)
      AppEntry.install(installed, config.url, options)
      {:ready, %{version: installed.version, url: config.url, root: installed.root}}
    end
  end

  @doc "Select already verified files while the caller owns the root lock and the studio's idle/restart boundary."
  def activate_prepared(installed, options \\ []) do
    if Keyword.get(options, :preflight, true), do: check_launcher!(installed)
    before = current_selection(installed.root)
    before_runtime = current_runtime(installed.root)

    Transaction.begin!(installed.root, %{
      version: installed.version,
      previous: before["version"],
      owner: System.pid()
    })

    Payload.write_json(
      Download.safe_target!(installed.root, "runtime.json"),
      Payload.runtime_config(installed)
    )

    copy_launcher(installed)

    Payload.write_json(Download.safe_target!(installed.root, "current.json"), %{
      version: installed.version,
      previous_runtime:
        if(before["version"] == installed.version,
          do: before["previous_runtime"],
          else: before_runtime
        ),
      previous:
        if(before["version"] == installed.version,
          do: before["previous"],
          else: before["version"]
        )
    })

    AppEntry.refresh(installed, options)
    Transaction.commit!(installed.root)
    installed
  rescue
    error ->
      Transaction.recover!(installed.root)
      reraise error, __STACKTRACE__
  end

  def ready?(%{version: version, url: url} = app) do
    case system(url) do
      {:ok, %{"version" => ^version, "engine" => %{"ready" => true}}} -> {:ok, app}
      _ -> :unavailable
    end
  end

  def open(app) do
    case ready?(app) do
      {:ok, _} -> Riff.Installer.Application.open_browser(app.url)
      _ -> {:error, :not_ready}
    end
  end

  defp launch_config(installed, options) do
    label = Keyword.get(options, :service_label, @label)

    unless is_binary(label) and Regex.match?(~r/\A[a-zA-Z0-9][a-zA-Z0-9.-]*\z/, label),
      do: error!("The startup service has an invalid name.")

    {uid, 0} = System.cmd("/usr/bin/id", ["-u"])
    uid = String.trim(uid)

    path =
      Keyword.get(
        options,
        :launch_agent_path,
        Path.join(System.user_home!(), "Library/LaunchAgents/#{label}.plist")
      )

    previous = if File.exists?(path), do: read_plist!(path), else: %{}
    launcher = Path.join(installed.root, "launcher.py")
    arguments = previous["ProgramArguments"] || []

    tail =
      if arguments == [] do
        ["--no-open", "--port", Integer.to_string(Keyword.get(options, :studio_port, 7878))]
      else
        case Enum.find_index(arguments, &(&1 == launcher)) do
          nil ->
            error!(
              "Another installation owns the Riff startup entry. Its settings have been preserved."
            )

          index ->
            Enum.drop(arguments, index + 1)
        end
      end

    port =
      case Enum.find_index(tail, &(&1 == "--port")) do
        nil -> 7878
        index -> Enum.at(tail, index + 1) |> String.to_integer()
      end

    logs = Path.join(installed.root, "logs")
    File.mkdir_p!(logs)

    environment =
      Map.merge(
        Map.drop(previous["EnvironmentVariables"] || %{}, ["PYTHONHOME", "PYTHONPATH"]),
        Map.new(
          Enum.reject(Payload.environment(installed), fn {_name, value} -> value == nil end)
        )
      )

    plist =
      Map.merge(previous, %{
        "Label" => label,
        "ProgramArguments" => [Payload.entry(installed, "python"), launcher | tail],
        "EnvironmentVariables" => environment,
        "RunAtLoad" => true,
        "KeepAlive" => true,
        "StandardOutPath" => previous["StandardOutPath"] || Path.join(logs, "studio.log"),
        "StandardErrorPath" => previous["StandardErrorPath"] || Path.join(logs, "studio.log")
      })

    %{
      path: path,
      plist: plist,
      domain: "gui/#{uid}/#{label}",
      uid: uid,
      url: "http://127.0.0.1:#{port}"
    }
  end

  defp wait_and_restart(installed, config, state, progress) do
    cond do
      state["busy"] or get_in(state, ["task", "status"]) == "running" ->
        progress.(:waiting, "Riff will finish setup after the current work is complete.")
        pause()

        case system(config.url) do
          {:ok, next} ->
            wait_and_restart(installed, config, next, progress)

          _ ->
            error!(
              "The current studio disconnected before setup could finish. Your files are prepared; try again."
            )
        end

      state["pending"] != nil and state["pending"]["version"] != installed.version ->
        error!(
          "A different Riff update is already prepared. Finish that update before installing this package."
        )

      true ->
        gate =
          installed.root
          |> Path.join(".installer-activation.json")
          |> File.read!()
          |> Jason.decode!()

        Payload.write_json(Path.join(installed.root, "workspace/data/pending-update.json"), %{
          kind: "external-installer",
          installer_id: gate["installer_id"],
          version: installed.version,
          path: installed.app
        })

        case request(:post, config.url <> "/api/system/restart", "{}") do
          {:ok, 200, _} ->
            :ok

          {:ok, 400, _} ->
            pause()

            case system(config.url) do
              {:ok, next} ->
                wait_and_restart(installed, config, next, progress)

              _ ->
                error!(
                  "The current studio disconnected while setup was waiting. Try again to continue."
                )
            end

          _ ->
            error!(
              "The studio could not finish its current session. Your files are prepared; try setup again."
            )
        end
    end
  end

  defp wait_for_existing(installed, config, progress) do
    # An auto-restarted launcher can already be waiting at our activation gate.
    # Only that exact launcher is safe to stop without the HTTP idle handshake.
    if waiting_launcher?(config.domain, Path.join(installed.root, "launcher.py")) do
      :ok
    else
      progress.(:waiting, "Waiting for the current Riff session")
      pause()

      case system(config.url) do
        {:ok, state} ->
          wait_and_restart(installed, config, state, progress)
          wait_until_closed(config.url, progress)

        :closed ->
          wait_for_existing(installed, config, progress)

        _ ->
          error!("The current studio did not respond. Its running work has been preserved.")
      end
    end
  end

  defp waiting_launcher?(domain, launcher) do
    with {description, 0} <-
           System.cmd("/bin/launchctl", ["print", domain], stderr_to_stdout: true),
         [_, pid] <- Regex.run(~r/\n\s*pid = (\d+)\n/, description),
         {command, 0} <-
           System.cmd("/bin/ps", ["-p", pid, "-o", "command="], stderr_to_stdout: true) do
      String.contains?(command, launcher) and not String.contains?(command, "studio.py")
    else
      _ -> false
    end
  end

  defp wait_until_closed(url, progress) do
    case system(url) do
      :closed ->
        :ok

      {:ok, _} ->
        progress.(:starting, "Finishing the current session")
        pause()
        wait_until_closed(url, progress)

      _ ->
        error!(
          "Riff could not confirm that its previous session ended. Try again to continue setup."
        )
    end
  end

  defp wait_ready(installed, url, progress, options) do
    timeout =
      Keyword.get(
        options,
        :startup_timeout,
        Application.get_env(:riff_installer, :studio_startup_timeout, 30_000)
      )

    deadline = System.monotonic_time(:millisecond) + timeout
    wait_ready_until(installed, url, progress, deadline)
  end

  defp wait_ready_until(installed, url, progress, deadline) do
    case system(url) do
      {:ok, %{"version" => version, "engine" => %{"ready" => true}}}
      when version == installed.version ->
        :ok

      _ ->
        if System.monotonic_time(:millisecond) >= deadline,
          do:
            error!(
              "Riff did not finish opening. Its previous version and your recordings are preserved; try setup again."
            )

        progress.(:starting, "Opening your studio")
        pause()
        wait_ready_until(installed, url, progress, deadline)
    end
  end

  defp system(url) do
    case request(:get, url <> "/api/system") do
      {:ok, 200, %{"version" => _, "engine" => _} = state} -> {:ok, state}
      {:error, %{reason: :econnrefused}} -> :closed
      {:error, %{reason: {:econnrefused, _}}} -> :closed
      _ -> {:error, :unavailable}
    end
  end

  defp request(method, url, body \\ nil) do
    uri = URI.parse(url)
    origin = "#{uri.scheme}://#{uri.host}:#{uri.port}"
    headers = [{"origin", origin}, {"content-type", "application/json"}, {"x-riff-request", "1"}]

    case Finch.build(method, url, headers, body)
         |> Finch.request(Riff.Installer.HTTP, receive_timeout: 2000) do
      {:ok, response} -> {:ok, response.status, Jason.decode!(response.body)}
      {:error, reason} -> {:error, reason}
    end
  rescue
    _ -> {:error, :response}
  end

  defp current_selection(root) do
    case File.read(Path.join(root, "current.json")) do
      {:ok, json} -> Jason.decode!(json)
      {:error, :enoent} -> %{}
      _ -> error!("The current installation could not be read.")
    end
  end

  defp current_runtime(root) do
    case File.read(Path.join(root, "runtime.json")) do
      {:ok, json} -> Jason.decode!(json)
      {:error, :enoent} -> nil
      _ -> error!("The current runtime could not be read.")
    end
  end

  defp copy_launcher(installed) do
    target = Download.safe_target!(installed.root, "launcher.py")

    temporary =
      target <> "." <> Base.url_encode64(:crypto.strong_rand_bytes(8), padding: false) <> ".tmp"

    File.write!(temporary, File.read!(Payload.entry(installed, "launcher")), [:exclusive, :sync])
    File.chmod!(temporary, 0o644)
    File.rename!(temporary, target)
  end

  defp check_launcher!(installed) do
    case System.cmd(
           Payload.entry(installed, "python"),
           [Payload.entry(installed, "launcher"), "--installer-protocol"],
           env: Payload.environment(installed),
           stderr_to_stdout: true
         ) do
      {output, 0} ->
        unless Jason.decode(output) == {:ok, %{"protocol" => 1}},
          do: error!("This application package needs an updated desktop launcher.")

      _ ->
        error!("This application package needs an updated desktop launcher.")
    end
  end

  defp read_plist!(path) do
    case System.cmd("/usr/bin/plutil", ["-convert", "json", "-o", "-", path],
           stderr_to_stdout: true
         ) do
      {output, 0} -> Jason.decode!(output)
      _ -> error!("The existing Riff startup settings could not be read.")
    end
  end

  defp install_launch_agent(config) do
    File.mkdir_p!(Path.dirname(config.path))
    temporary = config.path <> ".installing"
    File.write!(temporary, Jason.encode!(config.plist))

    case System.cmd("/usr/bin/plutil", ["-convert", "xml1", temporary], stderr_to_stdout: true) do
      {_, 0} -> File.rename!(temporary, config.path)
      _ -> error!("Riff's startup settings could not be prepared.")
    end
  end

  defp loaded?(domain) do
    {_output, code} = System.cmd("/bin/launchctl", ["print", domain], stderr_to_stdout: true)
    code == 0
  end

  defp launchctl!(arguments) do
    case System.cmd("/bin/launchctl", arguments, stderr_to_stdout: true) do
      {_, 0} -> :ok
      _ -> error!("macOS could not start Riff's background service. Reopen setup to try again.")
    end
  end

  defp pause, do: Process.sleep(Application.get_env(:riff_installer, :service_poll_interval, 250))
  defp error!(message), do: raise(Download.Error, reason: :activation, message: message)
end
