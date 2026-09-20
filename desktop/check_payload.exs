Code.require_file("support.exs", __DIR__)
alias Riff.Desktop.Build, as: B

{options, [], []} =
  OptionParser.parse(System.argv(),
    strict: [payload: :string, models: :string, image: :string, output: :string]
  )

payload = options |> Keyword.fetch!(:payload) |> Path.expand()
models = options |> Keyword.fetch!(:models) |> Path.expand()
image = options |> Keyword.fetch!(:image) |> Path.expand()
output = options |> Keyword.fetch!(:output) |> Path.expand()
if File.exists?(output), do: raise("Choose a fresh acceptance directory")
File.mkdir_p!(output)
manifest = B.json_read(Path.join(payload, "manifest.json"))
entry = fn name -> Path.join(payload, manifest["entries"][name]) end

environment = [
  {"PATH", Path.dirname(entry.("ffmpeg")) <> ":/usr/bin:/bin"},
  {"PYTHONHOME", nil},
  {"PYTHONPATH", nil},
  {"PYTHONNOUSERSITE", "1"},
  {"SSL_CERT_FILE", "/etc/ssl/cert.pem"}
]

runtime =
  B.run!(
    entry.("python"),
    [
      "-I",
      "-B",
      "-c",
      "import json,sqlite3,ssl,urllib.request; print(json.dumps(dict(sqlite=sqlite3.sqlite_version,tls=ssl.OPENSSL_VERSION,https=urllib.request.urlopen('https://openrouter.ai/api/v1/models',timeout=30).status)))"
    ],
    env: environment
  )
  |> :json.decode()

wave = Path.join(output, "native.wav")

native_log =
  B.run!(
    entry.("engine"),
    [
      "--task",
      "gen",
      "--family",
      "yue2",
      "--model",
      models,
      "--backend",
      "metal",
      "--threads",
      "4",
      "--lyrics",
      "[Instrumental]",
      "--session-option",
      "yue2.model_gguf=yue2-3b-q4_0.gguf",
      "--session-option",
      "yue2.vae_gguf=yue2-vae-f16.gguf",
      "--request-option",
      "cot=off",
      "--request-option",
      "cfg_scale=1",
      "--request-option",
      "semantic_max_tokens=200",
      "--request-option",
      "semantic_min_tokens=200",
      "--request-option",
      "num_inference_steps=2",
      "--request-option",
      "ode_method=ab2",
      "--request-option",
      "style=Plucked strings and warm woodwind, chamber instrumental",
      "--seed",
      "9271",
      "--out",
      wave,
      "--log",
      "--metrics"
    ],
    env: environment
  )

File.write!(Path.join(output, "native.log"), native_log)
mp3 = Path.join(output, "review.mp3")

B.run!(
  entry.("ffmpeg"),
  [
    "-hide_banner",
    "-loglevel",
    "error",
    "-i",
    wave,
    "-vn",
    "-c:a",
    "libmp3lame",
    "-b:a",
    "192k",
    mp3
  ],
  env: environment
)

frames = Path.join(output, "frames.pngstream")

File.open!(frames, [:write, :binary], fn file ->
  png = File.read!(image)
  for _ <- 1..60, do: IO.binwrite(file, png)
end)

video = Path.join(output, "export.mp4")

B.run!(
  entry.("ffmpeg"),
  [
    "-hide_banner",
    "-loglevel",
    "error",
    "-f",
    "image2pipe",
    "-framerate",
    "60",
    "-i",
    frames,
    "-t",
    "1",
    "-i",
    wave,
    "-map",
    "0:v:0",
    "-map",
    "1:a:0",
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-threads",
    "2",
    "-crf",
    "16",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "320k",
    "-movflags",
    "+faststart",
    video
  ],
  env: environment
)

probe = fn file ->
  B.run!(entry.("ffprobe"), ["-v", "error", "-show_streams", "-show_format", "-of", "json", file],
    env: environment
  )
  |> :json.decode()
end

audio_result = probe.(wave)
review_result = probe.(mp3)
video_result = probe.(video)
[visual] = Enum.filter(video_result["streams"], &(&1["codec_type"] == "video"))
[sound] = Enum.filter(video_result["streams"], &(&1["codec_type"] == "audio"))

unless visual["codec_name"] == "h264" and visual["nb_frames"] == "60" and
         visual["r_frame_rate"] == "60/1" and sound["codec_name"] == "aac",
       do: raise("Bundled MP4 encoder did not preserve the requested streams/frames")

encoded = Path.join(output, "frames.h264")
accelerated = Path.join(output, "accelerated.mp4")
B.run!(entry.("ffmpeg"), ["-v", "error", "-f", "image2pipe", "-framerate", "60",
  "-i", frames, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
  "-f", "h264", encoded], env: environment)
B.run!(entry.("ffmpeg"), ["-v", "error", "-f", "h264", "-framerate", "60",
  "-i", encoded, "-t", "1", "-i", wave, "-map", "0:v:0", "-map", "1:a:0",
  "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", accelerated], env: environment)
accelerated_result = probe.(accelerated)
[accelerated_visual] = Enum.filter(accelerated_result["streams"], &(&1["codec_type"] == "video"))
unless accelerated_visual["codec_name"] == "h264" and accelerated_visual["nb_frames"] == "60",
  do: raise("Bundled media tools did not preserve the H.264 transport frames")

result = %{
  runtime: runtime,
  version: manifest["version"],
  system_only_path: true,
  native: audio_result,
  review_audio: review_result,
  video: video_result,
  accelerated_video: accelerated_result,
  files: B.records(output)
}

B.json_write(Path.join(output, "validation.json"), result)

IO.puts(
  "Bundled native generation, TLS, MP3 review preparation and 60fps H.264/AAC export passed."
)
