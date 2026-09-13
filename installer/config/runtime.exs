import Config

config :riff_installer,
  destination: System.get_env("RIFF_INSTALLER_DESTINATION"),
  install_root: System.get_env("RIFF_INSTALLER_ROOT"),
  payload: System.get_env("RIFF_INSTALLER_PAYLOAD"),
  open_browser: System.get_env("RIFF_INSTALLER_OPEN_BROWSER", "true") != "false",
  port: String.to_integer(System.get_env("RIFF_INSTALLER_PORT", "0"))
