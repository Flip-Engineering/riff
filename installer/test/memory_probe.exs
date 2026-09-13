alias Riff.Installer.{Download, TestFixture}

measurements =
  for bytes <- [8 * 1_048_576, 64 * 1_048_576] do
    contents = :crypto.strong_rand_bytes(bytes)
    {server, url} = TestFixture.start!(contents: contents, mode: :stream)
    asset = TestFixture.asset(contents, url)
    destination = Path.expand("_build/memory-probe-#{bytes}")
    peak = :atomics.new(2, signed: false)
    started = System.monotonic_time(:millisecond)

    task =
      Task.async(fn ->
        Download.ensure(
          asset,
          destination,
          fn _, _ ->
            {:memory, heap} = Process.info(self(), :memory)
            {:binary, binaries} = Process.info(self(), :binary)
            binary_bytes = Enum.reduce(binaries, 0, fn {_, size, _}, sum -> sum + size end)
            :atomics.put(peak, 1, max(heap, :atomics.get(peak, 1)))
            :atomics.put(peak, 2, max(binary_bytes, :atomics.get(peak, 2)))
          end,
          allow_local_http: true
        )
      end)

    result = Task.await(task, :infinity)
    Supervisor.stop(server)

    %{
      asset_bytes: bytes,
      result: result,
      peak_worker_heap_bytes: :atomics.get(peak, 1),
      peak_worker_binary_refs_bytes: :atomics.get(peak, 2),
      elapsed_ms: System.monotonic_time(:millisecond) - started
    }
  end

File.write!("_build/download-memory-validation.json", Jason.encode!(measurements, pretty: true))
IO.puts(Jason.encode!(measurements))
