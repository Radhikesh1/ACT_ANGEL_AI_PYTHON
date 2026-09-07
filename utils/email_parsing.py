import re

# Most real mail clients include the full quoted prior message below a reply
# ("On <date>, X wrote:" followed by "> "-prefixed lines). Left unstripped,
# that quoted history both duplicates what's already in the DB thread and can
# confuse the model into reacting to old content as if it were new. This is a
# standard heuristic, not perfect for every client, but handles the large
# majority of real-world quoting conventions.
QUOTE_HEADER_RE = re.compile(r"^\s*On .{0,120} wrote:\s*$", re.IGNORECASE)


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


def strip_leading_subject_line(text):
    # Defensive net alongside build_messages's own fix (services/email_agent.py) -
    # strips a leading "Subject: ...\n" line if the model produces one for any reason.
    return re.sub(r"^\s*subject\s*:.*?\n+", "", text, count=1, flags=re.IGNORECASE)
