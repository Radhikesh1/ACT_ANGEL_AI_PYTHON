import logging
import os

import psycopg2
import psycopg2.extras

# Separate from this project's own DATABASE_URL (database/connection.py) - that
# one is the pipecat/telephony database (assistants, call_logs, ...). This is
# the shared leads/emails/prospect_memory/callbacks database that n8n's intake
# workflow and the voice/WhatsApp agents already write to.
LEADS_DATABASE_URL = os.environ["LEADS_DATABASE_URL"]

RAW_WINDOW = 12  # most recent messages kept verbatim; anything older gets rolled into a summary
COMPACTION_THRESHOLD = 50  # trigger a compaction pass once this many messages have piled up since the last one
MAX_HISTORY_BODY_CHARS = 3000  # bound token usage/cost on long-running threads with verbose emails

log = logging.getLogger(__name__)


def db_conn():
    return psycopg2.connect(LEADS_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def find_lead_by_email(cur, email_addr):
    cur.execute(
        "SELECT id, name, phone, email, lead_source, submission_timestamp FROM leads "
        "WHERE email = %s ORDER BY created_at DESC LIMIT 1",
        (email_addr,),
    )
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


def maybe_compact(cur, lead_id, call_openai_fn):
    """Rolling summarization: once enough messages have piled up since the last
    compaction, fold everything except the most recent RAW_WINDOW into an updated
    summary (merged with whatever summary already existed) and store it as a new
    prospect_memory row. Keeps per-turn context bounded without silently losing
    anything said early in a long-running thread - mirrors the same pattern built
    into the WhatsApp inbound workflow. call_openai_fn is injected (rather than
    imported) so this module stays free of OpenAI-specific config."""
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
        result = call_openai_fn([{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}], tools=None)
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
