"""
Call cost estimation service.

Costs are estimates based on known public pricing and call metadata.
All amounts are in USD. Rates are loaded from .env at import time.

Model pricing env vars (per 1 M tokens):
  OPENAI_GPT5_NANO_INPUT_PER_1M / _OUTPUT_PER_1M   (gpt-5-nano)
  OPENAI_GPT4O_MINI_INPUT_PER_1M / _OUTPUT_PER_1M  (gpt-4o-mini)
  OPENAI_GPT5_MINI_INPUT_PER_1M / _OUTPUT_PER_1M   (gpt-5-mini)
  OPENAI_GPT5_INPUT_PER_1M / _OUTPUT_PER_1M         (gpt-5)
  OPENAI_GPT4O_INPUT_PER_1M / _OUTPUT_PER_1M        (gpt-4o)

Other env vars:
  SARVAM_STT_COST_PER_MINUTE    (default 0.006)
  PLIVO_PHONE_COST_PER_MINUTE   (default 0.003)
  PLATFORM_COST_PER_MINUTE      (default 0.000)
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()  # must run before the module-level _f() calls below


def _f(key: str, default: str) -> float:
    return float(os.getenv(key, default))


# ── OpenAI pricing table (input/output per 1 M tokens) ───────────────────────
# Order matters for prefix matching — more specific entries first.

_LLM_PRICING: list[tuple[str, float, float]] = [
    # (model-prefix,              input/1M,                       output/1M)
    ("gpt-5-nano",  _f("OPENAI_GPT5_NANO_INPUT_PER_1M",  "0.050"), _f("OPENAI_GPT5_NANO_OUTPUT_PER_1M",  "0.400")),
    ("gpt-4o-mini", _f("OPENAI_GPT4O_MINI_INPUT_PER_1M", "0.150"), _f("OPENAI_GPT4O_MINI_OUTPUT_PER_1M", "0.600")),
    ("gpt-5-mini",  _f("OPENAI_GPT5_MINI_INPUT_PER_1M",  "0.250"), _f("OPENAI_GPT5_MINI_OUTPUT_PER_1M",  "2.000")),
    ("gpt-5",       _f("OPENAI_GPT5_INPUT_PER_1M",       "1.250"), _f("OPENAI_GPT5_OUTPUT_PER_1M",       "10.000")),
    ("gpt-4o",      _f("OPENAI_GPT4O_INPUT_PER_1M",      "2.500"), _f("OPENAI_GPT4O_OUTPUT_PER_1M",      "10.000")),
    # Legacy / fallback entries
    ("gpt-4",       30.0, 60.0),
    ("gpt-3.5",      0.5,  1.5),
]

_SARVAM_PER_MIN:   float = _f("SARVAM_STT_COST_PER_MINUTE",  "0.006")
_PLIVO_PER_MIN:    float = _f("PLIVO_PHONE_COST_PER_MINUTE",  "0.003")
_PLATFORM_PER_MIN: float = _f("PLATFORM_COST_PER_MINUTE",     "0.000")


def _llm_rates(model: str) -> tuple[float, float]:
    """Return (input_per_1M, output_per_1M) for the given model string."""
    for prefix, inp, out in _LLM_PRICING:
        if model.startswith(prefix):
            return inp, out
    # Unknown model — use gpt-4o-mini as safe default
    return _LLM_PRICING[1][1], _LLM_PRICING[1][2]


def calculate_cost(
    duration_seconds: int,
    chat_messages: list[dict],
    llm_model: str,
) -> dict:
    """
    Returns a cost breakdown dict:
    {
        "llm_usd":      float,
        "stt_usd":      float,
        "phone_usd":    float,
        "platform_usd": float,
        "total_usd":    float,
        "tokens":       {"input": int, "output": int},
        "model":        str,
        "duration_min": float,
    }
    """
    # ── Token estimation ──────────────────────────────────────────────────────
    # Conservative ratio of 3.5 chars/token to account for multilingual content
    # (Hindi, regional languages) which tokenise more densely than English.
    CHARS_PER_TOKEN = 3.5

    user_chars = sum(
        len(m.get("content") or "")
        for m in chat_messages
        if m.get("role") == "user"
    )
    assistant_chars = sum(
        len(m.get("content") or "")
        for m in chat_messages
        if m.get("role") == "assistant"
    )

    input_tokens  = max(int(user_chars / CHARS_PER_TOKEN), 1)
    output_tokens = max(int(assistant_chars / CHARS_PER_TOKEN), 1)

    # ── LLM cost ──────────────────────────────────────────────────────────────
    inp_rate, out_rate = _llm_rates(llm_model)
    llm_usd = (
        input_tokens  * inp_rate / 1_000_000 +
        output_tokens * out_rate / 1_000_000
    )

    # ── Duration-based costs ──────────────────────────────────────────────────
    duration_min = duration_seconds / 60.0
    stt_usd      = duration_min * _SARVAM_PER_MIN
    phone_usd    = duration_min * _PLIVO_PER_MIN
    platform_usd = duration_min * _PLATFORM_PER_MIN

    total_usd = llm_usd + stt_usd + phone_usd + platform_usd

    return {
        "llm_usd":          round(llm_usd,      6),
        "stt_usd":          round(stt_usd,      6),
        "phone_usd":        round(phone_usd,    6),
        "platform_usd":     round(platform_usd, 6),
        "total_usd":        round(total_usd,    6),
        "tokens": {
            "input":  input_tokens,
            "output": output_tokens,
        },
        "rates": {
            "llm_input_per_1m":  inp_rate,
            "llm_output_per_1m": out_rate,
            "stt_per_min":       _SARVAM_PER_MIN,
            "phone_per_min":     _PLIVO_PER_MIN,
            "platform_per_min":  _PLATFORM_PER_MIN,
        },
        "model":        llm_model,
        "duration_min": round(duration_min, 4),
    }


def cost_to_json(breakdown: dict) -> str:
    return json.dumps(breakdown)
