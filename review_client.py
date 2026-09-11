"""One direct OpenRouter listening request; credentials arrive over stdin."""
import base64
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import urllib.error
import urllib.request


def listen(settings):
    source = settings["recipe"]
    policy = "Keep every supplied lyric unchanged." if settings["keep_lyrics"] else "The lyrics may be revised when it serves the artist's request."
    timing = (f"\n\nRECORDING DURATION\n{settings['duration']:.2f} seconds. "
              "Use timestamps relative to the start of this supplied recording."
              if settings.get("duration") else "")
    prompt = (
        "Listen to this recording as a music producer. Review the actual performance against "
        "the musical direction and the artist's current focus. Give candid musical observations "
        "with useful timestamps: groove, vocal delivery, orchestration, emotional force, mix, "
        "transitions, development, and ending. Identify the strongest moments and the changes "
        "that would most improve the next take. Distinguish what is audible from what was "
        "requested, and be candid about uncertainty. Treat tempo and key in the brief as "
        "targets, not measurements; avoid unsupported numeric estimates. This is a musical "
        "review, not a transcription. "
        + policy + " Offer an editable musical direction for another take. Preserve the artist's "
        "creative intent; structure and genre are theirs to choose. Return a JSON object with "
        "notes (plain-text production notes), summary (the revision's musical intent), and "
        "revision (an object with suggested style and, when appropriate, title, lyrics or abc)." + timing + "\n\n"
        "ARTIST'S FOCUS\n" + (settings["focus"] or "Develop the strongest version of this song.") +
        "\n\nMUSICAL DIRECTION\n" + source.get("style", "") +
        "\n\nLYRIC SHEET\n" + source.get("lyrics", "") +
        "\n\nSUPPLIED SCORE\n" + source.get("abc", "")
    )
    if not shutil.which("ffmpeg"):
        raise ValueError("Install FFmpeg with brew install ffmpeg to prepare recordings for review.")
    with tempfile.TemporaryDirectory(prefix="riff-listen-") as folder:
        encoded = Path(folder) / "recording.mp3"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", settings["audio"],
                        "-vn", "-c:a", "libmp3lame", "-b:a", "192k", str(encoded)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        audio = base64.b64encode(encoded.read_bytes()).decode()
    payload = {"model": settings["model"], "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "input_audio", "input_audio": {"data": audio, "format": "mp3"}},
    ]}]}
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                     data=json.dumps(payload).encode(),
                                     headers={"Authorization": "Bearer " + settings["api_key"],
                                              "Content-Type": "application/json", "X-Title": "Riff producer review"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        raise ValueError(f"OpenRouter returned HTTP {error.code}. {message}") from None
    if result.get("error"):
        raise ValueError(result["error"].get("message", "OpenRouter could not finish this review."))
    text = result["choices"][0]["message"].get("content") or ""
    if not text:
        raise ValueError("The model returned an empty review. Try again or choose another model.")
    parsed = None
    decoder = json.JSONDecoder()
    for position, char in enumerate(text):
        if char == "{":
            try:
                candidate, _ = decoder.raw_decode(text[position:])
                if isinstance(candidate, dict) and isinstance(candidate.get("notes"), str):
                    parsed = candidate
                    break
            except ValueError:
                pass
    parsed = parsed or {"notes": text, "summary": "", "revision": {}}
    revision = parsed.get("revision") if isinstance(parsed.get("revision"), dict) else {}
    revision = {key: value for key, value in revision.items()
                if key in ("title", "style", "lyrics", "abc") and isinstance(value, str)}
    if settings["keep_lyrics"]:
        revision.pop("lyrics", None)
    return {"notes": parsed["notes"], "summary": str(parsed.get("summary", "")),
            "revision": revision, "usage": result.get("usage", {}),
            "model": result.get("model", settings["model"]), "response_id": result.get("id", "")}


if __name__ == "__main__":
    settings = json.load(sys.stdin)
    try:
        result = listen(settings)
    except Exception as error:
        result = {"error": str(error).replace(settings["api_key"], "[redacted]")}
    Path(sys.argv[1]).write_text(json.dumps(result).replace(settings["api_key"], "[redacted]") + "\n")
