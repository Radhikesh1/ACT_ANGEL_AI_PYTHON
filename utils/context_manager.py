MAX_MESSAGES = 8

def compress_context(context, system_prompt):

    if len(context.messages) <= MAX_MESSAGES:
        return

    recent_messages = context.messages[-MAX_MESSAGES:]

    summary_parts = []

    for msg in context.messages[:-MAX_MESSAGES]:

        if msg["role"] == "user":

            summary_parts.append(msg["content"])

    summary_text = " | ".join(summary_parts[:10])

    context.messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "system",
            "content":
                f"Conversation summary: {summary_text}"
        }
    ] + recent_messages