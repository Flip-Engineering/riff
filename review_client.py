"""One direct OpenRouter listening request; credentials arrive over stdin."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

from review_recipe import FIELDS, MP3_BITRATE, generation_context, recommended_generation, response_schema, symbolic_context


class ProviderResponseError(ValueError):
    def __init__(self, message, response, sent):
        super().__init__(message)
        self.response, self.sent = response, sent


def encode_mp3(source, folder, name, subject="the recording"):
    encoded = Path(folder) / name
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-i", str(source),
                        "-vn", "-c:a", "libmp3lame", "-b:a", str(MP3_BITRATE), str(encoded)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        # The command line names a local file; keep it out of the saved error.
        raise ValueError(f"FFmpeg could not prepare {subject} for review.") from None
    data = encoded.read_bytes()
    return {"data": base64.b64encode(data).decode(), "format": "mp3",
            "sent": {"format": "mp3", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}


def reference_prompt(references):
    """Describe supplied references by identity and provenance; never by file location."""
    described = [{"id": item["id"], "label": item["label"], "source_link": item.get("link", ""),
                  "source_note": item.get("note", ""), "duration_seconds": item["duration"],
                  "format": item["format"]} for item in references]
    return (
        "\n\nREFERENCE RECORDINGS\nThe first audio is the generated take under review. The audio that follows it "
        "is a reference recording the artist chose for calibration, in the order listed here. A reference is "
        "not Riff output and is not the take being revised; it shows a direction the artist is comparing against. "
        "Compare qualities such as groove, instrumentation, arrangement, vocal approach, mix and pacing, and say "
        "what the generated take would need to move toward them. Do not copy a reference's melody or words. "
        "Attribute every observation in observations to generated_take or to the reference's id, and give at least "
        "one observation for each. Timestamps in an observation are relative to that observation's own audio. "
        "The recommended generation is still one executable Riff recipe for the generated take.\n"
        + json.dumps(described, ensure_ascii=False, indent=2))


def listen(settings):
    source = settings["recipe"]
    references = settings.get("references") or []
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
        "within the requested preview scope. Retain the source seed exactly; improve the musical "
        "inputs and synthesis settings while keeping the same random starting point. The artist "
        "can change the seed afterward in the studio. Preserve any useful sampling overrides. "
        "You can recommend music, a performance saved for later rendering, a score-only composition, or a re-render of "
        "the supplied saved performance when performance_track_id is available. These use "
        "Riff's same generation queue and editable inputs. Reuse retains the performance's "
        "phrasing and duration; adjust acoustic direction or solver detail while retaining the seed. Music sampling "
        "controls are skipped during reuse. If the words, structure or phrasing need to change, "
        "choose a fresh performance. Supply the complete actual score when needed. "
        "Available acoustic sources are completed sound synthesis. Select an offered acoustic_source "
        "to decode it again, copying its listed musical inputs exactly and changing only decoder controls "
        "or title. Empty decoder controls restore captured defaults. This skips composition, performance "
        "and synthesis; leave acoustic_source empty and decoder empty for new music. Output sound saves "
        "completed synthesis for later decoding. "
        "YuE2's native context is 24576 tokens and "
        "music uses 25 tokens per second, with the text and score also needing context space. "
        "Do not promise that prompts or notation will force an exact performance." + timing + "\n\n"
        "ARTIST'S FOCUS\n" + (settings["focus"] or "Develop the strongest version of this song.") +
        "\n\nPRIOR GENERATION INPUTS\n" + json.dumps(
            generation_context(source),
            ensure_ascii=False, indent=2) +
        "\n\nSYMBOLIC REPRESENTATION\n" + json.dumps(symbolic_context(source), ensure_ascii=False, indent=2)
        + (reference_prompt(references) if references else "")
    )
    if settings.get("primary_kind") == "edited_master":
        prompt += ("\n\nPRIMARY EDITED MASTER\nThe first audio is the artist's edited master under review, "
                   "not an original generated take or a comparison clip. Review this actual audio. "
                   "The artist supplied the following source recipe and edit context; treat these as context, "
                   "not measurements of what is audible.\n" + json.dumps(
                       {"source_recipe": source, "edit_context": settings.get("edit_context", {})}, ensure_ascii=False))
    if not shutil.which("ffmpeg"):
        raise ValueError("Install FFmpeg with brew install ffmpeg to prepare recordings for review.")
    parts, sent = [{"type": "text", "text": prompt}], []
    def audio(source, folder, name, owner, subject="the recording"):
        # Providers take MP3 or WAV; converting is transcoding, and what was sent is recorded.
        encoded = encode_mp3(source, folder, name, subject)
        sent.append({"source": owner, **encoded.pop("sent")})
        return {"type": "input_audio", "input_audio": encoded}
    with tempfile.TemporaryDirectory(prefix="riff-listen-") as folder:
        parts.append(audio(settings["audio"], folder, "recording.mp3", settings.get("primary_kind", "generated_take")))
        for number, item in enumerate(references, 1):
            parts.append({"type": "text", "text": f"Reference audio {number}: id {item['id']}, labelled {item['label']!r}."})
            parts.append(audio(item["path"], folder, f"reference-{number}.mp3", item["id"], f"the reference {item['label']!r}"))
    payload = {"model": settings["model"],
               "provider": {"require_parameters": True},
               "response_format": {"type": "json_schema", "json_schema": {
                   "name": "riff_recommended_take", "strict": True,
                   "schema": response_schema(source, settings["keep_lyrics"], preserve_seed=True,
                                             references=references)}},
               "messages": [{"role": "user", "content": parts}]}
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                     data=json.dumps(payload).encode(),
                                     headers={"Authorization": "Bearer " + settings["api_key"],
                                              "Content-Type": "application/json", "X-Title": "Riff producer review"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = response.read()
            try:
                result = json.loads(body)
            except ValueError:
                if settings.get("primary_kind") == "edited_master":
                    raise ProviderResponseError("The provider returned an unreadable response.",
                                                {"body": body.decode("utf-8", errors="replace")}, sent) from None
                raise
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            message = json.loads(body).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        message = f"OpenRouter returned HTTP {error.code}. {message}"
        if settings.get("primary_kind") == "edited_master":
            raise ProviderResponseError(message, {"http_status": error.code,
                                        "body": body.decode("utf-8", errors="replace")}, sent) from None
        raise ValueError(message) from None
    try:
        return interpret_response(result, settings, source, references, sent)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        if settings.get("primary_kind") == "edited_master":
            raise ProviderResponseError(str(error), result, sent) from None
        raise


def interpret_response(result, settings, source, references, sent):
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
    review = {"notes": parsed["notes"], "summary": parsed["summary"],
              "generation": {key: generation[key] for key in FIELDS}, "usage": result.get("usage", {}),
              "model": result.get("model", settings["model"]), "response_id": result.get("id", "")}
    if references:
        review["observations"] = parsed.get("observations")
        review["sent_audio"] = sent
    if settings.get("primary_kind") == "edited_master":
        review["sent_audio"] = sent
        review["provider_response"] = result
    return review


if __name__ == "__main__":
    settings = json.load(sys.stdin)
    try:
        result = listen(settings)
    except Exception as error:
        result = {"error": str(error).replace(settings["api_key"], "[redacted]")}
        if isinstance(error, ProviderResponseError):
            result.update(provider_response=error.response, sent_audio=error.sent)
    Path(sys.argv[1]).write_text(json.dumps(result).replace(settings["api_key"], "[redacted]") + "\n")
