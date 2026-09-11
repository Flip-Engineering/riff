"""YuE2 controls and bounds from the pinned audio.cpp request contract."""
import math

# The model's context and audio token rate, independent of installed weights.
CONTEXT = 24576
TOKEN_RATE = 25
OPTIONS = {
    "semantic_top_p": ("Music probability mass", "Music", .95, 0, 1, False),
    "semantic_top_k": ("Music candidate pool", "Music", 100, 1, None, True),
    "semantic_repetition_penalty": ("Music repetition penalty", "Music", 1.2, 0, None, False),
    "semantic_penalty_window": ("Music repetition window", "Music", 50, 1, None, True),
    "semantic_min_tokens": ("Minimum music tokens", "Music", 200, 0, None, True),
    "abc_temperature": ("Melody temperature", "Melody", .7, 0, 5, False),
    "abc_top_p": ("Melody probability mass", "Melody", .9, 0, 1, False),
    "abc_top_k": ("Melody candidate pool", "Melody", 30, 1, None, True),
    "abc_repetition_penalty": ("Melody repetition penalty", "Melody", 1.005, 0, None, False),
    "abc_penalty_window": ("Melody repetition window", "Melody", 100, 1, None, True),
    "abc_min_tokens": ("Minimum melody tokens", "Melody", 32, 0, None, True),
    "abc_max_tokens": ("Maximum melody tokens", "Melody", 4096, 0, None, True),
}


def schema():
    return [{"key": key, "label": v[0], "group": v[1], "default": v[2],
             "min": v[3], "max": v[4], "integer": v[5],
             "exclusive_min": key.endswith("repetition_penalty")}
            for key, v in OPTIONS.items()]


def validate(values, max_seconds):
    if not isinstance(values, dict):
        raise ValueError("Refinement controls must be an object.")
    result = {}
    for key, raw in values.items():
        if key not in OPTIONS:
            raise ValueError(f"Unknown YuE2 refinement control: {key}")
        label, _, default, lower, upper, integer = OPTIONS[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{label} must be a number.")
        if not math.isfinite(raw) or raw < lower or (upper is not None and raw > upper):
            raise ValueError(f"{label} is outside the model's supported range.")
        if key.endswith("repetition_penalty") and raw == 0:
            raise ValueError(f"{label} must be positive.")
        if integer and int(raw) != raw:
            raise ValueError(f"{label} must be a whole number.")
        result[key] = int(raw) if integer else float(raw)
    if result.get("abc_min_tokens", 32) > result.get("abc_max_tokens", 4096):
        raise ValueError("The minimum melody length exceeds its maximum.")
    if result.get("semantic_min_tokens", 0) > math.floor(max_seconds * TOKEN_RATE):
        raise ValueError("Minimum music tokens exceed the chosen duration (25 tokens per second).")
    return result
