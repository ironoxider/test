"""Help desk: tickets from staff, each linked to devices and/or a location.

Tickets are created here (by IT staff, or from a device's page) or from email to the help
desk mailbox (see mailsync.py), and answered here by internal notes or email replies.
"""

import re
import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

import app as core
import app_settings
import gmail_api
import mailsync

bp = Blueprint("helpdesk", __name__, url_prefix="/helpdesk")

STATUSES = ["New", "Open", "Waiting", "Closed"]
PRIORITIES = ["Low", "Normal", "High", "Urgent"]
PRIORITY_RANK = "CASE t.priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END"
VIEWS = {
    "open": ("Open", "t.status != 'Closed'"),
    "new": ("New", "t.status = 'New'"),
    "waiting": ("Waiting", "t.status = 'Waiting'"),
    "closed": ("Closed", "t.status = 'Closed'"),
    "all": ("All", None),
}
SORTS = {
    "updated": "t.updated_at DESC",
    "priority": f"{PRIORITY_RANK}, t.created_at",
    "oldest": "t.created_at",
    "newest": "t.created_at DESC",
}
FIELD_NAMES = {"subject": "Subject", "requester_name": "Requester", "requester_email": "Requester email",
               "status": "Status", "priority": "Priority", "category": "Category",
               "assigned_to": "Assigned to", "location_id": "Location"}
# Looks like an asset tag: letters, a dash, digits (CB-494, AC-120, Dell-1).
TAG_RE = re.compile(r"\b[A-Za-z]{1,6}-\d{1,6}\b")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


# ---------------------------------------------------------------- helpers


def get_ticket(db, ticket_id):
    ticket = db.execute(
        "SELECT t.*, l.name AS location FROM tickets t LEFT JOIN locations l ON l.id = t.location_id "
        "WHERE t.id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        abort(404)
    return ticket


def add_message(db, ticket_id, kind, body, author=None):
    db.execute("INSERT INTO ticket_messages (ticket_id, kind, author, body) VALUES (?, ?, ?, ?)",
               (ticket_id, kind, author, body))
    db.execute("UPDATE tickets SET updated_at = datetime('now') WHERE id = ?", (ticket_id,))


def linked_devices(db, ticket_id):
    return db.execute(
        "SELECT d.*, l.name AS location FROM ticket_devices td JOIN devices d ON d.id = td.device_id "
        "LEFT JOIN locations l ON l.id = d.location_id WHERE td.ticket_id = ? "
        "ORDER BY d.asset_tag IS NULL, d.asset_tag, d.name", (ticket_id,)).fetchall()


def find_devices(db, text):
    """Devices named by asset tag in `text` ("CB-494, AC-120"). Returns (devices, unknown tags)."""
    found, unknown = [], []
    for tag in re.split(r"[\s,;]+", text or ""):
        tag = tag.strip()
        if not tag:
            continue
        row = db.execute("SELECT * FROM devices WHERE asset_tag = ?", (tag,)).fetchone()
        if row is None:
            unknown.append(tag)
        elif row["id"] not in [d["id"] for d in found]:
            found.append(row)
    return found, unknown


def link_devices(db, ticket, devices):
    """Link devices to a ticket, noting it on both the ticket and each device's history."""
    linked = 0
    for device in devices:
        cur = db.execute("INSERT OR IGNORE INTO ticket_devices (ticket_id, device_id) VALUES (?, ?)",
                         (ticket["id"], device["id"]))
        if cur.rowcount:
            linked += 1
            add_message(db, ticket["id"], "event", f"Linked {core.device_label(device)}.")
            db.execute("INSERT INTO device_history (device_id, field, new_value) VALUES (?, 'ticket', ?)",
                       (device["id"], f"Ticket #{ticket['id']} ({ticket['subject']}) linked"))
    return linked


def suggestions(db, ticket, devices):
    """Devices and a location the ticket's text mentions but that aren't linked yet."""
    text = " ".join([ticket["subject"]] + [m["body"] for m in db.execute(
        "SELECT body FROM ticket_messages WHERE ticket_id = ? AND kind IN ('description', 'incoming')",
        (ticket["id"],))])
    linked = {d["id"] for d in devices}
    found, _ = find_devices(db, " ".join(TAG_RE.findall(text)))
    device_hits = [d for d in found if d["id"] not in linked]
    location_hits = []
    if not ticket["location_id"]:
        for loc in db.execute("SELECT id, name FROM locations ORDER BY name"):
            # "Room W401" is found from "W401" in the text; other names must appear whole.
            core_name = re.sub(r"^room\s+", "", loc["name"], flags=re.I)
            if len(core_name) >= 3 and re.search(rf"(?<![\w-]){re.escape(core_name)}(?![\w-])", text, re.I):
                location_hits.append(loc)
    return device_hits, location_hits


def choice(value, choices, default):
    return value if value in choices else default


def option_value(db, kind, value):
    """A drop-down value, adding a name typed into "+ Add new…" to its list."""
    value = core.clean(value)
    if value == core.NEW_OPTION:
        return None
    return core.ensure_option(db, kind, value) if value else None


def location_from_form(db, form):
    new = core.clean(form.get("new_location"))
    if new:
        return core.get_or_create_location(db, new)
    loc = core.clean(form.get("location_id"))
    return int(loc) if loc and loc.isdigit() else None


def mail_state():
    """What the pages show about the email connection."""
    cfg = mailsync.config()
    return cfg | {"connected": mailsync.connected() and bool(cfg["mailbox"])}


def gmail_link(mailbox, gmail_id):
    return f"https://mail.google.com/mail/u/{mailbox}/#all/{gmail_id}" if mailbox and gmail_id else None


def form_lists(db):
    return {
        "technicians": core.list_options(db, "technician"),
        "ticket_categories": core.list_options(db, "ticket_category"),
        "locations": db.execute("SELECT id, name FROM locations ORDER BY name").fetchall(),
        "STATUSES_HD": STATUSES, "PRIORITIES": PRIORITIES,
    }


# ---------------------------------------------------------------- queue


@bp.route("/")
def queue():
    db = core.get_db()
    args = request.args
    view = choice(args.get("view"), VIEWS, "open")
    sort = choice(args.get("sort"), SORTS, "updated")
    where, params = [], []
    if VIEWS[view][1]:
        where.append(VIEWS[view][1])
    assigned = core.clean(args.get("assigned_to"))
    if assigned == "unassigned":
        where.append("t.assigned_to IS NULL")
    elif assigned:
        where.append("t.assigned_to = ?")
        params.append(assigned)
    for field in ("priority", "category"):
        if core.clean(args.get(field)):
            where.append(f"t.{field} = ?")
            params.append(args[field])
    if core.clean(args.get("location")):
        where.append("t.location_id = ?")
        params.append(args["location"])
    if core.clean(args.get("device")):
        where.append("EXISTS (SELECT 1 FROM ticket_devices td WHERE td.ticket_id = t.id AND td.device_id = ?)")
        params.append(args["device"])
    q = core.clean(args.get("q"))
    if q:
        like = f"%{q}%"
        number = re.fullmatch(r"#?(\d+)", q)
        where.append(
            "(t.id = ? OR t.subject LIKE ? OR t.requester_name LIKE ? OR t.requester_email LIKE ?"
            " OR t.spiceworks_number LIKE ? OR l.name LIKE ?"
            " OR EXISTS (SELECT 1 FROM ticket_messages m WHERE m.ticket_id = t.id AND m.body LIKE ?)"
            " OR EXISTS (SELECT 1 FROM ticket_devices td JOIN devices d ON d.id = td.device_id"
            "  WHERE td.ticket_id = t.id AND (d.asset_tag LIKE ? OR d.name LIKE ?)))")
        params += [int(number.group(1)) if number else -1] + [like] * 8
    tickets = db.execute(
        "SELECT t.*, l.name AS location,"
        " (SELECT GROUP_CONCAT(COALESCE(d.asset_tag, d.name), ', ') FROM ticket_devices td"
        "  JOIN devices d ON d.id = td.device_id WHERE td.ticket_id = t.id) AS devices"
        " FROM tickets t LEFT JOIN locations l ON l.id = t.location_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY {SORTS[sort]}, t.id DESC", params).fetchall()
    counts = db.execute(
        "SELECT SUM(status != 'Closed') AS open, SUM(status = 'New') AS new,"
        " SUM(status = 'Waiting') AS waiting,"
        " SUM(status != 'Closed' AND assigned_to IS NULL) AS unassigned,"
        " SUM(status != 'Closed' AND priority IN ('High', 'Urgent')) AS urgent FROM tickets").fetchone()
    return render_template("helpdesk_queue.html", tickets=tickets, counts=counts, view=view,
                           views=VIEWS, sort=sort, args=args, mail=mail_state(), **form_lists(db))


# ---------------------------------------------------------------- create


@bp.route("/new", methods=["GET", "POST"])
def new():
    db = core.get_db()
    errors = []
    if request.method == "POST":
        f = request.form
        ticket = {
            "subject": core.clean(f.get("subject")),
            "requester_name": core.clean(f.get("requester_name")),
            "requester_email": core.clean(f.get("requester_email")),
            "priority": choice(f.get("priority"), PRIORITIES, "Normal"),
            "status": choice(f.get("status"), STATUSES, "Open"),
            "category": option_value(db, "ticket_category", f.get("category")),
            "assigned_to": option_value(db, "technician", f.get("assigned_to")),
            "location_id": location_from_form(db, f),
            "description": core.clean(f.get("description")),
            "devices": core.clean(f.get("devices")) or "",
        }
        devices, unknown = find_devices(db, ticket["devices"])
        if not ticket["subject"]:
            errors.append("Subject is required.")
        if ticket["requester_email"] and not EMAIL_RE.fullmatch(ticket["requester_email"]):
            errors.append("The requester's email address doesn't look right.")
        if unknown:
            errors.append("No device has the asset tag " + ", ".join(unknown)
                          + ". Check the tag, or leave it out and link the device later.")
        if not errors:
            if not ticket["location_id"] and len({d["location_id"] for d in devices}) == 1:
                ticket["location_id"] = devices[0]["location_id"]  # the device's room
            cur = db.execute(
                "INSERT INTO tickets (subject, requester_name, requester_email, status, priority,"
                " category, assigned_to, location_id, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual')",
                [ticket[k] for k in ("subject", "requester_name", "requester_email", "status",
                                     "priority", "category", "assigned_to", "location_id")])
            ticket_id = cur.lastrowid
            if ticket["description"]:
                add_message(db, ticket_id, "description", ticket["description"],
                            ticket["requester_name"] or ticket["requester_email"])
            add_message(db, ticket_id, "event", "Ticket created.")
            link_devices(db, get_ticket(db, ticket_id), devices)
            db.commit()
            flash(f"Created ticket #{ticket_id}.", "success")
            return redirect(url_for("helpdesk.view", ticket_id=ticket_id))
        db.rollback()
    else:
        ticket = {"priority": "Normal", "status": "Open", "subject": request.args.get("subject", ""),
                  "devices": "", "location_id": None}
        device = request.args.get("device", "")
        if device.isdigit():
            row = db.execute("SELECT * FROM devices WHERE id = ?", (int(device),)).fetchone()
            if row:
                ticket["devices"] = row["asset_tag"] or ""
                ticket["location_id"] = row["location_id"]
                ticket["device_name"] = core.device_label(row)
        if request.args.get("location", "").isdigit():
            ticket["location_id"] = int(request.args["location"])
    return render_template("helpdesk_form.html", ticket=ticket, errors=errors, **form_lists(db))


# ---------------------------------------------------------------- one ticket


@bp.route("/<int:ticket_id>")
def view(ticket_id, reply_draft=None):
    db = core.get_db()
    ticket = get_ticket(db, ticket_id)
    devices = linked_devices(db, ticket_id)
    messages = db.execute("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id",
                          (ticket_id,)).fetchall()
    device_hits, location_hits = suggestions(db, ticket, devices)
    return render_template(
        "helpdesk_ticket.html", ticket=ticket, devices=devices, messages=messages,
        device_hits=device_hits, location_hits=location_hits,
        spiceworks_link=core.ticket_link(ticket["spiceworks_number"]), reply_draft=reply_draft,
        mail=mail_state(), **form_lists(db))


@bp.route("/<int:ticket_id>/update", methods=["POST"])
def update(ticket_id):
    """Change any of the ticket's fields; only fields present in the form are touched."""
    db = core.get_db()
    ticket = get_ticket(db, ticket_id)
    f = request.form
    changes = {}
    for field in ("subject", "requester_name", "requester_email"):
        if field in f:
            changes[field] = core.clean(f.get(field))
    if "status" in f:
        changes["status"] = choice(f.get("status"), STATUSES, ticket["status"])
    if "priority" in f:
        changes["priority"] = choice(f.get("priority"), PRIORITIES, ticket["priority"])
    if "category" in f:
        changes["category"] = option_value(db, "ticket_category", f.get("category"))
    if "assigned_to" in f:
        changes["assigned_to"] = option_value(db, "technician", f.get("assigned_to"))
    if "location_id" in f or "new_location" in f:
        changes["location_id"] = location_from_form(db, f)
    if "subject" in changes and not changes["subject"]:
        flash("The subject can't be blank.", "error")
        return redirect(url_for("helpdesk.view", ticket_id=ticket_id))
    if changes.get("requester_email") and not EMAIL_RE.fullmatch(changes["requester_email"]):
        flash("The requester's email address doesn't look right.", "error")
        return redirect(url_for("helpdesk.view", ticket_id=ticket_id))
    changed = {k: v for k, v in changes.items() if ticket[k] != v}
    if changed:
        sets = [f"{k} = ?" for k in changed] + ["updated_at = datetime('now')"]
        values = list(changed.values())
        if "status" in changed:
            sets.append("closed_at = " + ("datetime('now')" if changed["status"] == "Closed" else "NULL"))
        db.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", values + [ticket_id])
        for field, new_value in changed.items():
            old_value = ticket[field]
            if field == "location_id":
                old_value, new_value = ticket["location"], core.location_name(db, new_value)
            add_message(db, ticket_id, "event",
                        f"{FIELD_NAMES[field]}: {old_value or '(none)'} → {new_value or '(none)'}")
        db.commit()
        if changed.get("status") == "Closed":
            flash(f"Closed ticket #{ticket_id}.", "success")
        else:
            flash("Ticket updated.", "success")
    return redirect(url_for("helpdesk.view", ticket_id=ticket_id))


@bp.route("/<int:ticket_id>/note", methods=["POST"])
def note(ticket_id):
    db = core.get_db()
    ticket = get_ticket(db, ticket_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Type a note first.", "error")
        return redirect(url_for("helpdesk.view", ticket_id=ticket_id) + "#add-note")
    author = option_value(db, "technician", request.form.get("author"))
    add_message(db, ticket_id, "note", body, author)
    if request.form.get("close") and ticket["status"] != "Closed":
        db.execute("UPDATE tickets SET status = 'Closed', closed_at = datetime('now') WHERE id = ?",
                   (ticket_id,))
        add_message(db, ticket_id, "event", f"Status: {ticket['status']} → Closed")
    elif ticket["status"] == "New":
        # Someone is working on it now.
        db.execute("UPDATE tickets SET status = 'Open' WHERE id = ?", (ticket_id,))
        add_message(db, ticket_id, "event", "Status: New → Open")
    db.commit()
    flash("Note added." + (" Ticket closed." if request.form.get("close") else ""), "success")
    return redirect(url_for("helpdesk.view", ticket_id=ticket_id) + "#add-note")


@bp.route("/<int:ticket_id>/devices", methods=["POST"])
def add_devices(ticket_id):
    db = core.get_db()
    ticket = get_ticket(db, ticket_id)
    devices, unknown = find_devices(db, request.form.get("tags"))
    if unknown:
        flash("No device has the asset tag " + ", ".join(unknown) + ".", "error")
    if devices:
        linked = link_devices(db, ticket, devices)
        if not ticket["location_id"] and len({d["location_id"] for d in devices}) == 1 \
                and devices[0]["location_id"]:
            db.execute("UPDATE tickets SET location_id = ? WHERE id = ?", (devices[0]["location_id"], ticket_id))
            add_message(db, ticket_id, "event",
                        f"Location: (none) → {core.location_name(db, devices[0]['location_id'])}")
        db.commit()
        if linked:
            flash(f"Linked {', '.join(core.device_label(d) for d in devices)}.", "success")
    return redirect(url_for("helpdesk.view", ticket_id=ticket_id) + "#devices")


@bp.route("/<int:ticket_id>/devices/<int:device_id>/remove", methods=["POST"])
def remove_device(ticket_id, device_id):
    db = core.get_db()
    ticket = get_ticket(db, ticket_id)
    device = db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    cur = db.execute("DELETE FROM ticket_devices WHERE ticket_id = ? AND device_id = ?", (ticket_id, device_id))
    if cur.rowcount and device:
        add_message(db, ticket_id, "event", f"Unlinked {core.device_label(device)}.")
        db.execute("INSERT INTO device_history (device_id, field, new_value) VALUES (?, 'ticket', ?)",
                   (device_id, f"Ticket #{ticket_id} ({ticket['subject']}) unlinked"))
        db.commit()
    return redirect(url_for("helpdesk.view", ticket_id=ticket_id) + "#devices")


@bp.route("/<int:ticket_id>/delete", methods=["POST"])
def delete(ticket_id):
    db = core.get_db()
    get_ticket(db, ticket_id)
    db.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    db.commit()
    flash(f"Deleted ticket #{ticket_id}.", "success")
    return redirect(url_for("helpdesk.queue"))


# ---------------------------------------------------------------- email


@bp.route("/<int:ticket_id>/reply", methods=["POST"])
def reply(ticket_id):
    """Email the requester from the help desk mailbox and record it on the ticket."""
    db = core.get_db()
    ticket = get_ticket(db, ticket_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Type a reply first.", "error")
        return redirect(url_for("helpdesk.view", ticket_id=ticket_id) + "#reply")
    mail = mail_state()
    if not mail["connected"]:
        flash("Email isn't connected yet, so replies can't be sent. Add it as a note instead.", "error")
        return view(ticket_id, reply_draft=body)
    author = option_value(db, "technician", request.form.get("author"))
    signature = f"\n\n{author}\n{mail['mail_from_name']}" if author else f"\n\n{mail['mail_from_name']}"
    try:
        mailsync.send_on_ticket(mailsync.get_client(), db, ticket_id, body + signature.rstrip(),
                                mail, mail["mailbox"], "reply", author)
    except gmail_api.GmailError as e:
        db.rollback()
        flash(f"The reply wasn't sent: {e}", "error")
        return view(ticket_id, reply_draft=body)
    then = request.form.get("then")
    new_status = then if then in ("Waiting", "Closed") else ("Open" if ticket["status"] == "New" else None)
    if new_status and new_status != ticket["status"]:
        db.execute("UPDATE tickets SET status = ?, closed_at = " +
                   ("datetime('now')" if new_status == "Closed" else "NULL") + " WHERE id = ?",
                   (new_status, ticket_id))
        add_message(db, ticket_id, "event", f"Status: {ticket['status']} → {new_status}")
    db.commit()
    flash(f"Reply sent to {ticket['requester_email']}.", "success")
    return redirect(url_for("helpdesk.view", ticket_id=ticket_id) + "#reply")


@bp.route("/email/check", methods=["POST"])
def email_check():
    counts, error = mailsync.check_now()
    if error:
        flash(f"Couldn't check email: {error}", "error")
    else:
        new, replies = counts["new"], counts["replies"]
        flash(f"Checked email: {new} new ticket{'s' if new != 1 else ''}, "
              f"{replies} repl{'y' if replies == 1 else 'ies'} added"
              + (f", {counts['skipped']} skipped" if counts["skipped"] else "") + ".", "success")
    return redirect(request.form.get("next") or url_for("helpdesk.queue"))


def redirect_uri():
    """Where Google sends the browser back to after signing in: this computer, this port."""
    return f"http://{request.host}/"


@bp.route("/email", methods=["GET", "POST"])
def email_setup():
    path = current_app.config["SETTINGS_PATH"]
    data_dir = mailsync.data_dir()
    if request.method == "POST":
        f = request.form
        changes = {"mail_enabled": bool(f.get("mail_enabled")),
                   "mail_auto_reply": bool(f.get("mail_auto_reply")),
                   "mail_from_name": core.clean(f.get("mail_from_name")) or "IT Help Desk",
                   # blank = accept anyone (saved as "", not removed, so it doesn't fall back to the default)
                   "mail_allowed_domain": (core.clean(f.get("mail_allowed_domain")) or "").lstrip("@").lower()}
        try:
            changes["mail_interval"] = min(60, max(1, int(f.get("mail_interval", 2))))
        except ValueError:
            pass
        mailbox = (core.clean(f.get("mail_mailbox")) or "").lower()
        if mailbox and not EMAIL_RE.fullmatch(mailbox):
            flash("The mailbox address doesn't look right.", "error")
            return redirect(url_for("helpdesk.email_setup"))
        old = app_settings.load(path).get("mail_mailbox")
        if mailbox and mailbox != old:
            changes["mail_mailbox"] = mailbox
            secrets_data = gmail_api.load_secrets(data_dir)
            if secrets_data.pop("refresh_token", None):  # connected to a different mailbox
                gmail_api.save_secrets(data_dir, secrets_data)
                flash(f"Mailbox changed to {mailbox}. Click Connect mailbox to sign in to it.", "warn")
        upload = request.files.get("client_file")
        if upload and upload.filename:
            try:
                client = gmail_api.read_client_file(upload.read().decode("utf-8", "replace"))
            except gmail_api.GmailError as e:
                flash(str(e), "error")
                return redirect(url_for("helpdesk.email_setup"))
            gmail_api.save_secrets(data_dir, client)  # a new client needs a new sign-in
        app_settings.update(path, **changes)
        flash("Email settings saved.", "success")
        return redirect(url_for("helpdesk.email_setup"))
    secrets_data = gmail_api.load_secrets(data_dir)
    return render_template("helpdesk_email.html", mail=mail_state(),
                           has_client=bool(secrets_data.get("client_id")),
                           connected_as=secrets_data.get("connected_email"), redirect_uri=redirect_uri())


@bp.route("/email/connect", methods=["POST"])
def email_connect():
    data_dir = mailsync.data_dir()
    client = gmail_api.load_secrets(data_dir)
    mailbox = mailsync.config()["mailbox"]
    if not client.get("client_id"):
        flash("Upload the OAuth client file from Google Cloud first (step 4).", "error")
        return redirect(url_for("helpdesk.email_setup"))
    if not mailbox:
        flash("Enter the help desk mailbox address and save first.", "error")
        return redirect(url_for("helpdesk.email_setup"))
    if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        flash("Connect the mailbox from the computer running Device Inventory, at http://127.0.0.1.", "error")
        return redirect(url_for("helpdesk.email_setup"))
    verifier, challenge = gmail_api.pkce_pair()
    state = secrets.token_urlsafe(24)
    session["gmail_oauth"] = {"state": state, "verifier": verifier, "redirect_uri": redirect_uri()}
    return redirect(gmail_api.auth_url(client["client_id"], redirect_uri(), state, challenge, mailbox))


@bp.route("/email/oauth")
def email_oauth():
    pending = session.pop("gmail_oauth", None)
    if not pending or request.args.get("state") != pending["state"]:
        flash("That sign-in link has expired. Click Connect mailbox again.", "error")
        return redirect(url_for("helpdesk.email_setup"))
    if request.args.get("error"):
        flash(f"Google sign-in was cancelled or refused ({request.args['error']}).", "error")
        return redirect(url_for("helpdesk.email_setup"))
    data_dir = mailsync.data_dir()
    stored = gmail_api.load_secrets(data_dir)
    mailbox = mailsync.config()["mailbox"]
    try:
        tokens = gmail_api.exchange_code(stored, request.args.get("code", ""), pending["redirect_uri"],
                                         pending["verifier"])
        client = gmail_api.GmailClient(stored["client_id"], stored["client_secret"], tokens["refresh_token"])
        address = client.address().lower()
    except gmail_api.GmailError as e:
        flash(f"Couldn't connect: {e}", "error")
        return redirect(url_for("helpdesk.email_setup"))
    if address != mailbox:
        flash(f"You signed in as {address}, but the help desk mailbox is set to {mailbox}. "
              "Click Connect mailbox again and choose the help desk account.", "error")
        return redirect(url_for("helpdesk.email_setup"))
    gmail_api.save_secrets(data_dir, stored | {"refresh_token": tokens["refresh_token"],
                                               "connected_email": address})
    flash(f"Connected to {address}. Turn on \"Check this mailbox\" below, then send it a test email.", "success")
    return redirect(url_for("helpdesk.email_setup"))


@bp.route("/email/disconnect", methods=["POST"])
def email_disconnect():
    data_dir = mailsync.data_dir()
    stored = gmail_api.load_secrets(data_dir)
    stored.pop("refresh_token", None)
    stored.pop("connected_email", None)
    gmail_api.save_secrets(data_dir, stored)
    app_settings.update(current_app.config["SETTINGS_PATH"], mail_enabled=False)
    flash("Disconnected. Email is no longer checked or sent.", "success")
    return redirect(url_for("helpdesk.email_setup"))


@bp.before_app_request
def finish_google_sign_in():
    """Google returns to http://127.0.0.1:<port>/?state=…&code=…; pass it to email_oauth."""
    if request.path == "/" and "state" in request.args and ("code" in request.args or "error" in request.args):
        return redirect(url_for("helpdesk.email_oauth", **request.args))
    return None
