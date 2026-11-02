"""The alert light in the IT office (a relay on a Raspberry Pi; see pi/helpdesk_light.py).

A ticket "alerts" when something happens that the team chose to be told about: a new ticket
by email, a requester's reply, or a ticket becoming High or Urgent. The light is on while any
open ticket has an alert newer than the last time someone pressed the button (Silence);
opening the ticket in Device Inventory can also clear its alert.

The Pi reads /helpdesk/light/status and posts to /helpdesk/light/ack. From another computer,
those two need the light key (when "Allow phones and other computers" is off).
"""

import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app

import app_settings

DEFAULTS = {"light_new": True, "light_replies": False, "light_urgent": True, "light_clear_on_open": True}
REASONS = {"new": "New ticket", "reply": "Requester replied", "urgent": "High or urgent ticket",
           "raised": "Set to high or urgent", "test": "Test"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def config():
    data = app_settings.load(current_app.config["SETTINGS_PATH"])
    return {k: data.get(k, v) for k, v in DEFAULTS.items()} | {
        "ack_at": data.get("light_ack_at"), "test_until": data.get("light_test_until"),
        "key": data.get("light_key")}


def key():
    """The secret the Pi uses from another computer; made the first time it's needed."""
    current = config()["key"]
    if not current:
        current = secrets.token_urlsafe(24)
        app_settings.update(current_app.config["SETTINGS_PATH"], light_key=current)
    return current


def new_key():
    app_settings.update(current_app.config["SETTINGS_PATH"], light_key=secrets.token_urlsafe(24))


def raise_alert(db, ticket_id, *reasons):
    """Note that something happened on a ticket that may turn the light on.

    `reasons` are among "new", "reply", "urgent" (from email) and "raised" (someone in IT made the
    ticket High or Urgent); the light turns on if any of them is switched on.
    """
    cfg = config()
    wanted = {"new": cfg["light_new"], "reply": cfg["light_replies"], "urgent": cfg["light_urgent"],
              "raised": cfg["light_urgent"]}
    chosen = [r for r in ("raised", "urgent", "new", "reply") if r in reasons and wanted[r]]
    if chosen:
        db.execute("UPDATE tickets SET alerted_at = ?, alert_reason = ? WHERE id = ?",
                   (_now(), chosen[0], ticket_id))


def seen(db, ticket_id):
    """Someone opened the ticket: its alert no longer needs the light (if that's switched on).

    Alerts IT raised on purpose ("raised") stay until the button is pressed or the ticket is closed;
    whoever raised it is looking at the ticket at that moment.
    """
    if config()["light_clear_on_open"]:
        db.execute("UPDATE tickets SET alerted_at = NULL WHERE id = ? AND alerted_at IS NOT NULL"
                   " AND alert_reason IS NOT 'raised'", (ticket_id,))


def acknowledge():
    """The button: silence everything up to now."""
    app_settings.update(current_app.config["SETTINGS_PATH"], light_ack_at=_now(), light_test_until=None)


def test(seconds=15):
    until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    app_settings.update(current_app.config["SETTINGS_PATH"], light_test_until=until)


def state(db):
    """What the light should do, and why."""
    cfg = config()
    rows = db.execute(
        "SELECT id, subject, alert_reason, alerted_at, priority FROM tickets"
        " WHERE alerted_at IS NOT NULL AND status != 'Closed' AND (? IS NULL OR alerted_at > ?)"
        " ORDER BY alerted_at DESC", (cfg["ack_at"], cfg["ack_at"])).fetchall()
    counts = db.execute(
        "SELECT SUM(unread = 1) AS unread, SUM(status = 'New') AS new,"
        " SUM(status != 'Closed' AND priority IN ('High', 'Urgent')) AS urgent FROM tickets").fetchone()
    testing = bool(cfg["test_until"] and cfg["test_until"] > _now())
    latest = rows[0] if rows else None
    reason = "test" if testing and not latest else (latest["alert_reason"] if latest else None)
    return {
        "light": bool(rows) or testing,
        "reason": reason,
        "reason_text": REASONS.get(reason) if reason else None,
        "alerts": len(rows),
        "ticket": {"id": latest["id"], "subject": latest["subject"], "priority": latest["priority"]} if latest else None,
        "unread": counts["unread"] or 0, "new": counts["new"] or 0, "urgent_open": counts["urgent"] or 0,
        "silenced_at": cfg["ack_at"],
    }
