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
import smtplib
import sys
import time
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import imapclient
import requests

from services.email_agent_prompt import SYSTEM_PROMPT, TOOLS
from services.email_repository import (
    MAX_HISTORY_BODY_CHARS,
    already_logged_inbound,
    already_replied_to,
    db_conn,
    find_lead_by_email,
    load_context,
    log_email,
    maybe_compact,
)
from utils.email_parsing import extract_email_address, get_body, strip_leading_subject_line, strip_quoted_reply

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("email-agent")


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


# --- prompt/context assembly ---------------------------------------------------
def build_messages(thread, memory, callback, lead=None):
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

    # Without this the model had no idea who it was writing to: the lead's name and
    # email were looked up but never reached the prompt, so replies read as generic
    # even for a prospect who had filled in the website form. Same gap that was found
    # and fixed on the WhatsApp side - keep the two in step.
    extra = ""
    if lead:
        lines = []
        lead_name = (lead.get("name") or "").strip()
        lead_email = (lead.get("email") or "").strip()
        if lead_name:
            first_name = lead_name.split()[0]
            lines.append(
                f"- Their name is {lead_name}. Address them as \"{first_name}\". "
                "Never ask them what their name is - you already have it."
            )
        if lead_email:
            lines.append(
                f"- Email already on file: {lead_email}. Never ask them to give you their email again."
            )
        if (lead.get("lead_source") or "").strip() == "website_trial":
            when = ""
            submitted = lead.get("submission_timestamp")
            if submitted:
                try:
                    when = f" on {submitted.date().isoformat()}"
                except AttributeError:
                    when = ""
            lines.append(
                f"- They came to us by submitting the enquiry form on the Act Angel AI website{when}. "
                "They already gave us their details and are expecting to hear from us, so treat this "
                "as a warm follow-up to that enquiry, never a cold generic opener."
            )
        if lines:
            extra += (
                "\n\nWho you are writing to (ground truth from our own records - trust this over "
                "anything inferred from the thread):\n" + "\n".join(lines)
            )

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


def generate_reply(thread, memory, callback, lead_phone, lead=None):
    messages = build_messages(thread, memory, callback, lead)
    result = call_openai(messages)
    choice = result["choices"][0]["message"]

    tool_calls = choice.get("tool_calls") or []
    if not tool_calls:
        return strip_leading_subject_line(
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
    return strip_leading_subject_line(
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
            maybe_compact(cur, lead["id"], call_openai)
            conn_db.commit()
            thread, memory, callback = load_context(cur, lead["id"])

    reply_text = generate_reply(thread, memory, callback, lead["phone"], lead)
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
