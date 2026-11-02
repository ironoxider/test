"""Turn email to the help desk mailbox into tickets, and send replies from it.

Every few minutes (and on "Check now") new messages in the mailbox's inbox are read:
- a reply to a ticket's email (same Gmail thread, a reply to one of our messages, or
  "[Ticket #12]" in the subject) is added to that ticket, reopening it if needed;
- anything else from an allowed address becomes a new ticket, with asset tags and a room
  mentioned in it linked automatically, and an automatic "we got it" reply if that's on.
Processed messages get the Gmail label "Help Desk/Imported" (or "Help Desk/Skipped" for
auto-replies and outside senders) and stay in the mailbox.
"""

import email
import email.policy
import html.parser
import os
import re
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr

from flask import current_app

import app as core
import app_settings
import gmail_api

IMPORTED_LABEL = "Help Desk/Imported"
SKIPPED_LABEL = "Help Desk/Skipped"
TICKET_TAG_RE = re.compile(r"\[Ticket #(\d+)\]", re.I)
MAX_BODY = 20000
DEFAULTS = {"mail_enabled": False, "mail_interval": 2, "mail_auto_reply": True,
            "mail_allowed_domain": "thehudsonschool.org", "mail_from_name": "IT Help Desk",
            # The IT team: who hears about new tickets and replies, and gets the daily digest.
            "mail_team": "", "notify_new": True, "notify_replies": True,
            "digest_enabled": False, "digest_time": "07:30", "digest_weekdays": True,
            "mail_link_base": ""}

_check_lock = threading.Lock()


def _hd():
    import helpdesk  # imported late: helpdesk imports this module too
    return helpdesk


# ---------------------------------------------------------------- settings & status


def config():
    data = app_settings.load(current_app.config["SETTINGS_PATH"])
    return {k: data.get(k, v) for k, v in DEFAULTS.items()} | {
        "mailbox": data.get("mail_mailbox"),
        "last_check": data.get("mail_last_check"), "last_error": data.get("mail_last_error"),
        "last_result": data.get("mail_last_result"), "last_digest": data.get("last_digest")}


def team_addresses(cfg):
    return list(dict.fromkeys(a.lower() for a in re.findall(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+",
                                                             cfg.get("mail_team") or "")))


def link_base(cfg=None):
    """The start of links in emails, e.g. http://192.168.1.20:5000 (this computer on the network)."""
    cfg = cfg or config()
    if cfg.get("mail_link_base"):
        return cfg["mail_link_base"].rstrip("/")
    port = current_app.config.get("LISTEN_PORT") or 5000
    addresses = app_settings.lan_addresses()
    return f"http://{addresses[0] if addresses else '127.0.0.1'}:{port}"


def ticket_url(ticket_id, cfg=None):
    return f"{link_base(cfg)}/helpdesk/{ticket_id}"


def data_dir():
    return os.path.dirname(os.path.abspath(current_app.config["DATABASE"]))


def connected():
    return bool(gmail_api.load_secrets(data_dir()).get("refresh_token"))


def get_client():
    """The Gmail client for the help desk mailbox (replaced in tests)."""
    return gmail_api.GmailClient.from_secrets(gmail_api.load_secrets(data_dir()))


def record_status(error=None, result=None):
    changes = {"mail_last_check": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
               "mail_last_error": error}
    if result is not None:
        changes["mail_last_result"] = result
    app_settings.update(current_app.config["SETTINGS_PATH"], **changes)


# ---------------------------------------------------------------- reading an email


class _TextFromHtml(html.parser.HTMLParser):
    BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup):
    parser = _TextFromHtml()
    parser.feed(markup)
    return "".join(parser.parts)


def strip_quoted(text):
    """Drop the quoted earlier conversation from a reply ("On … wrote:" and below)."""
    patterns = [
        r"\n\s*On [^\n]{0,200}?(\n[^\n]{0,200}?)?wrote:\s*\n",       # Gmail, Apple Mail (may wrap)
        r"\n\s*-{2,}\s*Original Message\s*-{2,}",                   # Outlook
        r"\n\s*From: [^\n]+\n\s*Sent: ",                            # Outlook
        r"\n\s*-{2,}\s*Forwarded message\s*-{2,}",
    ]
    cut = len(text)
    for pattern in patterns[:3]:
        m = re.search(pattern, "\n" + text, re.I)
        if m:
            cut = min(cut, max(0, m.start() - 1))
    text = text[:cut]
    lines = text.rstrip().split("\n")
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    return "\n".join(lines).strip()


def clean_body(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_BODY]


def parse_message(raw):
    """The parts of an email the help desk uses."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, address = parseaddr(str(msg.get("From", "")))
    plain = html_body = None
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or (filename and disposition != "inline"):
            attachments.append(filename or "attachment")
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            continue
        if ctype == "text/plain" and plain is None and isinstance(content, str):
            plain = content
        elif ctype == "text/html" and html_body is None and isinstance(content, str):
            html_body = content
    body = plain if plain is not None else html_to_text(html_body or "")
    refs = " ".join(str(msg.get(h, "")) for h in ("In-Reply-To", "References"))
    auto = str(msg.get("Auto-Submitted", "no")).lower() not in ("", "no") \
        or str(msg.get("Precedence", "")).lower() in ("bulk", "junk", "auto_reply", "list") \
        or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond")) \
        or bool(re.match(r"(mailer-daemon|postmaster|no-?reply|do-?not-?reply)@", address, re.I))
    return {
        "from_name": name.strip() or None,
        "from_email": address.strip().lower() or None,
        "to": [a.lower() for _, a in getaddresses([str(msg.get("To", "")), str(msg.get("Cc", ""))]) if a],
        "subject": " ".join(str(msg.get("Subject", "")).split()) or "(no subject)",
        "message_id": str(msg.get("Message-ID", "")).strip() or None,
        "references": re.findall(r"<[^<>\s]+>", refs),
        "body": clean_body(strip_quoted(clean_body(body))),
        "attachments": attachments,
        "automatic": auto,
    }


# ---------------------------------------------------------------- processing


def find_ticket(db, parsed, thread_id):
    """The ticket an incoming email belongs to, if any."""
    m = TICKET_TAG_RE.search(parsed["subject"])
    if m:
        row = db.execute("SELECT * FROM tickets WHERE id = ?", (int(m.group(1)),)).fetchone()
        if row:
            return row
    if thread_id:
        row = db.execute("SELECT * FROM tickets WHERE gmail_thread_id = ?", (thread_id,)).fetchone()
        if row:
            return row
    for ref in parsed["references"]:
        row = db.execute("SELECT t.* FROM ticket_messages m JOIN tickets t ON t.id = m.ticket_id "
                         "WHERE m.message_id = ?", (ref,)).fetchone()
        if row:
            return row
    return None


def body_with_attachments(parsed):
    body = parsed["body"] or "(no text)"
    if parsed["attachments"]:
        body += ("\n\n[Attached to the email: " + ", ".join(parsed["attachments"])
                 + ". Open it in Gmail to see them.]")
    return body


def skip_reason(parsed, cfg, mailbox):
    sender = parsed["from_email"] or ""
    if not sender:
        return "no sender address"
    if sender == (mailbox or "").lower():
        return "sent by the help desk itself"
    if parsed["automatic"]:
        return "automatic message (auto-reply, bounce or mailing list)"
    domain = (cfg["mail_allowed_domain"] or "").strip().lower().lstrip("@")
    if domain and not sender.endswith("@" + domain):
        return f"not from an @{domain} address"
    return None


def process_mailbox(client, db, cfg, mailbox):
    """Import new mail. Returns counts of what happened."""
    imported = client.label_id(IMPORTED_LABEL)
    skipped_label = client.label_id(SKIPPED_LABEL)
    counts = {"new": 0, "replies": 0, "skipped": 0, "notes": 0}
    query = f'in:inbox -label:"{IMPORTED_LABEL}" -label:"{SKIPPED_LABEL}"'
    for gmail_id in client.list_ids(query):
        if db.execute("SELECT 1 FROM ticket_messages WHERE gmail_id = ?", (gmail_id,)).fetchone():
            client.add_label(gmail_id, imported)  # imported before, label didn't stick
            continue
        raw, thread_id = client.get_raw(gmail_id)
        parsed = parse_message(raw)
        ticket = find_ticket(db, parsed, thread_id)
        reason = skip_reason(parsed, cfg, mailbox)
        if reason and not (ticket and parsed["from_email"] and not parsed["automatic"]
                           and parsed["from_email"] != (mailbox or "").lower()):
            client.add_label(gmail_id, skipped_label)
            counts["skipped"] += 1
            continue
        team = team_addresses(cfg)
        if ticket and parsed["from_email"] in team and parsed["from_email"] != (ticket["requester_email"] or "").lower():
            # An IT team member replying (usually to a notification): an internal note.
            add_team_note(db, ticket, parsed, gmail_id)
            counts["notes"] = counts.get("notes", 0) + 1
        elif ticket:
            add_incoming(db, ticket, parsed, gmail_id)
            counts["replies"] += 1
            db.commit()
            if cfg["notify_replies"]:
                notify_team(client, db, ticket["id"], "reply", cfg, mailbox, parsed)
        else:
            ticket_id = create_from_email(db, parsed, gmail_id, thread_id)
            counts["new"] += 1
            db.commit()
            if cfg["mail_auto_reply"]:
                try:
                    send_acknowledgement(client, db, ticket_id, parsed, cfg, mailbox)
                except gmail_api.GmailError as e:
                    _hd().add_message(db, ticket_id, "event", f"The automatic reply couldn't be sent: {e}")
            if cfg["notify_new"]:
                notify_team(client, db, ticket_id, "new", cfg, mailbox, parsed)
        db.commit()
        client.add_label(gmail_id, imported)
    return counts


def add_team_note(db, ticket, parsed, gmail_id):
    db.execute("INSERT INTO ticket_messages (ticket_id, kind, author, author_email, body, message_id, gmail_id)"
               " VALUES (?, 'note', ?, ?, ?, ?, ?)",
               (ticket["id"], parsed["from_name"] or parsed["from_email"], parsed["from_email"],
                body_with_attachments(parsed), parsed["message_id"], gmail_id))
    db.execute("UPDATE tickets SET updated_at = datetime('now') WHERE id = ?", (ticket["id"],))


def add_incoming(db, ticket, parsed, gmail_id):
    hd = _hd()
    db.execute("INSERT INTO ticket_messages (ticket_id, kind, author, author_email, body, message_id, gmail_id)"
               " VALUES (?, 'incoming', ?, ?, ?, ?, ?)",
               (ticket["id"], parsed["from_name"] or parsed["from_email"], parsed["from_email"],
                body_with_attachments(parsed), parsed["message_id"], gmail_id))
    db.execute("UPDATE tickets SET updated_at = datetime('now'), unread = 1 WHERE id = ?", (ticket["id"],))
    if ticket["status"] in ("Closed", "Waiting"):
        db.execute("UPDATE tickets SET status = 'Open', closed_at = NULL WHERE id = ?", (ticket["id"],))
        hd.add_message(db, ticket["id"], "event", f"Status: {ticket['status']} → Open (the requester replied)")
    hd.auto_link(db, ticket["id"], parsed["body"], "a reply")


def create_from_email(db, parsed, gmail_id, thread_id):
    hd = _hd()
    subject = TICKET_TAG_RE.sub("", parsed["subject"]).strip() or "(no subject)"
    cur = db.execute(
        "INSERT INTO tickets (subject, requester_name, requester_email, status, source, gmail_thread_id, unread)"
        " VALUES (?, ?, ?, 'New', 'email', ?, 1)",
        (subject, parsed["from_name"], parsed["from_email"], thread_id))
    ticket_id = cur.lastrowid
    db.execute("INSERT INTO ticket_messages (ticket_id, kind, author, author_email, body, message_id, gmail_id)"
               " VALUES (?, 'description', ?, ?, ?, ?, ?)",
               (ticket_id, parsed["from_name"] or parsed["from_email"], parsed["from_email"],
                body_with_attachments(parsed), parsed["message_id"], gmail_id))
    hd.add_message(db, ticket_id, "event", "Ticket created from an email.")
    # Link the devices (asset tags or serial numbers) and room the email mentions.
    hd.auto_link(db, ticket_id, f"{subject}\n{parsed['body']}", "the email")
    return ticket_id


# ---------------------------------------------------------------- sending


def build_email(cfg, mailbox, ticket, to, body, in_reply_to=None, references=()):
    msg = EmailMessage()
    msg["From"] = f'{cfg["mail_from_name"]} <{mailbox}>' if cfg["mail_from_name"] else mailbox
    msg["To"] = to
    msg["Subject"] = f"Re: [Ticket #{ticket['id']}] {ticket['subject']}"
    msg["Message-ID"] = make_msgid(domain=mailbox.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = " ".join(list(references) + [in_reply_to])
    msg.set_content(body)
    return msg


def thread_headers(db, ticket_id):
    """In-Reply-To and References for the next email on a ticket, so mail apps thread it."""
    ids = [r["message_id"] for r in db.execute(
        "SELECT message_id FROM ticket_messages WHERE ticket_id = ? AND message_id IS NOT NULL"
        " AND kind NOT IN ('notify', 'note') ORDER BY id",  # the requester's conversation only
        (ticket_id,))]
    return (ids[-1] if ids else None), ids[:-1][-10:]


def send_on_ticket(client, db, ticket_id, body, cfg, mailbox, kind, author=None):
    """Email `body` to the ticket's requester and record it on the ticket."""
    hd = _hd()
    ticket = hd.get_ticket(db, ticket_id)
    if not ticket["requester_email"]:
        raise gmail_api.GmailError("This ticket has no requester email address to reply to.")
    in_reply_to, references = thread_headers(db, ticket_id)
    msg = build_email(cfg, mailbox, ticket, ticket["requester_email"], body, in_reply_to, references)
    gmail_id, thread_id = client.send(msg.as_bytes(), ticket["gmail_thread_id"])
    db.execute("INSERT INTO ticket_messages (ticket_id, kind, author, author_email, body, message_id, gmail_id)"
               " VALUES (?, ?, ?, ?, ?, ?, ?)",
               (ticket_id, kind, author, mailbox, body, msg["Message-ID"], gmail_id))
    if thread_id and not ticket["gmail_thread_id"]:
        db.execute("UPDATE tickets SET gmail_thread_id = ? WHERE id = ?", (thread_id, ticket_id))
    db.execute("UPDATE tickets SET updated_at = datetime('now') WHERE id = ?", (ticket_id,))


def send_acknowledgement(client, db, ticket_id, parsed, cfg, mailbox):
    first = (parsed["from_name"] or "").split()[0] if parsed["from_name"] else "there"
    ticket = _hd().get_ticket(db, ticket_id)
    body = (f"Hi {first},\n\nThanks, we've received your request and opened ticket #{ticket_id}: "
            f"{ticket['subject']}\n\nTo add details or photos, reply to this email.\n\n"
            f"{cfg['mail_from_name'] or 'IT Help Desk'}")
    send_on_ticket(client, db, ticket_id, body, cfg, mailbox, "auto", cfg["mail_from_name"])
    db.commit()


# ---------------------------------------------------------------- the IT team


def build_team_email(cfg, mailbox, to, subject, text, html=None):
    msg = EmailMessage()
    msg["From"] = f'{cfg["mail_from_name"]} <{mailbox}>' if cfg["mail_from_name"] else mailbox
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=mailbox.split("@")[-1])
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def notify_team(client, db, ticket_id, what, cfg, mailbox, parsed):
    """Email the IT team about a new ticket ("new") or a requester's reply ("reply").

    Its Message-ID is kept on the ticket, so a team member's reply to it becomes an internal note.
    """
    hd = _hd()
    team = [a for a in team_addresses(cfg) if a != (parsed.get("from_email") or "")]
    if not team:
        return
    ticket = hd.get_ticket(db, ticket_id)
    devices = ", ".join(core.device_label(d) for d in hd.linked_devices(db, ticket_id))
    who = parsed.get("from_name") or parsed.get("from_email") or "Someone"
    heading = f"New ticket from {who}" if what == "new" else f"{who} replied"
    details = [("Requester", f"{ticket['requester_name'] or ''} <{ticket['requester_email'] or ''}>".strip()),
               ("Location", ticket["location"]), ("Devices", devices), ("Priority", ticket["priority"]),
               ("Assigned to", ticket["assigned_to"] or "nobody yet")]
    text = (f"{heading}: #{ticket_id} {ticket['subject']}\n\n{parsed.get('body') or ''}\n\n"
            + "\n".join(f"{k}: {v}" for k, v in details if v) +
            f"\n\nOpen the ticket: {ticket_url(ticket_id, cfg)}\n\n"
            "Reply to this email to add an internal note to the ticket (the requester won't see it).")
    label = "New ticket" if what == "new" else "Reply on ticket"
    msg = build_team_email(cfg, mailbox, team, f"[Help Desk] {label} #{ticket_id}: {ticket['subject']}", text)
    try:
        gmail_id, _ = client.send(msg.as_bytes())
    except gmail_api.GmailError as e:
        hd.add_message(db, ticket_id, "event", f"Couldn't email the IT team: {e}")
        return
    db.execute("INSERT INTO ticket_messages (ticket_id, kind, author, author_email, body, message_id, gmail_id)"
               " VALUES (?, 'notify', ?, ?, ?, ?, ?)",
               (ticket_id, cfg["mail_from_name"], mailbox, f"Emailed the IT team ({', '.join(team)}).",
                msg["Message-ID"], gmail_id))


def open_tickets_for_digest(db):
    return db.execute(
        "SELECT t.*, l.name AS location,"
        " (SELECT GROUP_CONCAT(COALESCE(d.asset_tag, d.name), ', ') FROM ticket_devices td"
        "  JOIN devices d ON d.id = td.device_id WHERE td.ticket_id = t.id) AS devices"
        " FROM tickets t LEFT JOIN locations l ON l.id = t.location_id WHERE t.status != 'Closed'"
        " ORDER BY CASE t.priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END,"
        " t.created_at LIMIT 200").fetchall()


def digest_email(db, cfg, mailbox, to):
    """The daily summary of open tickets, as an email (plain text and HTML)."""
    from html import escape
    tickets = open_tickets_for_digest(db)
    closed = db.execute("SELECT COUNT(*) FROM tickets WHERE closed_at >= datetime('now', '-1 day')").fetchone()[0]
    counts = {
        "open": len(tickets), "new": sum(t["status"] == "New" for t in tickets),
        "unassigned": sum(not t["assigned_to"] for t in tickets),
        "urgent": sum(t["priority"] in ("High", "Urgent") for t in tickets),
    }
    summary = (f"{counts['open']} open ticket{'s' if counts['open'] != 1 else ''}: {counts['new']} new, "
               f"{counts['unassigned']} unassigned, {counts['urgent']} high or urgent. "
               f"{closed} closed in the last 24 hours.")
    lines = [summary, ""]
    rows = []
    for t in tickets:
        age = core.time_ago(t["created_at"]).replace(" ago", "")
        lines.append(f"#{t['id']} [{t['priority']}, {t['status']}] {t['subject']} - "
                     f"{t['requester_name'] or t['requester_email'] or ''}"
                     f"{' - ' + t['location'] if t['location'] else ''} - "
                     f"{t['assigned_to'] or 'unassigned'} - open {age}\n  {ticket_url(t['id'], cfg)}")
        rows.append(
            "<tr>" + "".join(f'<td style="padding:4px 8px;border-bottom:1px solid #ddd">{c}</td>' for c in (
                f'<a href="{escape(ticket_url(t["id"], cfg))}">#{t["id"]}</a>',
                f'<a href="{escape(ticket_url(t["id"], cfg))}">{escape(t["subject"])}</a>',
                escape(t["requester_name"] or t["requester_email"] or ""), escape(t["location"] or ""),
                escape(t["devices"] or ""), escape(t["priority"]), escape(t["status"]),
                escape(t["assigned_to"] or "unassigned"), escape(age))) + "</tr>")
    if not tickets:
        lines.append("No open tickets.")
    lines += ["", f"Help Desk: {link_base(cfg)}/helpdesk/"]
    head = "".join(f'<th style="text-align:left;padding:4px 8px;border-bottom:2px solid #999">{h}</th>' for h in
                   ("#", "Subject", "Requester", "Location", "Devices", "Priority", "Status", "Assigned", "Open for"))
    html_body = (f'<p style="font-family:sans-serif">{escape(summary)}</p>'
                 + (f'<table style="border-collapse:collapse;font-family:sans-serif;font-size:14px">'
                    f'<tr>{head}</tr>{"".join(rows)}</table>' if rows else "<p>No open tickets.</p>")
                 + f'<p style="font-family:sans-serif"><a href="{escape(link_base(cfg))}/helpdesk/">Open the Help Desk</a></p>')
    now = datetime.now()
    subject = f"[Help Desk] {now:%A, %B} {now.day}: {counts['open']} open, {counts['new']} new"
    return build_team_email(cfg, mailbox, to, subject, "\n".join(lines), html_body)


def send_digest(client=None):
    """Send the daily digest now. Returns an error message, or None."""
    cfg = config()
    team = team_addresses(cfg)
    if not team:
        return "Add the IT team's email addresses first."
    if not connected() or not cfg["mailbox"]:
        return "The help desk mailbox isn't connected."
    try:
        msg = digest_email(core.get_db(), cfg, cfg["mailbox"], team)
        (client or get_client()).send(msg.as_bytes())
    except gmail_api.GmailError as e:
        return str(e)
    app_settings.update(current_app.config["SETTINGS_PATH"], last_digest=datetime.now().strftime("%Y-%m-%d"))
    return None


def digest_due(cfg, now=None):
    """Whether today's digest should go out now (after the set time, once a day)."""
    now = now or datetime.now()
    if not cfg["digest_enabled"] or cfg.get("last_digest") == now.strftime("%Y-%m-%d"):
        return False
    if cfg["digest_weekdays"] and now.weekday() >= 5:
        return False
    try:
        hour, minute = (int(x) for x in str(cfg["digest_time"]).split(":"))
    except ValueError:
        hour, minute = 7, 30
    return (now.hour, now.minute) >= (hour, minute)


# ---------------------------------------------------------------- checking


def check_now():
    """Check the mailbox once (inside an app context). Returns (counts or None, error or None)."""
    cfg = config()
    if not connected() or not cfg["mailbox"]:
        return None, "The help desk mailbox isn't connected."
    if not _check_lock.acquire(blocking=False):
        return None, "A check is already running."
    try:
        counts = process_mailbox(get_client(), core.get_db(), cfg, cfg["mailbox"])
        record_status(result=counts)
        return counts, None
    except gmail_api.GmailError as e:
        core.get_db().rollback()
        record_status(error=str(e))
        return None, str(e)
    finally:
        _check_lock.release()


def start_background(app):
    """Check the mailbox every few minutes while the app runs (started by the launcher)."""
    def loop():
        last = 0.0
        while True:
            time.sleep(15)
            try:
                with app.app_context():
                    cfg = config()
                    if cfg["mail_enabled"] and time.time() - last >= max(1, int(cfg["mail_interval"])) * 60:
                        last = time.time()
                        check_now()
                    if digest_due(cfg) and connected():
                        error = send_digest()
                        if error:
                            print(f"Help desk daily digest wasn't sent: {error}", flush=True)
                            # Don't retry every 15 seconds; try again tomorrow (or use Send now).
                            app_settings.update(current_app.config["SETTINGS_PATH"],
                                                last_digest=datetime.now().strftime("%Y-%m-%d"),
                                                mail_last_error=f"Daily digest wasn't sent: {error}")
            except Exception as e:  # keep checking even if one round fails unexpectedly
                print(f"Help desk email check failed: {e}", flush=True)
    threading.Thread(target=loop, name="helpdesk-mail", daemon=True).start()
