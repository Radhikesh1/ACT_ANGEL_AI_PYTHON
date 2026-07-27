"""
Auto-generate FAQ items, intent triggers, and filler messages from a system prompt.
Called once when an assistant is published; results are stored in the DB.
"""
import json
import os

import httpx
from loguru import logger

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_PROMPT = """\
You are a configuration assistant for a voice AI product.

Given the voice assistant system prompt below, extract and generate:

1. faq_items — Up to 10 FAQ pairs callers are likely to ask, based on what the business/service description implies. Each item: {"question": "...", "answer": "..."}.

2. intent_triggers — Intent groups where caller speech should trigger a thinking filler while the system processes something (e.g., appointment booking, pricing lookup, transfers). Each item: {"name": "...", "keywords": ["...", ...], "action": "..."}.
   - Use action = "appointment" for booking/scheduling flows.
   - Use action = "transfer" for requests to speak with a human.
   - Use a short descriptive action name for any other intent.

3. filler_messages — Natural hold/thinking phrases suited to this assistant's tone, in three languages. Keys: "en", "hi", "bn". Each should be a single short sentence.

Respond ONLY with valid JSON in exactly this structure — no markdown, no explanation:
{
  "faq_items": [{"question": "...", "answer": "..."}],
  "intent_triggers": [{"name": "...", "keywords": ["..."], "action": "..."}],
  "filler_messages": {"en": "...", "hi": "...", "bn": "..."}
}

System prompt:
"""


async def generate_assistant_config(system_prompt: str) -> dict:
    """Return generated FAQ, intent triggers, and filler messages for the given prompt."""
    if not _OPENAI_API_KEY:
        logger.warning("[ConfigGenerator] OPENAI_API_KEY not set — skipping auto-generation")
        return {}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {_OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": _PROMPT + system_prompt}],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
            logger.info(
                f"[ConfigGenerator] Generated {len(result.get('faq_items', []))} FAQ items, "
                f"{len(result.get('intent_triggers', []))} intent triggers"
            )
            return result
    except Exception as exc:
        logger.error(f"[ConfigGenerator] Generation failed: {exc}")
        return {}
