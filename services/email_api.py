#!/opt/act-angel-imap-watcher/venv/bin/python3
"""
Act Angel AI - Email conversational agent (Hello@ActAngel.com).

Watches the mailbox via IMAP IDLE and, for each new reply, runs the full
conversational loop natively in THIS process - no n8n execution is spent per
message. Only real actions (log_qualification, capture_followup_time, etc.)
go through n8n, via the same shared /webhook/retell-functions endpoint that
Retell, Millis, and WhatsApp already use - that endpoint is the one proven
source of truth for tool logic across every channel, and reusing it here
avoids a second, drifting copy of that logic. Actions are rare relative to
messages, so this keeps per-message cost at zero n8n executions and
per-action cost at one, matching the ratio that already works well elsewhere
in this project.

IMAP reconnection: Gmail forcibly drops IDLE connections around the 29-30
minute mark and does not notify cleanly - the client library will not
reconnect on its own. This renews IDLE proactively well under that limit,
and wraps the whole connect/idle cycle in a reconnect loop with exponential
backoff, discarding and never reusing a connection object after any error.
"""
import email as email_lib
import json
import logging
import os
import re
import smtplib
import sys
import time
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import imapclient
import psycopg2
import psycopg2.extras
import requests

# --- config ------------------------------------------------------------------
IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASSWORD = os.environ["IMAP_PASSWORD"]

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", IMAP_USER)
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", IMAP_PASSWORD)
FROM_HEADER = os.environ.get("FROM_HEADER", f"Act Angel AI <{SMTP_USER}>")
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "actangel.com")

DATABASE_URL = os.environ["DATABASE_URL"]

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

TOOL_WEBHOOK_URL = os.environ["TOOL_WEBHOOK_URL"]

OUR_NUMBER_US = "+13236885001"
OUR_NUMBER_INDIA = "+918031807748"

IDLE_RENEW_SECONDS = 9 * 60
SOCKET_TIMEOUT_SECONDS = 60
BACKOFF_START = 5
BACKOFF_MAX = 300
STATE_FILE = "/opt/act-angel-imap-watcher/last_uid.txt"
HTTP_TIMEOUT = 30

RAW_WINDOW = 12  # most recent messages kept verbatim; anything older gets rolled into a summary
COMPACTION_THRESHOLD = 50  # trigger a compaction pass once this many messages have piled up since the last one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("email-agent")

# --- system prompt (email-adapted from the voice/WhatsApp-aligned prompt) ----
SYSTEM_PROMPT = """You are **Act Angel** — the AI Sales Consultant for Act Angel AI, replying by email. Same identity, same personality as the voice and WhatsApp agents: warm, confident, intelligent, concise, commercially aware, curious, never pushy.

### Golden rule
Don't explain AI when you can demonstrate it. This conversation IS the product demo.

### Language - check every single message, do not coast on earlier context
Match the language the person is writing in right now, not what language the conversation started in or what you replied in last time. Devanagari Hindi, romanized Hinglish, and English are all in play - re-check which one their MOST RECENT message actually uses before you write your reply, every time, even if you have exchanged several messages already.

If they explicitly ask you to reply in Hindi ("reply in Hindi," "hindi mein baat karo," or similar), switch immediately - your very next message must be in Hindi, not a message or two later. Once you switch, STAY in Hindi for every following message until they either explicitly ask you to switch back to English or write a full message of their own in English - do not revert to English on your own after a few exchanges just because that is your default. This has been an active bug (drifting back to English after 2-3 messages, or not switching on the first request) - treat it as a hard rule, not a preference.

### Format for email, not chat
Write a real email: a short greeting, 2-4 focused paragraphs at most, one clear question or next step. Do not add a closing line or sign-off ("Best,", "Regards,", "Act Angel," or similar) — the mailbox's own signature is appended automatically after every send, so anything you add here would be redundant. No walls of text — if you're writing more than what a busy person would read in 20 seconds, cut it down. Plain text, no markdown syntax (no #, *, or [links](url) formatting — this goes out as a real email).

### Cross-channel memory — this is the most important behavior on this surface
If this person has spoken with Act Angel before (by phone, WhatsApp, or a prior email), you will be given that context. Open by referencing it naturally: "Good to hear from you again — when we spoke earlier, you mentioned [specific thing]." Never restart from zero with someone who has prior history.

### Discovery (dynamic — do NOT run this as a fixed question list)
Select what's relevant to what they just said. Never ask something you already know, never ask the same question twice.

Business: What does your company do? Roughly how many new enquiries/leads per month? Where do most enquiries come from (website, ads, calls, WhatsApp, marketplaces, referrals)?

Current process: What happens when a new lead comes in today? How quickly does someone normally contact them? And if they don't answer the first time, what happens after that? — this last one is particularly important; it usually surfaces the real pain (leads going cold after one missed attempt).

Listen for: slow response, unanswered calls, inconsistent follow-up, leads contacted only once, after-hours enquiries, multilingual customers, salespeople cherry-picking leads, poor CRM discipline, missed appointments, lack of management visibility, expensive repetitive manpower, inconsistent customer experience.

### Positioning — actions, not interactions
When it's natural: "We're not trying to give companies another chatbot. A lead comes in — Act Angel acts. Someone doesn't answer — Act Angel follows up. They ask to be contacted later — Act Angel remembers. They're ready to buy — your sales team gets involved." Once the problem is understood, it's fair to point out directly: what's happening in this email exchange right now is essentially what Act Angel would be doing with every one of their leads.

### Qualification (never interrogate — log what naturally comes up)
Gradually determine, as it comes up naturally: **Company** (industry, geography, website, size). **Volume** (leads/month, calls/month, WhatsApp/messages, customer enquiries). **Current setup** (CRM, contact center, WhatsApp provider, telephony, existing AI). **Commercial potential** (average transaction/order value, lead-to-sale conversion, sales team size). **Decision process** (prospect's role, decision maker, evaluation timeline). Call `log_qualification` immediately as you learn each one.

### ROI — only ever from their own numbers
If they've given volume and average deal size, a rough calculation is fine, clearly labeled as an estimate, never invented.

### Objection & question handling

**"How are you different from other AI/chatbot companies?"** — There are good conversational AI tools out there. Our focus is different — less about selling a conversation, more about getting business actions completed: qualifying, following up, booking, updating CRM, escalating an unhappy customer, getting the right person involved. The conversation is the interface, the action is the product.

**"Can you replace my sales team?"** — No, and that's usually not the right way to think about it. Their team is valuable where judgement, negotiation and relationships matter. Act Angel is strongest at making sure opportunities aren't lost because nobody answered quickly enough, followed up enough times, or remembered a requested callback.

**Price question.** Never state, estimate, or imply a number — not even a range — unless one has been specifically pre-approved for this exact prospect (essentially never). Never say "I don't have access to pricing," "you'll have to speak to sales," "pricing is confidential," "it depends," or similar. Instead: pricing is structured around the use case — subscription, usage-based, enterprise, or in some cases outcome-linked — rather than one fixed number, and the team will propose the right structure once they understand what's needed. Ask what they'd want Act Angel handling first.

If they push for a ballpark: explain a single follow-up process looks very different cost-wise from an enterprise deployment across multiple channels, and the team would rather understand the value first, then propose a fitting structure.

If they cite a competitor's price: don't compare per-minute/per-call — redirect to outcomes. Log what they're comparing against via `capture_unsupported_question`.

**Technical questions — the named-product trap.** Describing capability generally is safe ("Act Angel connects to CRMs and business systems through APIs"). But if someone names a specific product (HubSpot, Salesforce, Zoho, any CRM, any integration, any language support), do not confirm or deny it, even though the instinct is to say "yes." Say the general capability, then note you don't want to promise a specific integration before the team confirms it for their setup. Then actually call `capture_unsupported_question` — answering without logging it is a silent failure. Same for traction: no customer counts, client names, industries served, or company history claims.

**"Not interested"** — never argue. Ask once, warmly, whether AI automation just isn't a priority right now or whether Act Angel specifically wasn't the right fit. Use their answer via `update_sentiment`, then close politely.

### Recognizing high intent
Signals: asks price, asks integration questions, asks implementation time, discusses volume, asks for a demo/working session, mentions decision makers, asks about security, gives CRM details, asks for a proposal. When you notice these, it's fair to suggest moving to a working session. Call `book_demo` once they agree.

### Lead scoring (call `update_lead_score` once you have enough signal)
- **HOT** — clear problem + volume + authority + active timeline → move toward booking now.
- **WARM** — relevant problem but unclear urgency/authority → nurture with personalized follow-up.
- **EARLY** — interested in AI but no immediate project → light, permission-based follow-up.
- **LOW_FIT** — insufficient volume/use case/budget → close politely and briefly.

### Behavior
- Never invent product capabilities, statistics, customer names, success stories, or ROI figures. Always distinguish an estimate from a fact.
- WhatsApp and phone are both live channels — if it's natural, mention they can continue there too; never claim to have sent something on a channel that genuinely wasn't used.
- Respect a clear "no" or disinterest immediately.
- Capture any specific requested callback time precisely via `capture_followup_time` — but only when their latest message contains a new, explicit callback ask, not a plain check-in.
- If a technical question comes up that you're not confident about, say so plainly and log it via `capture_unsupported_question`.
- Identify as AI if it's ever in question — never pretend to be human.
- Never end the email with a closing line or sign-off of any kind (no "Best,", "Regards,", "Act Angel," a human name, or similar) — the mailbox's real signature is appended automatically after your reply.

### Sentiment
Track the same categories as the other channels (positive, neutral, skeptical, frustrated, rushed, high-intent) and adapt tone and length accordingly.

### Core philosophy
Listen → Understand → Diagnose → Demonstrate → Quantify → Act. Not a scripted Q&A. The prospect should experience the product while reading its reply.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "capture_followup_time",
            "description": "Schedule an actual phone callback ONLY when the prospect's CURRENT message contains an explicit, new callback request with a time. Do NOT call this for a plain check-in. Never call this tool twice for the same request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "requested_time": {"type": "string", "description": "Relative phrases like 'in 2 minutes' must be passed through EXACTLY as said. Absolute times must be a full ISO 8601 timestamp with offset."},
                    "channel": {"type": "string", "enum": ["call"], "description": "Always 'call' - this books an actual phone call back."},
                },
                "required": ["requested_time", "channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_qualification",
            "description": "Log a single qualification field as soon as you learn it. Call immediately, not batched.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "industry, geography, website, company_size, leads_per_month, calls_per_month, whatsapp_messages_per_month, customer_enquiries, crm, contact_center, whatsapp_provider, telephony, existing_ai, avg_transaction_value, lead_to_sale_conversion, sales_team_size, prospect_role, is_decision_maker, evaluation_timeline"},
                    "value": {"type": "string", "description": "The value learned for this field"},
                },
                "required": ["field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_lead_score",
            "description": "Classify the lead once there's enough signal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "score": {"type": "string", "enum": ["HOT", "WARM", "EARLY", "LOW_FIT"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["score", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_sentiment",
            "description": "Record the prospect's current sentiment whenever it shifts noticeably.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sentiment": {"type": "string", "enum": ["positive", "neutral", "skeptical", "frustrated", "rushed", "high_intent"]},
                },
                "required": ["sentiment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_demo",
            "description": "Book a working session/demo once the prospect agrees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scheduled_time": {"type": "string", "description": "The day/time agreed, in their own words or ISO 8601."},
                    "participants": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["scheduled_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_contact_name",
            "description": "Correct the name on file when the prospect tells you it is wrong. Only call if they actually asked.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_unsupported_question",
            "description": "Log a question you could not answer confidently - especially any specifically named integration, language support, customer count, or traction claim.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
]

# --- db -----------------------------------------------------------------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def find_lead_by_email(cur, email_addr):
    cur.execute("SELECT id, name, phone FROM leads WHERE email = %s ORDER BY created_at DESC LIMIT 1", (email_addr,))
    return cur.fetchone()  # None if no matching lead - we do not create a phoneless lead from a cold email


def already_replied_to(cur, inbound_message_id):
    if not inbound_message_id:
        return False
    cur.execute(
        "SELECT 1 FROM emails WHERE direction = 'outbound' AND in_reply_to = %s LIMIT 1",
        (inbound_message_id,),
    )
    return cur.fetchone() is not None


def already_logged_inbound(cur, message_id):
    # Guards against a duplicate row if a retry (after a transient failure further
    # down the pipeline) re-processes the same uid - without this, a crash between
    # logging the inbound message and finishing the reply would double-log it on
    # the next attempt, even though already_replied_to alone would still prevent
    # actually double-sending a reply.
    if not message_id:
        return False
    cur.execute(
        "SELECT 1 FROM emails WHERE direction = 'inbound' AND message_id = %s LIMIT 1",
        (message_id,),
    )
    return cur.fetchone() is not None


def log_email(cur, lead_id, direction, to_address, from_address, subject, body, message_id, in_reply_to, references, status="received"):
    cur.execute(
        """INSERT INTO emails (lead_id, to_address, from_address, subject, body, status, direction, message_id, in_reply_to, email_references, sent_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())""",
        (lead_id, to_address, from_address, subject, body, status, direction, message_id, in_reply_to, references),
    )


def maybe_compact(cur, lead_id):
    """Rolling summarization: once enough messages have piled up since the last
    compaction, fold everything except the most recent RAW_WINDOW into an updated
    summary (merged with whatever summary already existed) and store it as a new
    prospect_memory row. Keeps per-turn context bounded without silently losing
    anything said early in a long-running thread - mirrors the same pattern built
    into the WhatsApp inbound workflow."""
    cur.execute(
        "SELECT count(*) AS n FROM emails WHERE lead_id = %s AND sent_at > COALESCE("
        "  (SELECT created_at FROM prospect_memory WHERE lead_id = %s AND channel = 'email' ORDER BY created_at DESC LIMIT 1),"
        "  '-infinity'::timestamptz)",
        (lead_id, lead_id),
    )
    if cur.fetchone()["n"] < COMPACTION_THRESHOLD:
        return

    cur.execute(
        "SELECT e.direction, e.subject, e.body, e.sent_at, ps.summary AS prior_summary"
        " FROM emails e"
        " LEFT JOIN LATERAL ("
        "   SELECT summary FROM prospect_memory WHERE lead_id = %s AND channel = 'email' ORDER BY created_at DESC LIMIT 1"
        " ) ps ON true"
        " WHERE e.lead_id = %s AND e.sent_at > COALESCE("
        "   (SELECT created_at FROM prospect_memory WHERE lead_id = %s AND channel = 'email' ORDER BY created_at DESC LIMIT 1),"
        "   '-infinity'::timestamptz)"
        " ORDER BY e.sent_at ASC",
        (lead_id, lead_id, lead_id),
    )
    rows = cur.fetchall()
    if len(rows) <= RAW_WINDOW:
        return

    prior_summary = rows[0]["prior_summary"] or ""
    to_compact = rows[: len(rows) - RAW_WINDOW]
    compact_text = "\n".join(
        f"{'Prospect' if r['direction'] == 'inbound' else 'Agent'}: {r['body'][:MAX_HISTORY_BODY_CHARS]}"
        for r in to_compact
    )

    system_msg = (
        "You are compacting an ongoing sales email conversation's history into a concise but "
        "complete running summary for a sales AI to use as context on future turns. Preserve "
        "every concrete fact: numbers, company/industry details, stated pain points, objections "
        "raised and how they were answered, decisions made, and anything the prospect explicitly "
        "asked for or committed to. Merge the prior summary with the new messages below into ONE "
        "updated summary - integrate, don't just append. Dense and factual, no filler, 3-6 sentences."
    )
    user_msg = (f"Prior summary: {prior_summary}\n\n" if prior_summary else "") + f"New messages since then:\n{compact_text}"

    try:
        result = call_openai([{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}], tools=None)
        new_summary = result["choices"][0]["message"].get("content") or ""
    except Exception:
        log.exception("compaction call failed for lead_id=%s, skipping this cycle", lead_id)
        return

    if not new_summary:
        return

    cur.execute(
        "INSERT INTO prospect_memory (lead_id, channel, summary) VALUES (%s, 'email', %s)",
        (lead_id, new_summary),
    )
    log.info("compacted %d messages into a new summary for lead_id=%s", len(to_compact), lead_id)


def load_context(cur, lead_id):
    # ORDER BY ... ASC LIMIT N takes the OLDEST N rows, not the most recent N -
    # on a thread longer than the limit, this would permanently freeze context
    # at the earliest messages and never see anything said after. Take the most
    # recent N by DESC, then re-sort ascending for the model. RAW_WINDOW is kept
    # small deliberately - anything older is covered by maybe_compact's rolling
    # summary instead, not by widening this raw window.
    cur.execute(
        "SELECT direction, subject, body, sent_at FROM ("
        "  SELECT direction, subject, body, sent_at FROM emails WHERE lead_id = %s"
        "  ORDER BY sent_at DESC LIMIT %s"
        ") recent ORDER BY sent_at ASC",
        (lead_id, RAW_WINDOW),
    )
    thread = cur.fetchall()

    cur.execute(
        "SELECT summary, last_topic FROM prospect_memory WHERE lead_id = %s ORDER BY created_at DESC LIMIT 1",
        (lead_id,),
    )
    memory = cur.fetchone()

    cur.execute(
        "SELECT scheduled_time, status FROM callbacks WHERE lead_id = %s ORDER BY id DESC LIMIT 1",
        (lead_id,),
    )
    callback = cur.fetchone()

    return thread, memory, callback


# --- prompt/context assembly ---------------------------------------------------
MAX_HISTORY_BODY_CHARS = 3000  # bound token usage/cost on long-running threads with verbose emails


def build_messages(thread, memory, callback):
    # `thread` already includes the just-logged current inbound message as its
    # last row (handle_message logs it before calling load_context) - do not
    # append it again here, that would duplicate the current turn in the
    # context sent to the model.
    # Deliberately NOT prefixing history entries with "Subject: ..." - doing so
    # trained the model to imitate that format in its own generated replies
    # (few-shotting off its own prior assistant-role turns), which then leaked
    # the literal "Subject: ..." line into the sent email body, duplicating
    # what the real Subject header already shows.
    history = []
    for row in thread:
        role = "user" if row["direction"] == "inbound" else "assistant"
        body = row["body"][:MAX_HISTORY_BODY_CHARS]
        history.append({"role": role, "content": body})

    extra = ""
    if memory and memory.get("summary"):
        extra += f"\n\nPrior context for this prospect (from voice or WhatsApp - reference it naturally if relevant): summary: {memory['summary']} | last topic discussed: {memory.get('last_topic') or 'n/a'}"

    if callback and callback.get("status"):
        st, t = callback["status"], callback.get("scheduled_time")
        if st in ("scheduled", "processing"):
            extra += f"\n\nCallback status (ground truth): a callback is currently genuinely pending for {t}. It has not happened yet."
        elif st in ("failed", "cancelled"):
            extra += f"\n\nCallback status (ground truth): the callback scheduled for {t} did NOT go through. Do not claim it succeeded - acknowledge honestly if it comes up."
        elif st == "sent":
            extra += f"\n\nCallback status (ground truth): the callback scheduled for {t} was attempted/connected. Do not schedule a duplicate unless explicitly asked."

    return [{"role": "system", "content": SYSTEM_PROMPT + extra}] + history


# --- openai ---------------------------------------------------------------------
def call_openai(messages, tools=TOOLS):
    body = {"model": OPENAI_MODEL, "max_completion_tokens": 500, "messages": messages}
    if tools:
        body["tools"] = tools  # omitted entirely (not sent as null) when tools=None

    for attempt in range(3):
        try:
            resp = requests.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            log.exception("OpenAI call failed (attempt %d/3)", attempt + 1)
            time.sleep(2 ** attempt)
    raise RuntimeError("OpenAI call failed after 3 attempts")


def run_tool_call(tool_call, lead_phone):
    args = json.loads(tool_call["function"]["arguments"])
    is_india = lead_phone.startswith("+91")
    payload = dict(
        function_name=tool_call["function"]["name"],
        caller_phone=lead_phone,
        called_number=OUR_NUMBER_INDIA if is_india else OUR_NUMBER_US,
        source="email",
        metadata={"prospect_phone": lead_phone, "call_type": "email_reply"},
        **args,
    )
    resp = requests.post(TOOL_WEBHOOK_URL, json=payload, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _strip_leading_subject_line(text):
    # Defensive net alongside the build_messages fix above - strips a leading
    # "Subject: ...\n" line if the model produces one for any other reason.
    return re.sub(r"^\s*subject\s*:.*?\n+", "", text, count=1, flags=re.IGNORECASE)


def generate_reply(thread, memory, callback, lead_phone):
    messages = build_messages(thread, memory, callback)
    result = call_openai(messages)
    choice = result["choices"][0]["message"]

    tool_calls = choice.get("tool_calls") or []
    if not tool_calls:
        return _strip_leading_subject_line(
            choice.get("content") or "Thanks for your message - let me get back to you on that shortly."
        )

    # Same two-step pattern as WhatsApp: execute the tool(s), then ask the model
    # for the actual reply text now that it has the tool result as ground truth.
    # Deliberately NOT offering tools again on this second call (matching the
    # WhatsApp workflow's own followup call) - it forces a final natural-language
    # reply instead of risking a second round of tool_calls with no content that
    # would otherwise silently fall through to the generic fallback text below.
    followup_messages = messages + [{"role": "assistant", "content": None, "tool_calls": tool_calls}]
    for tc in tool_calls:
        try:
            tool_result = run_tool_call(tc, lead_phone)
        except Exception:
            log.exception("tool call %s failed", tc["function"]["name"])
            tool_result = {"success": False, "error": "tool execution failed"}
        followup_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(tool_result)})

    followup = call_openai(followup_messages, tools=None)
    return _strip_leading_subject_line(
        followup["choices"][0]["message"].get("content") or "Thanks for your message - let me get back to you on that shortly."
    )


# --- smtp ------------------------------------------------------------------------
def send_reply(to_address, subject, body, in_reply_to_message_id, references):
    reply_subject = subject if subject.strip().lower().startswith("re:") else f"Re: {subject}"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = reply_subject
    msg["From"] = FROM_HEADER
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    new_message_id = make_msgid(domain=MAIL_DOMAIN)
    msg["Message-ID"] = new_message_id
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = f"{references} {in_reply_to_message_id}".strip()

    last_err = None
    for attempt in range(3):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=HTTP_TIMEOUT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
            return new_message_id, reply_subject
        except Exception as e:
            last_err = e
            log.exception("SMTP send failed (attempt %d/3)", attempt + 1)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"SMTP send failed after 3 attempts: {last_err}")


# --- email parsing -----------------------------------------------------------------
def extract_email_address(header_value):
    if not header_value:
        return ""
    m = re.search(r"<([^>]+)>", header_value)
    return (m.group(1) if m else header_value).strip().lower()


def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html" and not part.get_filename():
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        return ""
    charset = msg.get_content_charset() or "utf-8"
    payload = msg.get_payload(decode=True)
    return payload.decode(charset, errors="replace") if payload else ""


# Most real mail clients include the full quoted prior message below a reply
# ("On <date>, X wrote:" followed by "> "-prefixed lines). Left unstripped,
# that quoted history both duplicates what's already in `thread` from our own
# DB and can confuse the model into reacting to old content as if it were new.
# This is a standard heuristic, not perfect for every client, but handles the
# large majority of real-world quoting conventions.
QUOTE_HEADER_RE = re.compile(r"^\s*On .{0,120} wrote:\s*$", re.IGNORECASE)


def strip_quoted_reply(body):
    lines = body.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        if QUOTE_HEADER_RE.match(line):
            cut = i
            break
        if line.startswith(">") and all(l.startswith(">") or not l.strip() for l in lines[i:]):
            cut = i
            break
    return "\n".join(lines[:cut]).strip()


# --- main per-message handling -----------------------------------------------------
def load_last_uid():
    try:
        with open(STATE_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def save_last_uid(uid):
    with open(STATE_FILE, "w") as f:
        f.write(str(uid))


def handle_message(conn, uid):
    raw = conn.fetch([uid], ["RFC822"])[uid][b"RFC822"]
    msg = email_lib.message_from_bytes(raw)

    from_email = extract_email_address(msg.get("From", ""))
    subject = msg.get("Subject", "") or ""
    body = strip_quoted_reply(get_body(msg).strip())
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")
    references = msg.get("References", "")

    if not from_email or not body:
        log.info("uid=%s skipped: missing from/body", uid)
        return

    # First DB round-trip: match lead, guard against double-processing, log the
    # inbound message, load context - then close the connection before the
    # slow part (OpenAI + tool webhook + SMTP can easily take several seconds
    # combined), rather than holding a Postgres connection idle the whole time.
    with db_conn() as conn_db:
        with conn_db.cursor() as cur:
            lead = find_lead_by_email(cur, from_email)
            if not lead:
                log.info("uid=%s from=%s: no matching lead, acknowledging but not logging", uid, from_email)
                conn_db.commit()
                return

            if already_replied_to(cur, message_id):
                log.info("uid=%s from=%s: already replied to this message, skipping (idempotency guard)", uid, from_email)
                conn_db.commit()
                return

            if already_logged_inbound(cur, message_id):
                log.info("uid=%s from=%s: inbound already logged from a prior attempt, loading context without re-inserting", uid, from_email)
            else:
                log_email(cur, lead["id"], "inbound", IMAP_USER, from_email, subject, body, message_id, in_reply_to, references)
            conn_db.commit()
            maybe_compact(cur, lead["id"])
            conn_db.commit()
            thread, memory, callback = load_context(cur, lead["id"])

    reply_text = generate_reply(thread, memory, callback, lead["phone"])
    new_message_id, reply_subject = send_reply(from_email, subject or "Act Angel AI", reply_text, message_id, references)

    # Second, fresh connection for the final write.
    with db_conn() as conn_db:
        with conn_db.cursor() as cur:
            log_email(cur, lead["id"], "outbound", from_email, IMAP_USER, reply_subject, reply_text, new_message_id, message_id, f"{references} {message_id}".strip(), status="sent")
            conn_db.commit()

    log.info("uid=%s from=%s: replied and logged (lead_id=%s)", uid, from_email, lead["id"])


def catch_up(conn):
    last_uid = load_last_uid()
    if last_uid is None:
        all_uids = conn.search(["ALL"])
        if all_uids:
            save_last_uid(max(all_uids))
        log.info("first run: baselined at current mailbox state, not replaying history")
        return

    uids = sorted(u for u in conn.search(["UID", f"{last_uid + 1}:*"]) if u > last_uid)
    for uid in uids:
        try:
            handle_message(conn, uid)
        except Exception:
            log.exception("failed to process uid=%s, will retry next cycle", uid)
            return
        save_last_uid(uid)


def idle_loop(conn):
    while True:
        conn.idle()
        try:
            responses = conn.idle_check(timeout=IDLE_RENEW_SECONDS)
        finally:
            conn.idle_done()

        if responses:
            log.info("IDLE woke with %d event(s), checking for new mail", len(responses))
            catch_up(conn)
        else:
            log.info("proactive IDLE renewal (refreshing before Gmail's forced timeout)")


def main():
    backoff = BACKOFF_START
    while True:
        conn = None
        try:
            conn = imapclient.IMAPClient(IMAP_HOST, ssl=True, timeout=SOCKET_TIMEOUT_SECONDS)
            conn.login(IMAP_USER, IMAP_PASSWORD)
            conn.select_folder("INBOX")
            log.info("connected and logged in as %s", IMAP_USER)
            backoff = BACKOFF_START
            catch_up(conn)
            idle_loop(conn)
        except Exception:
            log.exception("connection cycle failed, reconnecting in %ss", backoff)
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)


if __name__ == "__main__":
    main()
