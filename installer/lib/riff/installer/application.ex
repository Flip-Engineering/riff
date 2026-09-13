defmodule Riff.Installer.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Finch,
       name: Riff.Installer.HTTP, pools: %{default: [size: 1, count: 1, protocols: [:http1]]}},
      {Task.Supervisor, name: Riff.Installer.Tasks},
      Riff.Installer.Session
    ]

    server? = Application.get_env(:riff_installer, :start_server, true)

    children =
      if server? do
        web =
          Supervisor.child_spec(
            {Bandit,
             plug: Riff.Installer.Router,
             ip: {127, 0, 0, 1},
             port: Application.get_env(:riff_installer, :port, 0),
             startup_log: false},
            id: Riff.Installer.Web
          )

        children ++ [web]
      else
        children
      end

    with {:ok, supervisor} <-
           Supervisor.start_link(children,
             strategy: :one_for_one,
             name: Riff.Installer.Supervisor
           ) do
      if server? do
        {_id, server, _type, _modules} =
          Enum.find(Supervisor.which_children(supervisor), fn {id, _, _, _} ->
            id == Riff.Installer.Web
          end)

        {:ok, {_address, port}} = ThousandIsland.listener_info(server)
        url = "http://127.0.0.1:#{port}"
        :ok = Riff.Installer.Session.set_endpoint(url)

        if Application.get_env(:riff_installer, :open_browser, false),
          do: Task.Supervisor.start_child(Riff.Installer.Tasks, fn -> open_browser(url) end)
      end

      {:ok, supervisor}
    end
  end

  def open_browser(url) do
    executable =
      case :os.type() do
        {:unix, :darwin} -> "/usr/bin/open"
        _ -> System.find_executable("xdg-open")
      end

    if executable do
      case System.cmd(executable, [url], stderr_to_stdout: true) do
        {_, 0} -> :ok
        _ -> {:error, :opener}
      end
    else
      {:error, :opener}
    end
  end
end
