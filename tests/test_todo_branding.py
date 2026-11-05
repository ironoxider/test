"""Version 2.6: the editable automatic reply, Hudson School branding, and Todoist on the board."""

import email
import email.policy
import io
import json
import os
import sys
import zipfile
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app_settings  # noqa: E402
import mailsync  # noqa: E402
import todoist  # noqa: E402
from test_email import FakeGmail, MAILBOX, check, make_email, text  # noqa: E402
import gmail_api  # noqa: E402
from app import create_app  # noqa: E402

PHONE = {"REMOTE_ADDR": "192.0.2.50"}


@pytest.fixture
def app(tmp_path):
    todoist.forget()
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def gmail(app, monkeypatch):
    fake = FakeGmail()
    gmail_api.save_secrets(os.path.dirname(app.config["DATABASE"]),
                           {"client_id": "i", "client_secret": "s", "refresh_token": "r"})
    app_settings.update(app.config["SETTINGS_PATH"], mail_mailbox=MAILBOX, mail_enabled=True)
    monkeypatch.setattr(mailsync, "get_client", lambda: fake)
    return fake


def sent_body(raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return msg, msg.get_body(("plain",)).get_content()


# ------------------------------------------------------------------ automatic reply


def test_standard_auto_reply_signed_with_school(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    msg, body = sent_body(gmail.sent[0][0])
    assert msg["Subject"] == "Re: [Ticket #1] Projector in W401 won't turn on"
    assert body.startswith("Hi Grace,")
    assert "opened ticket #1: Projector in W401 won't turn on" in body
    assert body.rstrip().endswith("IT Help Desk\nThe Hudson School")


def test_custom_auto_reply_fills_placeholders(app, client, gmail):
    template = ("Hello {first_name} ({email}),\n\nTicket {ticket} is about \"{subject}\". "
                "We'll reply by tomorrow. {unknown} stays as typed.\n\n{team}, {school}")
    resp = client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "mail_enabled": "1", "mail_auto_reply": "1",
                                                "mail_from_name": "Hudson IT", "mail_ack_template": template})
    assert resp.status_code == 302
    gmail.deliver(make_email(sender="Ada Lovelace <alovelace@thehudsonschool.org>"))
    check(client)
    _, body = sent_body(gmail.sent[0][0])
    assert body.startswith("Hello Ada (alovelace@thehudsonschool.org),")
    assert 'Ticket 1 is about "Projector in W401 won\'t turn on"' in body
    assert "{unknown} stays as typed." in body
    assert "Hudson IT, The Hudson School" in body
    # The ticket keeps a copy of what was sent.
    page = text(client.get("/helpdesk/1"))
    assert "We&#39;ll reply by tomorrow." in page or "We'll reply by tomorrow." in page


def test_auto_reply_without_a_name_and_reset(app, client):
    values = mailsync.ack_values(7, "Wifi", None, "x@thehudsonschool.org", {"mail_from_name": ""})
    assert mailsync.ack_text("", values).startswith("Hi there,")
    assert values["name"] == "x@thehudsonschool.org" and values["team"] == "IT Help Desk"
    path = app.config["SETTINGS_PATH"]
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "mail_ack_template": "Got it, {first_name}!"})
    assert app_settings.load(path)["mail_ack_template"] == "Got it, {first_name}!"
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "mail_ack_template": "Got it", "ack_reset": "1"})
    assert app_settings.load(path)["mail_ack_template"] == ""
    # Saving the standard text unchanged keeps it "standard" (so future improvements apply).
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX,
                                         "mail_ack_template": mailsync.DEFAULT_ACK.replace("\n", "\r\n")})
    assert app_settings.load(path)["mail_ack_template"] == ""
    # Saving the mailbox section without the field (older form) leaves it alone.
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "mail_ack_template": "Custom"})
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX})
    assert app_settings.load(path)["mail_ack_template"] == "Custom"


def test_email_page_shows_editor_and_preview_data(client):
    page = text(client.get("/helpdesk/email"))
    assert 'name="mail_ack_template"' in page and "{first_name}" in page
    assert "Use the standard message" in page and "Pat Rivera" in page


# ------------------------------------------------------------------ branding


def test_branding_on_pages_and_assets(app, client):
    page = text(client.get("/"))
    assert "The Hudson School" in page and "hudson-logo.png" in page and "favicon.png" in page
    assert "Device Inventory version 2.7" in page
    for name in ("hudson-logo.png", "favicon.png"):
        resp = client.get(f"/static/{name}")
        assert resp.status_code == 200 and resp.data[:8] == b"\x89PNG\r\n\x1a\n"
        resp.close()
    board = text(client.get("/helpdesk/board"))
    assert "IT Help Desk" in board and "The Hudson School" in board and "hudson-logo.png" in board


def test_digest_has_school_header(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    app_settings.update(app.config["SETTINGS_PATH"], mail_team="it@thehudsonschool.org")
    client.post("/helpdesk/email/digest")
    msg = email.message_from_bytes(gmail.sent[-1][0], policy=email.policy.default)
    assert "The Hudson School" in msg.get_body(("html",)).get_content()


# ------------------------------------------------------------------ Todoist


class FakeTodoist:
    def __init__(self):
        today = date.today()
        self.calls, self.closed, self.fail = [], [], None
        self.projects = [{"id": "p1", "name": "IT"}, {"id": "p2", "name": "Home"},
                         {"id": "p3", "name": "Old", "is_archived": True}]
        self.tasks = [
            {"id": "t1", "content": "Replace toner in **AC-115** printer", "priority": 1, "project_id": "p1",
             "due": {"date": today.isoformat()}, "child_order": 2},
            {"id": "t2", "content": "Order [Chromebook chargers](https://example.com)", "priority": 4,
             "project_id": "p1", "due": {"date": (today - timedelta(days=3)).isoformat()}, "labels": ["orders"]},
            {"id": "t3", "content": "Summer imaging plan", "priority": 2, "project_id": "p1",
             "due": {"date": (today + timedelta(days=30)).isoformat()}},
            {"id": "t4", "content": "Label new projectors", "priority": 3, "project_id": "p1", "due": None},
            {"id": "t5", "content": "Groceries", "priority": 1, "project_id": "p2", "due": None},
        ]

    def __call__(self, token, method, path, params=None):
        self.calls.append((method, path, dict(params or {})))
        if self.fail:
            raise todoist.TodoistError(self.fail)
        if token != "good-token":
            raise todoist.TodoistError("Todoist didn't accept the API token (check it in Help Desk → To-do list).")
        if path == "/projects":
            return {"results": self.projects, "next_cursor": None}
        if path == "/tasks":
            items = [t for t in self.tasks if t["id"] not in self.closed]
            if params and params.get("project_id"):
                items = [t for t in items if t["project_id"] == params["project_id"]]
            # Two pages, to follow next_cursor.
            if not (params or {}).get("cursor"):
                return {"results": items[:2], "next_cursor": "page2"}
            return {"results": items[2:], "next_cursor": None}
        if path == "/tasks/filter":
            today = date.today().isoformat()
            return {"results": [t for t in self.tasks if t["id"] not in self.closed and t["due"]
                                and t["due"]["date"] <= today], "next_cursor": None}
        if method == "POST" and path.endswith("/close"):
            self.closed.append(path.split("/")[2])
            return None
        raise AssertionError(path)


@pytest.fixture
def fake_todoist(monkeypatch):
    fake = FakeTodoist()
    monkeypatch.setattr(todoist, "_request", fake)
    return fake


def connect(client, **extra):
    return client.post("/helpdesk/todo", data={"todoist_token": "good-token", "todoist_project": "p1", "todoist_enabled": "1",
                                               "todoist_show": "today", "todoist_limit": "12"} | extra,
                       follow_redirects=True)


def test_todoist_setup_checks_token_and_keeps_it_out_of_backups(app, client, fake_todoist):
    page = text(client.post("/helpdesk/todo", data={"todoist_token": "bad"}, follow_redirects=True))
    assert "That token didn&#39;t work" in page or "That token didn't work" in page
    assert "todoist_token" not in app_settings.load(app.config["SETTINGS_PATH"])
    page = text(connect(client))
    assert "To-do list settings saved." in page and "Connected to Todoist." in page
    assert ">IT</option>" in page and "Old" not in page  # archived projects aren't offered
    cfg = app_settings.load(app.config["SETTINGS_PATH"])
    assert cfg["todoist_token"] == "good-token" and cfg["todoist_enabled"] is True
    assert 'value="good-token"' not in page  # never shown again
    with zipfile.ZipFile(io.BytesIO(client.get("/backup/download").data)) as z:
        assert "todoist_token" not in json.loads(z.read("settings.json"))
    # Saving without a token keeps the saved one.
    connect(client, todoist_token="", todoist_show="all")
    assert app_settings.load(app.config["SETTINGS_PATH"])["todoist_token"] == "good-token"


def test_todoist_setup_is_local_only(app, client, fake_todoist):
    app_settings.update(app.config["SETTINGS_PATH"], allow_network=True)
    assert client.get("/helpdesk/todo", environ_base=PHONE).status_code == 403


def test_board_shows_todos_sorted_and_filtered(app, client, fake_todoist):
    connect(client)
    live = text(client.get("/helpdesk/board/live"))
    assert "To-do" in live and "with-todo" in live
    # Overdue first, then today; future tasks and other projects hidden; no-date last.
    order = [live.index(s) for s in ("Order Chromebook chargers", "Replace toner in AC-115 printer",
                                     "Label new projectors")]
    assert order == sorted(order)
    assert "Summer imaging plan" not in live and "Groceries" not in live
    assert "3 days overdue" in live and "@orders" in live and "Today" in live
    assert "**" not in live and "https://example.com" not in live
    # Cached: the next refresh doesn't ask Todoist again.
    calls = len(fake_todoist.calls)
    client.get("/helpdesk/board/live")
    assert len(fake_todoist.calls) == calls


def test_todo_show_choices(app, client, fake_todoist):
    connect(client, todoist_show="all")
    live = text(client.get("/helpdesk/board/live"))
    assert "Summer imaging plan" in live and "Groceries" not in live
    connect(client, todoist_show="due", todoist_project="")
    live = text(client.get("/helpdesk/board/live"))
    assert "Replace toner" in live and "Label new projectors" not in live
    connect(client, todoist_show="filter", todoist_filter="#IT & p1")
    assert ("GET", "/tasks/filter", {"query": "#IT & p1", "limit": 200}) in fake_todoist.calls
    page = text(connect(client, todoist_show="filter", todoist_filter=""))
    assert "Type a Todoist filter" in page
    connect(client, todoist_show="all", todoist_limit="3")
    live = text(client.get("/helpdesk/board/live"))
    assert "+ 1 more in Todoist" in live


def test_tick_off_a_todo_from_the_board(app, client, fake_todoist):
    connect(client)
    client.get("/helpdesk/board/live")
    resp = client.post("/helpdesk/todo/t2/done", data={"js": "1"})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert ("POST", "/tasks/t2/close", {}) in fake_todoist.calls
    assert "Order Chromebook chargers" not in text(client.get("/helpdesk/board/live"))  # gone at once
    # Without JavaScript it goes back to the board.
    assert client.post("/helpdesk/todo/t1/done").headers["Location"].endswith("/helpdesk/board")
    fake_todoist.fail = "Couldn't reach Todoist (timed out)."
    resp = client.post("/helpdesk/todo/t4/done", data={"js": "1"})
    assert resp.status_code == 502 and "Couldn't reach Todoist" in resp.get_json()["error"]
    assert client.post("/helpdesk/todo/bad id!/done", data={"js": "1"}).status_code == 502


def test_board_keeps_last_list_when_todoist_is_down(app, client, fake_todoist, monkeypatch):
    connect(client)
    assert "Replace toner" in text(client.get("/helpdesk/board/live"))
    fake_todoist.fail = "Couldn't reach Todoist (no internet)."
    monkeypatch.setattr(todoist, "REFRESH_SECONDS", -1)
    live = text(client.get("/helpdesk/board/live"))
    assert "Couldn&#39;t reach Todoist" in live or "Couldn't reach Todoist" in live
    assert "Replace toner" in live  # still shows the last good list


def test_board_without_todoist_has_two_columns(client):
    live = text(client.get("/helpdesk/board/live"))
    assert "with-todo" not in live and "from Todoist" not in live


def test_disconnect_todoist(app, client, fake_todoist):
    connect(client)
    client.post("/helpdesk/todo", data={"action": "disconnect"})
    cfg = app_settings.load(app.config["SETTINGS_PATH"])
    assert not cfg.get("todoist_token") and cfg["todoist_enabled"] is False
    assert "from Todoist" not in text(client.get("/helpdesk/board/live"))


def test_simplify_due_times_and_text():
    today = date(2026, 11, 5)
    t = todoist.simplify({"id": 9, "content": "__Bold__ thing", "priority": 3,
                          "due": {"date": "2026-11-06T14:30:00", "is_recurring": True}}, today)
    assert t["content"] == "Bold thing" and t["due_text"] == "Tomorrow" and t["time"] == "2:30 pm"
    assert t["recurring"] and not t["overdue"]
    assert todoist.due_text(date(2026, 11, 4), today) == "Yesterday"
    assert todoist.due_text(date(2026, 11, 20), today) == "Fri Nov 20"
    assert todoist.simplify({"id": 1, "content": "x", "due": {"date": "garbage"}}, today)["due"] is None


def test_board_columns_hold_long_lists_for_scrolling(app, client):
    import sqlite3
    db = sqlite3.connect(app.config["DATABASE"])
    for i in range(70):
        db.execute("INSERT INTO tickets (subject, requester_email, priority, status, unread) VALUES (?, ?, 'Normal', 'Open', ?)",
                   (f"Ticket number {i}", "s@thehudsonschool.org", 1 if i < 30 else 0))
    db.commit()
    db.close()
    live = text(client.get("/helpdesk/board/live"))
    assert live.count('class="col-scroll"') == 2 and 'data-col="unread"' in live and 'data-col="queue"' in live
    assert live.count("board-ticket unread") == 30 and live.count("board-ticket compact") == 40
    board = text(client.get("/helpdesk/board"))
    assert 'id="board-scroll"' in board and "setupScrollers" in board
