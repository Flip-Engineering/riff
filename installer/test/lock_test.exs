defmodule Riff.Installer.LockTest do
  use ExUnit.Case, async: false
  alias Riff.Installer.Lock

  test "kernel lock survives competing claims and is released when its owner exits" do
    directory =
      Path.join(
        System.tmp_dir!(),
        "riff-installer-lock-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    on_exit(fn -> File.rm_rf!(directory) end)
    test = self()

    owner =
      spawn(fn ->
        {:ok, lock} = Lock.acquire(directory)
        send(test, {:locked, lock})

        receive do
          :finish -> :ok
        end
      end)

    assert_receive {:locked, _}, 2000
    assert {:error, message} = Lock.acquire(directory)
    assert message =~ "Another Riff setup window"
    monitor = Process.monitor(owner)
    send(owner, :finish)
    assert_receive {:DOWN, ^monitor, :process, ^owner, :normal}, 2000
    # Synchronize on the actual advisory lock rather than assuming OS port exit timing.
    lock = acquire_after_exit(directory, System.monotonic_time(:millisecond) + 2000)
    assert :ok = Lock.release(lock)
    {:ok, again} = Lock.acquire(directory)
    assert :ok = Lock.release(again)
  end

  defp acquire_after_exit(directory, deadline) do
    case Lock.acquire(directory) do
      {:ok, lock} ->
        lock

      {:error, _} ->
        assert System.monotonic_time(:millisecond) < deadline
        Process.sleep(5)
        acquire_after_exit(directory, deadline)
    end
  end
end
