Code.require_file("support.exs", __DIR__)
alias Riff.Desktop.Build, as: B

unless :os.type() == {:unix, :darwin}, do: raise("Build the macOS media tools on macOS")
{options, [], []} = OptionParser.parse(System.argv(), strict: [jobs: :integer, output: :string])
jobs = Keyword.get(options, :jobs, 2)
unless jobs > 0, do: raise("Build jobs must be positive")
stamp = Integer.to_string(System.system_time(:millisecond))
root = Path.expand(Keyword.get(options, :output, Path.join(B.root(), ".build/media-" <> stamp)))
if File.exists?(root), do: raise("Choose a fresh media build directory")
File.mkdir_p!(root)
prefix = Path.join(root, "install")
File.mkdir_p!(prefix)
flags = "-O2 -ffile-prefix-map=#{root}=riff-media -fdebug-prefix-map=#{root}=riff-media"

environment = [
  {"CFLAGS", flags},
  {"CPPFLAGS", "-I#{prefix}/include"},
  {"LDFLAGS", "-L#{prefix}/lib"},
  {"PKG_CONFIG_PATH", Path.join(prefix, "lib/pkgconfig")}
]

for name <- ["lame", "x264", "ffmpeg"] do
  IO.puts("Building pinned #{name}")
  source_parent = B.unpack!(B.fetch!(name), Path.join(root, name))
  [directory] = File.ls!(source_parent)
  source = Path.join(source_parent, directory)

  arguments =
    case name do
      "lame" ->
        [
          "--prefix=#{prefix}",
          "--disable-shared",
          "--enable-static",
          "--disable-frontend",
          "--disable-dependency-tracking"
        ]

      "x264" ->
        [
          "--prefix=#{prefix}",
          "--enable-static",
          "--disable-cli",
          "--disable-opencl",
          "--extra-cflags=#{flags}"
        ]

      "ffmpeg" ->
        [
          "--prefix=#{prefix}",
          "--disable-shared",
          "--enable-static",
          "--disable-debug",
          "--disable-doc",
          "--disable-ffplay",
          "--disable-network",
          "--disable-autodetect",
          "--enable-gpl",
          "--enable-libx264",
          "--enable-libmp3lame",
          "--enable-zlib",
          "--disable-everything",
          "--enable-protocol=file,pipe",
          "--enable-demuxer=wav,image2pipe,image_png_pipe,mp3,mov,h264",
          "--enable-muxer=mp4,mp3,wav,h264",
          "--enable-decoder=pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le,png,mjpeg,mp3,aac,h264",
          "--enable-encoder=libx264,aac,libmp3lame,pcm_s16le,pcm_f32le",
          "--enable-parser=png,mjpeg,mpegaudio,aac,h264",
          "--enable-filter=aresample,format,scale,anull,null",
          "--enable-swscale",
          "--enable-swresample",
          "--extra-cflags=#{flags} -I#{prefix}/include",
          "--extra-ldflags=-L#{prefix}/lib",
          "--pkg-config-flags=--static"
        ]
    end

  log = B.run!(Path.join(source, "configure"), arguments, cd: source, env: environment)
  File.write!(Path.join(root, name <> "-configure.log"), log)

  if String.contains?(log, "did not match anything"),
    do: raise("A requested media component was not recognized; inspect #{name}-configure.log")

  if name == "ffmpeg" do
    # Generated diagnostic/preset metadata embeds the build prefix independently
    # of __FILE__. Keep the exact flags, with a neutral path, in distributed tools.
    header = Path.join(source, "config.h")
    File.write!(header, File.read!(header) |> String.replace(root, "riff-media"))
  end

  log = B.run!("make", ["-j#{jobs}"], cd: source, env: environment)
  File.write!(Path.join(root, name <> "-build.log"), log)
  log = B.run!("make", ["install"], cd: source, env: environment)
  File.write!(Path.join(root, name <> "-install.log"), log)
end

media = Path.join(root, "media")
File.mkdir!(media)

for executable <- ["ffmpeg", "ffprobe"] do
  target = Path.join(media, executable)
  File.cp!(Path.join(prefix, "bin/" <> executable), target)
  B.run!("/usr/bin/strip", ["-x", target])
  B.run!("/usr/bin/codesign", ["--force", "--sign", "-", target])
end

B.inspect_macos!(media)
B.run!(Path.join(media, "ffmpeg"), ["-hide_banner", "-encoders"])

B.json_write(Path.join(root, "build.json"), %{
  "components" => Map.take(B.components(), ["ffmpeg", "lame", "x264"]),
  "files" => B.records(media),
  "source_archives_required_for_distribution" => true
})

IO.puts("Media tools ready: #{media}")
