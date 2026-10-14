import os
import re
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")}).test_client()


def add_device(client, tag="CB-494", **extra):
    data = {"asset_tag": tag, "name": "Chromebook", "category": "Chromebook", "status": "In Service",
            "manufacturer": "Acer", "model": "Chromebook 311", "serial_number": "NXH8VAA006",
            "new_location": "Tech Office", "assigned_to": "Ada Lovelace"}
    data.update(extra)
    resp = client.post("/devices/new", data=data)
    assert resp.status_code == 302
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def test_log_close_reopen_and_remove_ticket(client):
    device_id = add_device(client)
    resp = client.post(f"/devices/{device_id}/tickets",
                       data={"ticket_number": "#1234", "opened_on": "2026-10-01", "summary": "Cracked screen"},
                       follow_redirects=True)
    page = resp.data.decode()
    assert "Ticket logged." in page and ">1234<" in page and "Cracked screen" in page
    assert "Ticket 1234 (Cracked screen) opened" in page  # history

    listing = client.get("/").data.decode()
    assert "1 open" in listing and "With open tickets" in listing
    assert "CB-494" in client.get("/?tickets=open").data.decode()
    assert "CB-494" in client.get("/?q=cracked").data.decode()
    assert "CB-494" in client.get("/?q=1234").data.decode()

    ticket_id = int(re.search(r"/tickets/(\d+)/status", page).group(1))
    page = client.post(f"/tickets/{ticket_id}/status", follow_redirects=True).data.decode()
    assert "Reopen" in page and "closed" in page
    assert "CB-494" not in client.get("/?tickets=open").data.decode()
    page = client.post(f"/tickets/{ticket_id}/status", follow_redirects=True).data.decode()
    assert "reopened" in page

    page = client.post(f"/tickets/{ticket_id}/delete", follow_redirects=True).data.decode()
    assert "Ticket removed." in page and "/status" not in page


def test_ticket_needs_number_or_summary_and_valid_date(client):
    device_id = add_device(client)
    page = client.post(f"/devices/{device_id}/tickets", data={"opened_on": "2026-10-01"},
                       follow_redirects=True).data.decode()
    assert "Enter a ticket number or a short summary." in page
    page = client.post(f"/devices/{device_id}/tickets", data={"summary": "x", "opened_on": "10/01"},
                       follow_redirects=True).data.decode()
    assert "isn&#39;t a valid date" in page
    assert client.post("/devices/999/tickets", data={"summary": "x"}).status_code == 404


def test_tickets_deleted_with_device(client):
    device_id = add_device(client)
    client.post(f"/devices/{device_id}/tickets", data={"summary": "Won't charge"})
    client.post(f"/devices/{device_id}/delete")
    import sqlite3
    db = sqlite3.connect(client.application.config["DATABASE"])
    assert db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_report_problem_email_and_ticket_links(client):
    device_id = add_device(client)
    page = client.get(f"/devices/{device_id}").data.decode()
    assert "Report a problem" not in page.split("<h2")[0]  # no address set yet

    resp = client.post("/settings", data={"helpdesk_email": "help@school.on.spiceworks.com",
                                          "ticket_url": "https://on.spiceworks.com/tickets/open/{number}"},
                       follow_redirects=True)
    assert b"Settings saved" in resp.data

    client.post(f"/devices/{device_id}/tickets", data={"ticket_number": "77", "summary": "Hinge"})
    page = client.get(f"/devices/{device_id}").data.decode()
    href = re.search(r'href="(mailto:[^"]+)"', page).group(1).replace("&amp;", "&")
    parsed = urllib.parse.urlsplit(href)
    assert parsed.path == "help@school.on.spiceworks.com"
    q = urllib.parse.parse_qs(parsed.query)
    assert q["subject"] == ["Problem with CB-494 (Chromebook)"]
    body = q["body"][0]
    for text in ("Asset tag: CB-494", "Make / model: Acer Chromebook 311", "Serial number: NXH8VAA006",
                 "Location: Tech Office", "Assigned to: Ada Lovelace"):
        assert text in body
    assert "#" not in body  # Spiceworks reads "#word" as an email command
    assert 'href="https://on.spiceworks.com/tickets/open/77"' in page


def test_helpdesk_settings_validation_and_kept_when_absent(client):
    page = client.post("/settings", data={"helpdesk_email": "not-an-email"}, follow_redirects=True).data
    assert b"doesn&#39;t look right" in page
    page = client.post("/settings", data={"ticket_url": "https://x/tickets/1"}, follow_redirects=True).data
    assert b"{number}" in page and b"Settings saved" not in page
    client.post("/settings", data={"helpdesk_email": "help@x.org", "ticket_url": ""})
    client.post("/settings", data={"allow_network": "1"})  # a form without these fields keeps them
    assert b'value="help@x.org"' in client.get("/settings").data
