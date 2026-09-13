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

from review_recipe import FIELDS, generation_context, recommended_generation, response_schema, symbolic_context


def listen(settings):
    source = settings["recipe"]
    policy = "Riff keeps the artist's supplied lyrics in the recommended generation. Focus your changes on the music; generation.lyrics may be empty because Riff restores the original words." if settings["keep_lyrics"] else "The lyrics may be revised or translated when it serves the artist's request. Put the actual new words in generation.lyrics."
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
        + policy + " Return one complete recommended generation, using the response schema: "
        "notes, summary, and generation. The generation object goes directly into Riff's "
        "music generator. Supply actual musical direction, words, ABC notation, and settings, "
        "rather than instructions for someone else to implement. Start with the prior inputs "
        "and native symbolic plan; preserve what works and make purposeful changes to what does not. "
        "The supplied and generated ABC are YuE2 composition data, not an audio transcription. "
        "When an arrangement includes several takes, its source inputs, source scores, and "
        "edit timeline describe how those performances were combined. Consider that context "
        "when judging the mix and recommending the next generation. "
        "A score describes the plan; compare it to what you actually hear, and remember the "
        "recording may be a short excerpt of that plan. Use the existing score as a starting "
        "point when useful. To retain an exact saved score, select its ID from available_scores "
        "in score_source and leave abc empty. To revise notation, supply the complete abc and "
        "leave score_source empty. Displayed ABC is an editing view and may not preserve every "
        "native token. Leave both empty to let YuE2 compose. An arrangement keeps its individual "
        "source score references; its combined notation is not one exact saved score. "
        "Supplied ABC or a saved score requires cot=melody or full. Preserve the artist's creative intent; "
        "language, genre, structure, and instruments remain open to their direction. Carry over "
        "generation settings unless a change serves this iteration, and keep a short study "
        "within the requested preview scope. Preserve any useful sampling overrides. "
        "You can recommend music, a performance saved for later rendering, a score-only composition, or a re-render of "
        "the supplied saved performance when performance_track_id is available. These use "
        "Riff's same generation queue and editable inputs. Reuse retains the performance's "
        "phrasing and duration; adjust acoustic direction, solver detail or seed. Music sampling "
        "controls are skipped during reuse. If the words, structure or phrasing need to change, "
        "choose a fresh performance. Supply the complete actual score when needed. "
        "YuE2's native context is 24576 tokens and "
        "music uses 25 tokens per second, with the text and score also needing context space. "
        "Do not promise that prompts or notation will force an exact performance." + timing + "\n\n"
        "ARTIST'S FOCUS\n" + (settings["focus"] or "Develop the strongest version of this song.") +
        "\n\nPRIOR GENERATION INPUTS\n" + json.dumps(
            generation_context(source),
            ensure_ascii=False, indent=2) +
        "\n\nSYMBOLIC REPRESENTATION\n" + json.dumps(symbolic_context(source), ensure_ascii=False, indent=2)
    )
    if not shutil.which("ffmpeg"):
        raise ValueError("Install FFmpeg with brew install ffmpeg to prepare recordings for review.")
    with tempfile.TemporaryDirectory(prefix="riff-listen-") as folder:
        encoded = Path(folder) / "recording.mp3"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", settings["audio"],
                        "-vn", "-c:a", "libmp3lame", "-b:a", "192k", str(encoded)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        audio = base64.b64encode(encoded.read_bytes()).decode()
    payload = {"model": settings["model"],
               "provider": {"require_parameters": True},
               "response_format": {"type": "json_schema", "json_schema": {
                   "name": "riff_recommended_take", "strict": True,
                   "schema": response_schema(source, settings["keep_lyrics"])}},
               "messages": [{"role": "user", "content": [
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
    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("The recommendation was cut off before its recipe was complete. Review again to retry.")
    text = choice["message"].get("content") or ""
    if not text:
        raise ValueError("The model returned an empty review. Review again to retry.")
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("The review did not return a structured generation recipe. Review again to retry.") from None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("notes"), str) or not isinstance(parsed.get("summary"), str):
        raise ValueError("The review returned an incomplete recommendation. Review again to retry.")
    generation = recommended_generation(parsed.get("generation"), source, settings["keep_lyrics"])
    return {"notes": parsed["notes"], "summary": parsed["summary"],
            "generation": {key: generation[key] for key in FIELDS}, "usage": result.get("usage", {}),
            "model": result.get("model", settings["model"]), "response_id": result.get("id", "")}


if __name__ == "__main__":
    settings = json.load(sys.stdin)
    try:
        result = listen(settings)
    except Exception as error:
        result = {"error": str(error).replace(settings["api_key"], "[redacted]")}
    Path(sys.argv[1]).write_text(json.dumps(result).replace(settings["api_key"], "[redacted]") + "\n")
