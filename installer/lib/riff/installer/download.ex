defmodule Riff.Installer.Download do
  @moduledoc "Bounded-memory, pinned, resumable downloads. Only verified files receive their final names."
  alias Riff.Installer.Manifest

  defmodule Error do
    defexception [:reason, :message]
  end

  def ensure(asset, destination, progress, options \\ []) do
    Manifest.validate_path!(asset.id)
    target = safe_target!(destination, asset.id)
    partial = target <> ".part"
    regular_or_missing!(target)
    regular_or_missing!(partial)

    if valid_existing?(target, asset, progress, options) do
      progress.(:verified, asset.bytes)
      :reused
    else
      preserve_unverified!(target)
      prepare_partial!(partial, asset)
      {received, digest} = hash_partial(partial, progress, options)

      if received == asset.bytes do
        finish!(partial, target, asset, received, digest, progress)
      else
        progress.(:downloading, received)

        result =
          transfer(asset.url, asset, partial, received, digest, progress, options, MapSet.new())

        case result do
          {:ok, state} ->
            finish!(partial, target, asset, state.received, state.digest, progress)

          {:restart, _state} ->
            # Some mirrors ignore Range. Preserve that partial and make a clean request.
            preserve_unverified!(partial)
            progress.(:downloading, 0)

            {:ok, state} =
              transfer(
                asset.url,
                asset,
                partial,
                0,
                :crypto.hash_init(:sha256),
                progress,
                options,
                MapSet.new()
              )

            finish!(partial, target, asset, state.received, state.digest, progress)
        end
      end

      :downloaded
    end
  end

  def safe_target!(destination, relative) do
    Manifest.validate_path!(relative)
    root = Path.expand(destination)
    File.mkdir_p!(root)

    unless File.lstat!(root).type == :directory,
      do: fail!(:unsafe_path, "The setup destination is not a regular directory.")

    relative
    |> Path.split()
    |> Enum.drop(-1)
    |> Enum.reduce(root, fn component, parent ->
      directory = Path.join(parent, component)

      case File.lstat(directory) do
        {:ok, %{type: :directory}} ->
          :ok

        {:error, :enoent} ->
          File.mkdir!(directory)

        _ ->
          fail!(
            :unsafe_path,
            "A model folder is not a regular directory. Choose a different destination."
          )
      end

      directory
    end)

    Path.join(root, relative)
  end

  def write_manifest!(destination, manifest) do
    target = safe_target!(destination, "manifest.json")
    regular_or_missing!(target)
    temporary = target <> "." <> unique_suffix() <> ".tmp"
    {:ok, file} = File.open(temporary, [:write, :binary, :exclusive, :raw])

    try do
      :ok = :file.write(file, Jason.encode_to_iodata!(manifest, pretty: true))
      :ok = :file.sync(file)
    after
      File.close(file)
    end

    File.rename!(temporary, target)
  end

  defp valid_existing?(target, asset, progress, options) do
    case File.stat(target) do
      {:ok, %{size: size}} when size == asset.bytes ->
        progress.(:checking, 0)
        {_size, digest} = hash_file(target, fn bytes -> progress.(:checking, bytes) end, options)
        hex(digest) == asset.sha256

      _ ->
        false
    end
  end

  defp prepare_partial!(partial, asset) do
    case File.stat(partial) do
      {:ok, %{size: size}} when size > asset.bytes -> preserve_unverified!(partial)
      _ -> :ok
    end
  end

  defp hash_partial(partial, progress, options) do
    if File.exists?(partial) do
      progress.(:checking, 0)
      hash_file(partial, fn bytes -> progress.(:checking, bytes) end, options)
    else
      {0, :crypto.hash_init(:sha256)}
    end
  end

  defp hash_file(path, progress, options) do
    chunk =
      Keyword.get(
        options,
        :hash_chunk_bytes,
        Application.get_env(:riff_installer, :hash_chunk_bytes, 1_048_576)
      )

    {:ok, input} = File.open(path, [:read, :binary, :raw])

    try do
      hash_chunks(input, chunk, 0, :crypto.hash_init(:sha256), progress)
    after
      File.close(input)
    end
  end

  defp hash_chunks(input, chunk, received, digest, progress) do
    case :file.read(input, chunk) do
      {:ok, data} ->
        next = received + byte_size(data)
        progress.(next)
        hash_chunks(input, chunk, next, :crypto.hash_update(digest, data), progress)

      :eof ->
        {received, digest}

      {:error, reason} ->
        fail!(:file_read, "A saved model could not be read: #{:file.format_error(reason)}.")
    end
  end

  defp transfer(url, asset, partial, offset, digest, progress, options, visited) do
    validate_url!(url, options)

    if MapSet.member?(visited, url),
      do: fail!(:redirect_loop, "The download service returned a redirect loop. Try again later.")

    visited = MapSet.put(visited, url)
    headers = [{"accept-encoding", "identity"}, {"user-agent", "Riff-Installer/0.1"}]
    headers = if offset > 0, do: [{"range", "bytes=#{offset}-"} | headers], else: headers
    {:ok, output} = File.open(partial, [:append, :binary, :raw])

    result =
      try do
        initial = %{
          status: nil,
          headers: [],
          received: offset,
          digest: digest,
          action: nil,
          valid: false
        }

        request = Finch.build(:get, url, headers)

        callback = fn event, state ->
          receive_event(event, state, output, asset, offset, progress)
        end

        timeout =
          Keyword.get(
            options,
            :receive_timeout,
            Application.get_env(:riff_installer, :receive_timeout, 30_000)
          )

        Finch.stream_while(
          request,
          Keyword.get(options, :finch, Riff.Installer.HTTP),
          initial,
          callback,
          receive_timeout: timeout,
          request_timeout: :infinity
        )
      after
        # Writes are synchronous; shutdown also closes the worker-owned descriptor.
        File.close(output)
      end

    case result do
      {:ok, %{action: {:redirect, location}}} ->
        next = URI.merge(url, location) |> URI.to_string()
        transfer(next, asset, partial, offset, digest, progress, options, visited)

      {:ok, %{action: :restart} = state} ->
        {:restart, state}

      {:ok, %{action: {:error, reason, message}}} ->
        fail!(reason, message)

      {:ok, %{valid: true} = state} ->
        {:ok, state}

      {:ok, _} ->
        fail!(:response, "The download service did not return a model file.")

      {:error, _error, _state} ->
        fail!(
          :network,
          "The download was interrupted. Choose Continue to resume the saved download."
        )
    end
  end

  defp receive_event({:status, status}, state, _output, _asset, _offset, _progress),
    do: {:cont, %{state | status: status}}

  defp receive_event({:headers, headers}, state, _output, asset, offset, _progress) do
    headers = Map.new(headers, fn {name, value} -> {String.downcase(name), value} end)
    state = %{state | headers: headers}

    cond do
      state.status in [301, 302, 303, 307, 308] and is_binary(headers["location"]) ->
        {:halt, %{state | action: {:redirect, headers["location"]}}}

      state.status == 200 and offset > 0 ->
        {:halt, %{state | action: :restart}}

      state.status not in [200, 206] ->
        {:halt,
         %{
           state
           | action:
               {:error, :http_status,
                "The download service returned HTTP #{state.status}. Try again when the service is available."}
         }}

      headers["content-encoding"] not in [nil, "identity"] ->
        {:halt,
         %{
           state
           | action:
               {:error, :encoding,
                "The download service changed the file encoding. Try again later."}
         }}

      not range_valid?(state.status, headers["content-range"], offset, asset.bytes) ->
        {:halt,
         %{
           state
           | action:
               {:error, :range,
                "The download service returned the wrong file range. Your partial download has been kept."}
         }}

      not length_valid?(headers["content-length"], asset.bytes - offset) ->
        {:halt,
         %{
           state
           | action:
               {:error, :size, "The download size does not match this release's verified model."}
         }}

      true ->
        {:cont, %{state | valid: true}}
    end
  end

  defp receive_event({:data, data}, %{valid: true} = state, output, asset, _offset, progress) do
    received = state.received + byte_size(data)

    if received > asset.bytes do
      {:halt,
       %{
         state
         | action:
             {:error, :size,
              "The download exceeded its verified size. Your saved files have been kept."}
       }}
    else
      case :file.write(output, data) do
        :ok ->
          progress.(:downloading, received)
          {:cont, %{state | received: received, digest: :crypto.hash_update(state.digest, data)}}

        {:error, reason} ->
          {:halt,
           %{
             state
             | action:
                 {:error, :file_write,
                  "The model could not be saved: #{:file.format_error(reason)}."}
           }}
      end
    end
  end

  defp receive_event({:trailers, _}, state, _output, _asset, _offset, _progress),
    do: {:cont, state}

  defp receive_event(_, state, _output, _asset, _offset, _progress),
    do: {:halt, %{state | action: {:error, :response, "The download response was incomplete."}}}

  defp finish!(partial, target, asset, received, digest, progress) do
    if received != asset.bytes,
      do: fail!(:size, "The download ended early. Choose Continue to resume it.")

    progress.(:verifying, received)

    if hex(digest) != asset.sha256 do
      preserve_unverified!(partial)

      fail!(
        :checksum,
        "This download did not pass verification. Choose Try again to download a fresh copy."
      )
    end

    {:ok, file} = File.open(partial, [:read, :write, :binary, :raw])

    try do
      :ok = :file.sync(file)
    after
      File.close(file)
    end

    File.rename!(partial, target)
    progress.(:verified, received)
  end

  defp range_valid?(200, nil, 0, _total), do: true

  defp range_valid?(206, range, offset, total) when is_binary(range) do
    range == "bytes #{offset}-#{total - 1}/#{total}"
  end

  defp range_valid?(_, _, _, _), do: false
  defp length_valid?(nil, _), do: true
  defp length_valid?(value, expected), do: value == Integer.to_string(expected)

  defp validate_url!(url, options) do
    uri = URI.parse(url)

    local_test? =
      Keyword.get(options, :allow_local_http, false) and uri.scheme == "http" and
        uri.host in ["127.0.0.1", "localhost", "::1"]

    unless (uri.scheme == "https" or local_test?) and is_binary(uri.host) and uri.userinfo == nil do
      fail!(:url, "Model downloads require a secure source.")
    end
  end

  defp regular_or_missing!(path) do
    case File.lstat(path) do
      {:ok, %{type: :regular}} ->
        :ok

      {:error, :enoent} ->
        :ok

      _ ->
        fail!(
          :unsafe_path,
          "A model destination is not a regular file. Choose a different destination."
        )
    end
  end

  defp preserve_unverified!(path) do
    case File.lstat(path) do
      {:ok, %{type: :regular}} -> File.rename!(path, path <> ".unverified." <> unique_suffix())
      {:error, :enoent} -> :ok
      _ -> regular_or_missing!(path)
    end
  end

  defp unique_suffix, do: Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
  defp hex(digest), do: digest |> :crypto.hash_final() |> Base.encode16(case: :lower)
  defp fail!(reason, message), do: raise(Error, reason: reason, message: message)
end
