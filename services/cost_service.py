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
  OPENAI_GPT4_INPUT_PER_1M / _OUTPUT_PER_1M         (gpt-4, legacy)
  OPENAI_GPT35_INPUT_PER_1M / _OUTPUT_PER_1M        (gpt-3.5, legacy)

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
    # Legacy entries — no assistant should be configured with these anymore,
    # but the rates stay env-configurable for consistency with the rest of
    # this table rather than being frozen in code.
    ("gpt-4",       _f("OPENAI_GPT4_INPUT_PER_1M",       "30.000"), _f("OPENAI_GPT4_OUTPUT_PER_1M",       "60.000")),
    ("gpt-3.5",     _f("OPENAI_GPT35_INPUT_PER_1M",       "0.500"), _f("OPENAI_GPT35_OUTPUT_PER_1M",       "1.500")),
]

_SARVAM_PER_MIN:   float = _f("SARVAM_STT_COST_PER_MINUTE",  "0.006")
_PLIVO_PER_MIN:    float = _f("PLIVO_PHONE_COST_PER_MINUTE",  "0.003")
_PLATFORM_PER_MIN: float = _f("PLATFORM_COST_PER_MINUTE",     "0.000")

# Flat per-minute fee charged instead of the real provider-cost formula when
# an org supplies its own Sarvam/OpenAI key — they pay that provider directly,
# so we charge a flat orchestration fee rather than double-billing usage.
_BYOK_STT_FLAT_PER_MIN: float = _f("BYOK_STT_FLAT_FEE_PER_MINUTE", "0.001")
_BYOK_LLM_FLAT_PER_MIN: float = _f("BYOK_LLM_FLAT_FEE_PER_MINUTE", "0.002")


def _llm_rates(model: str, llm_overrides: dict | None = None) -> tuple[float, float]:
    """Return (input_per_1M, output_per_1M) for the given model string.

    llm_overrides: optional dict keyed by the same model-prefix strings as
    _LLM_PRICING (e.g. WEB's active modelPricingVersions row, Global Settings
    > Model Pricing tab), each value {"inputPer1M": float, "outputPer1M": float}.
    A prefix present in the override wins over its env-configured default.
    """
    for prefix, inp, out in _LLM_PRICING:
        if model.startswith(prefix):
            override = (llm_overrides or {}).get(prefix)
            if override:
                return float(override.get("inputPer1M", inp)), float(override.get("outputPer1M", out))
            return inp, out
    # Unknown model — use gpt-4o-mini as safe default
    default_prefix, default_inp, default_out = _LLM_PRICING[1]
    override = (llm_overrides or {}).get(default_prefix)
    if override:
        return float(override.get("inputPer1M", default_inp)), float(override.get("outputPer1M", default_out))
    return default_inp, default_out


def calculate_cost(
    duration_seconds: int,
    chat_messages: list[dict],
    llm_model: str,
    used_own_sarvam: bool = False,
    used_own_openai: bool = False,
    byok_stt_rate: float | None = None,
    byok_llm_rate: float | None = None,
    model_pricing: dict | None = None,
) -> dict:
    """
    Returns a cost breakdown dict:
    {
        "llm_usd":      float,
        "stt_usd":      float,
        "phone_usd":    float,
        "platform_usd": float,
        "total_usd":    float,
        "used_own_sarvam": bool,
        "used_own_openai": bool,
        "tokens":       {"input": int, "output": int},
        "model":        str,
        "duration_min": float,
    }

    used_own_sarvam/used_own_openai: pass True when the org's own key was used
    for that provider on this call — that component is then charged at the
    flat BYOK rate instead of the real usage-based formula, since the org is
    already paying that provider directly.

    byok_stt_rate/byok_llm_rate: SAD-configurable overrides for the flat BYOK
    rate (per minute), read from voice_provider_settings by the caller. Pass
    None (the default) to fall back to the env-configured _BYOK_*_FLAT_PER_MIN.

    model_pricing: SAD-configurable override for the actual-cost basis (WEB's
    Global Settings > Model Pricing tab, versioned in `model_pricing_versions`
    — read fresh per-call by the caller). Pass None (the default) to fall back
    to the env-configured module-level rates below. Shape:
    {"llm": {"<model-prefix>": {"inputPer1M", "outputPer1M"}, ...},
     "sttPerMinute": float, "phonePerMinute": float, "platformPerMinute": float}
    — an "analytics" key, if present, is ignored here; that's WEB-only.
    """
    stt_flat_rate = byok_stt_rate if byok_stt_rate is not None else _BYOK_STT_FLAT_PER_MIN
    llm_flat_rate = byok_llm_rate if byok_llm_rate is not None else _BYOK_LLM_FLAT_PER_MIN

    llm_overrides = (model_pricing or {}).get("llm")
    sarvam_per_min = (
        float(model_pricing["sttPerMinute"])
        if model_pricing and model_pricing.get("sttPerMinute") is not None
        else _SARVAM_PER_MIN
    )
    plivo_per_min = (
        float(model_pricing["phonePerMinute"])
        if model_pricing and model_pricing.get("phonePerMinute") is not None
        else _PLIVO_PER_MIN
    )
    platform_per_min = (
        float(model_pricing["platformPerMinute"])
        if model_pricing and model_pricing.get("platformPerMinute") is not None
        else _PLATFORM_PER_MIN
    )
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

    duration_min = duration_seconds / 60.0

    # ── LLM cost ──────────────────────────────────────────────────────────────
    inp_rate, out_rate = _llm_rates(llm_model, llm_overrides)
    if used_own_openai:
        llm_usd = duration_min * llm_flat_rate
    else:
        llm_usd = (
            input_tokens  * inp_rate / 1_000_000 +
            output_tokens * out_rate / 1_000_000
        )

    # ── Duration-based costs ──────────────────────────────────────────────────
    stt_usd      = duration_min * (stt_flat_rate if used_own_sarvam else sarvam_per_min)
    phone_usd    = duration_min * plivo_per_min
    platform_usd = duration_min * platform_per_min

    total_usd = llm_usd + stt_usd + phone_usd + platform_usd

    return {
        "llm_usd":          round(llm_usd,      6),
        "stt_usd":          round(stt_usd,      6),
        "phone_usd":        round(phone_usd,    6),
        "platform_usd":     round(platform_usd, 6),
        "total_usd":        round(total_usd,    6),
        "used_own_sarvam":  used_own_sarvam,
        "used_own_openai":  used_own_openai,
        "tokens": {
            "input":  input_tokens,
            "output": output_tokens,
        },
        "rates": {
            "llm_input_per_1m":  inp_rate,
            "llm_output_per_1m": out_rate,
            "stt_per_min":       stt_flat_rate if used_own_sarvam else sarvam_per_min,
            "phone_per_min":     plivo_per_min,
            "platform_per_min":  platform_per_min,
        },
        "model":        llm_model,
        "duration_min": round(duration_min, 4),
    }


def cost_to_json(breakdown: dict) -> str:
    return json.dumps(breakdown)
