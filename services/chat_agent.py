from openai import AsyncOpenAI

from utils.session_state import append_conversation_turn, get_conversation_history


async def run_chat(
    client: AsyncOpenAI,
    channel: str,
    contact_id: str,
    system_prompt: str,
    llm_model: str,
    temperature: float,
    user_text: str,
) -> str:
    """Run one turn of the text-in/text-out AI agent for a given messaging
    channel + contact, using that contact's rolling conversation memory."""
    history = get_conversation_history(channel, contact_id)
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_text}]

    completion = await client.chat.completions.create(
        model=llm_model,
        temperature=temperature,
        messages=messages,
    )
    reply = completion.choices[0].message.content or ""

    append_conversation_turn(channel, contact_id, "user", user_text)
    append_conversation_turn(channel, contact_id, "assistant", reply)
    return reply
