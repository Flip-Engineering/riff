import Config

config :riff_installer,
  start_server: config_env() != :test,
  open_browser: config_env() == :prod,
  finish_after_open: config_env() == :prod,
  port: 0,
  receive_timeout: 30_000,
  # A bounded file/hash buffer, independent of model size.
  hash_chunk_bytes: 1_048_576

config :logger, level: :warning
