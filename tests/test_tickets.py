import os
import re
import sqlite3
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")}).test_client()


def text(resp):
    return resp.data.decode()


def add_device(client, tag="CB-494", location="Tech Office", **extra):
    data = {"asset_tag": tag, "name": "Chromebook", "category": "Chromebook", "status": "In Service",
            "manufacturer": "Acer", "model": "Chromebook 311", "serial_number": "NXH8VAA006",
            "new_location": location, "assigned_to": "Ada Lovelace"}
    data.update(extra)
    resp = client.post("/devices/new", data=data)
    assert resp.status_code == 302
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def new_ticket(client, **extra):
    data = {"subject": "Screen cracked", "description": "Dropped it in the hallway.",
            "requester_name": "Grace Hopper", "requester_email": "ghopper@thehudsonschool.org",
            "priority": "High", "status": "New", "devices": "cb-494"}
    data.update(extra)
    resp = client.post("/helpdesk/new", data=data)
    assert resp.status_code == 302, text(resp)
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def db_for(client):
    return sqlite3.connect(client.application.config["DATABASE"])


# ------------------------------------------------------------------ creating


def test_create_ticket_links_device_and_takes_its_location(client):
    device_id = add_device(client)
    ticket_id = new_ticket(client)
    page = text(client.get(f"/helpdesk/{ticket_id}"))
    for expected in ("#1</span> Screen cracked", "Dropped it in the hallway.", "Grace Hopper",
                     "Linked CB-494", "Tech Office", "High", "Ticket created."):
        assert expected in page
    # Shown on the device, in its history, in the devices list, in the queue and the nav count.
    device_page = text(client.get(f"/devices/{device_id}"))
    assert f"/helpdesk/{ticket_id}" in device_page and "Ticket #1 (Screen cracked) linked" in device_page
    assert "1 open" in text(client.get("/"))
    queue = text(client.get("/helpdesk/"))
    assert "Screen cracked" in queue and 'class="nav-count">1<' in queue
    assert "CB-494" in text(client.get("/?tickets=open"))


def test_new_ticket_form_prefilled_from_device(client):
    device_id = add_device(client)
    page = text(client.get(f"/helpdesk/new?device={device_id}"))
    assert 'value="CB-494"' in page and "for CB-494" in page


def test_create_ticket_validation(client):
    page = text(client.post("/helpdesk/new", data={"subject": "", "devices": "ZZ-1"}))
    assert "Subject is required." in page and "No device has the asset tag ZZ-1" in page
    page = text(client.post("/helpdesk/new", data={"subject": "x", "requester_email": "nope"}))
    assert "doesn&#39;t look right" in page
    assert db_for(client).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_new_technician_and_category_added_to_lists(client):
    new_ticket(client, devices="", assigned_to="__new__")  # "+ Add new…" cancelled -> unassigned
    ticket_id = new_ticket(client, devices="", assigned_to="Rafael", category="Smartboard")
    assert db_for(client).execute("SELECT assigned_to, category FROM tickets WHERE id = ?",
                                  (ticket_id,)).fetchone() == ("Rafael", "Smartboard")
    lists = text(client.get("/lists"))
    assert "Rafael" in lists and "Smartboard" in lists and "Ticket categories" in lists


# ------------------------------------------------------------------ working on a ticket


def test_update_logs_changes_and_close_reopen(client):
    add_device(client)
    ticket_id = new_ticket(client)
    page = text(client.post(f"/helpdesk/{ticket_id}/update",
                            data={"status": "Waiting", "priority": "Urgent", "assigned_to": "Rafael"},
                            follow_redirects=True))
    for expected in ("Status: New → Waiting", "Priority: High → Urgent", "Assigned to: (none) → Rafael"):
        assert expected in page
    page = text(client.post(f"/helpdesk/{ticket_id}/update", data={"status": "Closed"}, follow_redirects=True))
    assert "Closed ticket #1." in page and "Reopen" in page
    assert db_for(client).execute("SELECT closed_at IS NOT NULL FROM tickets").fetchone() == (1,)
    assert "Screen cracked" not in text(client.get("/helpdesk/"))
    assert "Screen cracked" in text(client.get("/helpdesk/?view=closed"))
    client.post(f"/helpdesk/{ticket_id}/update", data={"status": "Open"})
    assert db_for(client).execute("SELECT status, closed_at FROM tickets").fetchone() == ("Open", None)
    # Fields left out of the form are kept.
    assert db_for(client).execute("SELECT priority, assigned_to FROM tickets").fetchone() == ("Urgent", "Rafael")


def test_note_opens_new_ticket_and_can_close(client):
    add_device(client)
    ticket_id = new_ticket(client)
    page = text(client.post(f"/helpdesk/{ticket_id}/note", data={"body": "Ordered a new screen.",
                                                                 "author": "Rafael"}, follow_redirects=True))
    assert "Ordered a new screen." in page and "internal note" in page and "Status: New → Open" in page
    page = text(client.post(f"/helpdesk/{ticket_id}/note", data={"body": "Replaced.", "close": "1"},
                            follow_redirects=True))
    assert "Ticket closed." in page and "Status: Open → Closed" in page
    assert "Type a note first." in text(client.post(f"/helpdesk/{ticket_id}/note", data={"body": " "},
                                                    follow_redirects=True))


def test_mentioned_devices_link_automatically_and_can_be_unlinked(client):
    add_device(client)
    add_device(client, tag="AC-120", name="Desktop", location="Room W401")
    ticket_id = new_ticket(client, devices="", subject="Desktop AC-120 in W401 is slow",
                           description="Also CB-494 won't charge")
    page = text(client.get(f"/helpdesk/{ticket_id}"))
    assert "Linked AC-120 (mentioned in the ticket)." in page and "Linked CB-494 (mentioned in the ticket)." in page
    # The two devices are in different rooms, so the location is only suggested.
    assert "Set location Room W401" in page and "Link AC-120" not in page
    page = text(client.post(f"/helpdesk/{ticket_id}/devices", data={"tags": "nope-9"}, follow_redirects=True))
    assert "No device has the asset tag nope-9" in page
    ac_id = db_for(client).execute("SELECT id FROM devices WHERE asset_tag = 'AC-120'").fetchone()[0]
    page = text(client.post(f"/helpdesk/{ticket_id}/devices/{ac_id}/remove", follow_redirects=True))
    assert "Unlinked AC-120" in page and "Link AC-120" in page  # offered again, not forced back
    assert "Ticket #1 (Desktop AC-120 in W401 is slow) unlinked" in text(client.get(f"/devices/{ac_id}"))


def test_loose_tag_spellings_and_serial_numbers(client):
    add_device(client, tag="CB-494", serial_number="NXH8VAA006021066B77600")
    add_device(client, tag="AC-092", location="Room W403")
    add_device(client, tag="CB-500", serial_number="PF5KAJRZ")
    ticket_id = new_ticket(client, devices="", subject="cb494 and AC 92 broken",
                           description="Serial on the third one is pf5kajrz. Also ZZ-1 and room 204.")
    linked = {r[0] for r in db_for(client).execute(
        "SELECT d.asset_tag FROM ticket_devices td JOIN devices d ON d.id = td.device_id WHERE td.ticket_id = ?",
        (ticket_id,))}
    assert linked == {"CB-494", "AC-092", "CB-500"}
    # The tag box accepts the same loose spellings.
    other = new_ticket(client, subject="Typed tags", description="", devices="ac-92, cb 500")
    assert db_for(client).execute("SELECT COUNT(*) FROM ticket_devices WHERE ticket_id = ?",
                                  (other,)).fetchone()[0] == 2


def test_ambiguous_tags_are_not_guessed(client):
    add_device(client, tag="AC-92")
    add_device(client, tag="AC-092")
    ticket_id = new_ticket(client, devices="", subject="AC-092 again", description="")
    page = text(client.get(f"/helpdesk/{ticket_id}"))
    # The exact tag still works in the tag box; the free text doesn't pick one of the two.
    assert "Linked" not in page
    client.post(f"/helpdesk/{ticket_id}/devices", data={"tags": "AC-092"})
    assert "Linked AC-092." in text(client.get(f"/helpdesk/{ticket_id}"))


def test_location_set_from_single_room_and_subject_edit_links(client):
    add_device(client, tag="CB-494", location="Room W401")
    ticket_id = new_ticket(client, devices="", subject="Chromebook won't charge", description="")
    assert "Linked" not in text(client.get(f"/helpdesk/{ticket_id}"))
    page = text(client.post(f"/helpdesk/{ticket_id}/update", data={"subject": "CB-494 won't charge"},
                            follow_redirects=True))
    assert "Linked CB-494 (mentioned in the subject)." in page
    assert "Location: (none) → Room W401 (mentioned in the subject)" in page


def test_queue_filters_and_search(client):
    add_device(client)
    add_device(client, tag="AC-120", location="Room W401")
    first = new_ticket(client)
    second = new_ticket(client, subject="Printer jam", devices="AC-120", priority="Low", status="Open",
                        description="Paper stuck in tray 2", assigned_to="Rafael")
    w401 = db_for(client).execute("SELECT id FROM locations WHERE name = 'Room W401'").fetchone()[0]
    def subjects(query):
        page = text(client.get("/helpdesk/" + query))
        return [s for s in ("Screen cracked", "Printer jam") if s in page]
    assert subjects("") == ["Screen cracked", "Printer jam"]
    assert subjects("?view=new") == ["Screen cracked"]
    assert subjects("?assigned_to=unassigned") == ["Screen cracked"]
    assert subjects("?assigned_to=Rafael") == ["Printer jam"]
    assert subjects(f"?location={w401}") == ["Printer jam"]
    assert subjects(f"?q=%23{second}") == ["Printer jam"]
    assert subjects("?q=tray") == ["Printer jam"]          # message text
    assert subjects("?q=cb-494") == ["Screen cracked"]     # asset tag
    assert subjects("?q=hopper") == ["Screen cracked", "Printer jam"]
    assert subjects("?priority=Low") == ["Printer jam"]
    assert first != second
    locations = text(client.get("/locations"))
    assert f"/helpdesk/?location={w401}" in locations


def test_deleting_device_keeps_ticket_and_deleting_ticket(client):
    device_id = add_device(client)
    ticket_id = new_ticket(client)
    client.post(f"/devices/{device_id}/delete")
    assert "Screen cracked" in text(client.get(f"/helpdesk/{ticket_id}"))
    assert db_for(client).execute("SELECT COUNT(*) FROM ticket_devices").fetchone()[0] == 0
    page = text(client.post(f"/helpdesk/{ticket_id}/delete", follow_redirects=True))
    assert "Deleted ticket #1." in page
    assert client.get(f"/helpdesk/{ticket_id}").status_code == 404


def test_lists_rename_technician_updates_tickets_and_blocks_removal(client):
    ticket_id = new_ticket(client, devices="", assigned_to="Raf")
    db = db_for(client)
    option_id = db.execute("SELECT id FROM options WHERE kind = 'technician' AND name = 'Raf'").fetchone()[0]
    page = text(client.post(f"/lists/option/{option_id}/rename", data={"name": "Rafael"}, follow_redirects=True))
    assert "on 1 ticket(s)" in page
    assert db_for(client).execute("SELECT assigned_to FROM tickets WHERE id = ?", (ticket_id,)).fetchone() == ("Rafael",)
    page = text(client.post(f"/lists/option/{option_id}/delete", follow_redirects=True))
    assert "1 ticket(s) use it" in page


# ------------------------------------------------------------------ old data and settings


def test_old_device_ticket_log_becomes_help_desk_tickets(tmp_path):
    path = tmp_path / "old.db"
    create_app({"TESTING": True, "DATABASE": str(path)})  # current schema, then fake the 1.7 table
    db = sqlite3.connect(path)
    db.executescript("""
        DROP TABLE ticket_messages; DROP TABLE ticket_devices; DROP TABLE tickets;
        CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE, ticket_number TEXT,
            opened_on TEXT NOT NULL, summary TEXT, status TEXT NOT NULL DEFAULT 'Open', closed_on TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE INDEX idx_tickets_device ON tickets(device_id);
        INSERT INTO locations (name) VALUES ('Tech Office');
        INSERT INTO devices (asset_tag, name, location_id) VALUES ('CB-494', 'Chromebook', 1);
        INSERT INTO tickets (device_id, ticket_number, opened_on, summary, status, closed_on)
            VALUES (1, '1042', '2026-10-01', 'Keyboard keys missing', 'Closed', '2026-10-03');
        INSERT INTO tickets (device_id, ticket_number, opened_on) VALUES (1, '998', '2026-09-12');
    """)
    db.commit()
    db.close()
    client = create_app({"TESTING": True, "DATABASE": str(path)}).test_client()
    client.post("/settings", data={"ticket_url": "https://on.spiceworks.com/tickets/open/{number}"})
    rows = db_for(client).execute(
        "SELECT subject, status, spiceworks_number, location_id, closed_at, created_at FROM tickets ORDER BY id").fetchall()
    assert rows == [("Keyboard keys missing", "Closed", "1042", 1, "2026-10-03 00:00:00", "2026-10-01 00:00:00"),
                    ("Spiceworks ticket 998", "Open", "998", 1, None, "2026-09-12 00:00:00")]
    page = text(client.get("/helpdesk/1"))
    assert 'href="https://on.spiceworks.com/tickets/open/1042"' in page and "CB-494" in page
    assert "Keyboard keys missing" in text(client.get("/devices/1"))
    assert db_for(client).execute("SELECT name FROM sqlite_master WHERE name = 'tickets_v1'").fetchone() is None


def test_report_problem_email(client):
    device_id = add_device(client)
    assert "Report a problem" not in text(client.get(f"/devices/{device_id}")).split("<h2")[0]
    client.post("/settings", data={"helpdesk_email": "helpdesk@thehudsonschool.org"})
    page = text(client.get(f"/devices/{device_id}"))
    href = re.search(r'href="(mailto:[^"]+)"', page).group(1).replace("&amp;", "&")
    parsed = urllib.parse.urlsplit(href)
    assert parsed.path == "helpdesk@thehudsonschool.org"
    q = urllib.parse.parse_qs(parsed.query)
    assert q["subject"] == ["Problem with CB-494 (Chromebook)"]
    for expected in ("Asset tag: CB-494", "Make / model: Acer Chromebook 311", "Location: Tech Office"):
        assert expected in q["body"][0]
    assert "#" not in q["body"][0]


def test_helpdesk_settings_validation_and_kept_when_absent(client):
    page = client.post("/settings", data={"helpdesk_email": "not-an-email"}, follow_redirects=True).data
    assert b"doesn&#39;t look right" in page
    page = client.post("/settings", data={"ticket_url": "https://x/tickets/1"}, follow_redirects=True).data
    assert b"{number}" in page and b"Settings saved" not in page
    client.post("/settings", data={"helpdesk_email": "help@x.org", "ticket_url": ""})
    client.post("/settings", data={"allow_network": "1"})
    assert b'value="help@x.org"' in client.get("/settings").data
