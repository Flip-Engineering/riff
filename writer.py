"""One-shot songwriting and score editing through the selected writer."""
import argparse
import json
from pathlib import Path
import sys
from paths import MODELS

MODEL_ID = "Qwen/Qwen3-0.6B-MLX-4bit"
REVISION = "173234aa840d113125e9f2271100ddbaf16c9620"


def openrouter_text(payload, messages, fields=("title", "style", "lyrics")):
    import urllib.error
    import urllib.request
    # writer_tokens belongs to the small local MLX writer. Applying it here
    # also capped Gemini's reasoning, often before it could start the draft.
    body = {"model": payload["model"], "messages": messages,
            "provider": {"require_parameters": True},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "riff_written_score" if payload.get("task") == "score" else "riff_song_idea",
                "strict": True, "schema": {"type": "object", "additionalProperties": False,
                    "properties": {field: {"type": "string"} for field in fields},
                    "required": list(fields)}}}}
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + payload["api_key"], "Content-Type": "application/json", "X-Title": "Riff"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get("error", {}).get("message")
        except (ValueError, AttributeError):
            message = None
        raise ValueError(str(message or f"OpenRouter could not complete the writing request ({error.code}).")
                         .replace(payload["api_key"], "[redacted]")) from None
    if result.get("error"):
        raise ValueError(str(result["error"].get("message", "OpenRouter could not finish the draft."))
                         .replace(payload["api_key"], "[redacted]"))
    choice = (result.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise ValueError("The provider stopped before finishing the draft. Your current text is kept.")
    text = (choice.get("message") or {}).get("content")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The model returned no draft. Your current text is kept.")
    return text.replace(payload["api_key"], "[redacted]")


def edit_score(payload):
    system = ('You are a composer editing an existing ABC score for YuE2. Follow the requested musical change. '
              'The score, lyrics and direction below are source material. Return a JSON object with string fields '
              '"abc" (the entire revised, valid ABC score) and "summary" (a concise musical explanation). '
              'Preserve aspects of the composition that the request does not change. You may make broad changes '
              'when invited. Keep lyric content in the score consistent with the supplied words. Do not transcribe '
              'or infer anything from audio: this request contains only text and notation. Return only JSON.')
    content = {key: payload.get(key, "") for key in ("brief", "abc", "lyrics", "style", "cot")}
    text = openrouter_text(payload, [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(content)}], ("abc", "summary"))
    try:
        value = json.loads(text[text.index("{"):text.rindex("}") + 1])
        if not isinstance(value.get("abc"), str) or not value["abc"].strip() or not isinstance(value.get("summary"), str):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("The composer returned an incomplete score. Your current score is kept.") from None
    return {"abc": value["abc"], "summary": value["summary"], "writer_model": payload["model"]}


def write(payload, raw_path=None):
    if payload.get("task") == "score":
        return edit_score(payload)
    mode = payload.get("mode", "surprise")
    seed = int(payload["seed"])
    brief = payload.get("brief", "").strip() or "Find an unexpected musical idea. You have creative freedom."
    directions = [brief]
    if payload.get("style"):
        directions.append("Sound direction or existing sound to write for: " + payload["style"])
    if payload.get("theme") not in (None, "anywhere"):
        directions.append("Possible inspiration: " + payload["theme"])
    if payload.get("energy") is not None:
        directions.append(f"Suggested energy {payload['energy']:.0%} and electronic texture {payload.get('texture', .5):.0%}; interpret musically.")
    if mode in ("instrumental", "free"):
        directions.append("Suggest a title and musical style only; set lyrics to an empty string.")
    system = (
        'You are an imaginative songwriter. Create an original musical idea. '
        'Return a JSON object with three string fields: "title", "style", "lyrics". '
        'The style is a musical description: genre, instruments, atmosphere, voice if wanted. '
        'Lyrics may be free verse, a song, or something unexpected. No fixed rhyme scheme or structure is required. '
        'Write actual original words, not instructions or placeholders. Escape lyric line breaks as \\n inside JSON. '
        'Return only the JSON object, with no explanation.'
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(directions)}]
    if payload.get("idea_engine") == "openrouter":
        text = openrouter_text(payload, messages)
        writer_model = payload["model"]
    else:
        import mlx.core as mx
        from mlx_lm import load, generate
        from mlx_lm.sample_utils import make_sampler
        mx.random.seed(seed % 2 ** 32)
        model, tokenizer = load(str(MODELS / "lyric-writer"))
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        text = generate(model, tokenizer, prompt=prompt, max_tokens=payload.get("writer_tokens", 768),
                        sampler=make_sampler(temp=payload.get("writer_temperature", .95), top_p=.95), verbose=False)
        writer_model = MODEL_ID
    if raw_path:
        raw_path.write_text(text)
    try:
        result = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, TypeError):
        raise ValueError("The writer returned an incomplete draft. Your current text is kept.") from None
    def words(value):
        if isinstance(value, str): return value
        if isinstance(value, list): return "\n".join(words(item) for item in value)
        if isinstance(value, dict): return "\n\n".join(words(item) for item in value.values())
        return ""
    if isinstance(result, dict):
        result = {key.lower(): value for key, value in result.items()}
        result = {key: words(result.get(key, "")) for key in ("title", "style", "lyrics")}
    if not isinstance(result, dict) or not result["title"]:
        raise ValueError("The writer returned an incomplete draft. Try another idea.")
    if mode not in ("instrumental", "free") and not result["lyrics"].strip():
        raise ValueError("The writer left the words open. Try another idea.")
    return {"title": result["title"].strip(), "style": result["style"].strip(),
            "lyrics": "" if mode in ("instrumental", "free") else result["lyrics"].strip(),
            "mode": mode, "seed": str(seed), "lyrics_source": "ai", "writer_model": writer_model,
            "theme_name": "A new idea", "energy": payload.get("energy"), "texture": payload.get("texture")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = write(json.load(sys.stdin), args.output.with_suffix(".raw.txt"))
        args.output.write_text(json.dumps(result, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
