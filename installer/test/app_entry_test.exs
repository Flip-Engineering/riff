defmodule Riff.Installer.AppEntryTest do
  use ExUnit.Case, async: false
  @moduletag skip: :os.type() != {:unix, :darwin}
  alias Riff.Installer.{AppEntry, Download}

  setup do
    root =
      Path.join(
        System.tmp_dir!(),
        "riff-app-entry-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    File.mkdir_p!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    runtime = Path.join(root, "runtimes/test")
    bundled = Path.join(runtime, "control/lib/riff_installer-0.1.0/priv/desktop/Riff.app")
    File.mkdir_p!(Path.dirname(bundled))
    File.cp_r!(Application.app_dir(:riff_installer, "priv/desktop/Riff.app"), bundled)
    target = Path.join(root, "Applications/Riff.app")

    options = [
      desktop_app: true,
      desktop_app_path: target,
      service_label: "org.flip-engineering.riff.fixture.entry"
    ]

    %{
      installed: %{root: root, runtime: runtime},
      root: root,
      bundled: bundled,
      target: target,
      options: options
    }
  end

  test "copies the verified runtime's app entry and retains the owned prior bundle during replacement",
       context do
    assert :ok = AppEntry.install(context.installed, "http://127.0.0.1:58901", context.options)
    assert File.read!(Path.join(context.root, "studio-url.txt")) == "http://127.0.0.1:58901\n"

    assert File.read!(Path.join(context.root, "studio-service.txt")) ==
             "org.flip-engineering.riff.fixture.entry\n"

    assert File.read!(Path.join(context.target, "Contents/MacOS/Riff")) ==
             File.read!(Path.join(context.bundled, "Contents/MacOS/Riff"))

    assert :ok = AppEntry.install(context.installed, "http://127.0.0.1:58901", context.options)

    assert length(
             Path.wildcard(Path.join(context.root, "Applications/.riff-app-previous-*"),
               match_dot: true
             )
           ) == 1
  end

  test "foreign app paths are left byte-for-byte intact", context do
    File.mkdir_p!(context.target)
    sentinel = Path.join(context.target, "my-file")
    File.write!(sentinel, "foreign application")

    assert_raise Download.Error, ~r/could not be identified/, fn ->
      AppEntry.install(context.installed, "http://127.0.0.1:58901", context.options)
    end

    assert File.read!(sentinel) == "foreign application"
    refute File.exists?(Path.join(context.root, "studio-url.txt"))
  end
end
