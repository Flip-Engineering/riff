"""One-shot songwriting and score editing through the selected writer."""
import argparse
import json
from pathlib import Path
import sys
from platform_support import writer_settings

MODEL_ID = "Qwen/Qwen3-0.6B-MLX-4bit"
REVISION = "173234aa840d113125e9f2271100ddbaf16c9620"


def openrouter_text(payload, messages, fields=("title", "style", "lyrics"), schema=None):
    import urllib.error
    import urllib.request
    # writer_tokens belongs to the small local MLX writer. Applying it here
    # also capped Gemini's reasoning, often before it could start the draft.
    body = {"model": payload["model"], "messages": messages,
            "provider": {"require_parameters": True},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "riff_written_score" if payload.get("task") == "score" else "riff_song_idea",
                "strict": True, "schema": schema or {"type": "object", "additionalProperties": False,
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
    content = {**writing_context(payload), **{key: payload.get(key, "") for key in ("brief", "abc", "lyrics", "style", "cot")}}
    text = openrouter_text(payload, [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(content)}], ("abc", "summary"))
    try:
        value = json.loads(text[text.index("{"):text.rindex("}") + 1])
        if not isinstance(value.get("abc"), str) or not value["abc"].strip() or not isinstance(value.get("summary"), str):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("The composer returned an incomplete score. Your current score is kept.") from None
    return {"abc": value["abc"], "score_source": "", "summary": value["summary"], "writer_model": payload["model"]}


def writing_context(payload):
    from review_recipe import generation_context, symbolic_context
    return {"inputs": generation_context(payload), "symbolic": symbolic_context(payload),
            "request": {key: payload[key] for key in ("brief", "write_scope", "hold_words", "hold_sound", "queued_generation") if key in payload},
            "reference": payload.get("reference", {})}


def writing_schema(payload):
    from review_recipe import response_schema
    generation = response_schema(payload, False)["properties"]["generation"]
    return {"type": "object", "additionalProperties": False, "required": ["summary", "generation"],
            "properties": {"summary": {"type": "string", "description": "Brief musical explanation of this written direction."},
                           "generation": generation}}


def written_generation(value, payload, partial=False):
    from review_recipe import FIELDS, validate_generation
    from studio_core import validate_recipe
    source = {**payload, **validate_recipe(payload)}
    if not isinstance(value, dict):
        raise ValueError("The writer returned an incomplete draft. Your current draft is kept.")
    proposal = value.get("generation")
    if partial:
        # Small local writers and older saved responses may omit unchanged
        # fields. Every supported field still participates in the same recipe.
        proposal = proposal if proposal is not None else {key: item for key, item in value.items() if key in FIELDS}
        if isinstance(proposal, dict):
            # A changed score input replaces the inherited alternative. Keep
            # both explicit inputs for the common validator to check.
            if "abc" in proposal and "score_source" not in proposal:
                proposal = {**proposal, "score_source": ""}
            elif proposal.get("score_source") and "abc" not in proposal:
                proposal = {**proposal, "abc": ""}
            proposal = {**{key: source[key] for key in FIELDS}, **proposal}
    if not isinstance(proposal, dict):
        raise ValueError("The writer returned no generation recipe. Your current draft is kept.")
    proposal = dict(proposal)
    scope = payload.get("write_scope", "all")
    keep_words = scope == "sound" or (scope == "all" and payload.get("hold_words"))
    keep_sound = scope == "words" or (scope == "all" and payload.get("hold_sound"))
    if keep_words:
        proposal.update(lyrics=source["lyrics"], mode=source["mode"])
    if scope == "sound":
        proposal["title"] = source["title"]
    if keep_sound:
        proposal["style"] = source["style"]
    if source["mode"] in ("instrumental", "free"):
        proposal.update(mode=source["mode"], lyrics="")
    if payload.get("queued_generation"):
        # The user already queued a particular output and duration. Writing
        # fills that request; standalone writing can propose a different scope.
        proposal.update(max_seconds=source["max_seconds"], render_mode=source["render_mode"])
        if not source.get("title_auto"):
            proposal["title"] = source["title"]
    generation = validate_generation(proposal, source)
    if generation["mode"] not in ("free", "instrumental") and not generation["lyrics"].strip():
        raise ValueError("The writer left the words open. Your current draft is kept.")
    generation["lyrics_source"] = "ai" if generation["lyrics"] else "none"
    return generation


def write(payload, raw_path=None):
    if payload.get("task") == "score":
        return edit_score(payload)
    seed = int(payload["seed"])
    brief = payload.get("brief", "").strip() or "Find an unexpected musical idea. You have creative freedom."
    system = (
        'You are an imaginative composer working in Riff with YuE2. Develop the artist\'s idea into '
        'an actual editable generation recipe. Return JSON with summary and generation, following the schema. '
        'Consider every supplied input, the symbolic representation, and any reference inputs and listening notes. '
        'Use actual musical direction, original words and valid complete ABC when useful, with purposeful generation '
        'settings, not abstract instructions. Melody, chord symbols, key, meter, tempo, multiple voices, dynamics '
        'and arrangement can be expressed in ABC; notation guides the music but does not guarantee exact performance. '
        'To retain an exact native score, select its ID from symbolic.available_scores in score_source and leave abc empty. '
        'To revise the notation, provide complete abc and leave score_source empty. Displayed ABC is an editing view '
        'and may not represent every saved native token. Leave both empty for a new composition: cot melody plans '
        'a melody, full plans melody and chords. An arrangement retains its individual source score references; '
        'do not treat their notation as one exact combined score. '
        'The current cot off is the artist\'s Direct choice: keep both score inputs empty unless the request invites changing planning. '
        'Planning sampling controls matter when YuE2 composes, while semantic controls shape fresh music codes; '
        'acoustic steps and solver shape the sound. Preserve useful settings and overrides; change them when it serves '
        'the request. A schema field being available does not mean it must be changed or overridden. '
        'Lyrics may be free verse, a song, or something unexpected; genre, language, rhyme and structure are open. '
        'When supplied, the sound compass energy ranges from calm (0) to driving (1), and texture from acoustic (0) '
        'to electronic (1), with blended textures between. Express that direction musically within the artist\'s idea; '
        'these are not fixed genres or tempo requirements. An omitted position imposes no direction. '
        'Write complete original words, without placeholders. Instrumental and Free play leave lyrics empty. '
        'Respect hold_words and hold_sound. write_scope words asks for new words in the existing sound; sound asks '
        'for a musical variation retaining the words. A direct request for new words or sound overrides that particular hold. '
        'Use the entire recipe to keep melody, harmony, lyrics and performance coherent, even for a focused revision. '
        'Retain the chosen duration and output unless the artist asks to change them; queued_generation must fit '
        'the already requested duration and output. Write enough music for that scope and shape an ending. '
        'A supplied saved performance may be re-rendered with new acoustic conditioning, detail or seed; its codes '
        'retain phrasing and duration and skip music sampling. Choose fresh codes for new words or composition. '
        'An offered symbolic.available_acoustics source holds completed synthesis. Select acoustic_source '
        'with its listed musical inputs unchanged to refine only decoding; empty decoder controls restore '
        'captured settings. For new music, leave acoustic_source empty and decoder empty. Output sound '
        'saves completed synthesis for later decoding without producing audio yet. '
        'The model context is 24576 tokens including text, notation and 25 music tokens per second. '
        'Reference notes describe earlier audio; this writing request contains text and notation, so do not claim '
        'to have heard a recording. Return only the JSON object.'
    )
    schema = writing_schema(payload)
    context = writing_context(payload)
    context["request"]["brief"] = brief
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    if payload.get("idea_engine") == "openrouter":
        text = openrouter_text(payload, messages, schema=schema)
        writer_model = payload["model"]
    else:
        import mlx.core as mx
        from mlx_lm import load, generate
        from mlx_lm.sample_utils import make_sampler
        mx.random.seed(seed % 2 ** 32)
        model, tokenizer = load(str(writer_settings()["model"]))
        messages[0]["content"] += (" You may omit unchanged generation fields to spend the writing budget on the music."
                                   " Response schema: " + json.dumps(schema))
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
    generation = written_generation(result, payload, partial=payload.get("idea_engine") != "openrouter")
    summary = result.get("summary", "A new idea")
    if not isinstance(summary, str):
        raise ValueError("The writer returned an unreadable explanation. Your current draft is kept.")
    generation.update(writer_model=writer_model, writer_summary=summary)
    from review_recipe import FIELDS
    return {**generation, "generation": {key: generation[key] for key in FIELDS}, "summary": summary, "theme_name": summary}


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
