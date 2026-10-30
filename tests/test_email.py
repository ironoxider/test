import io
import json
import os
import re
import sqlite3
import sys
import zipfile
from email.message import EmailMessage

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app_settings  # noqa: E402
import gmail_api  # noqa: E402
import mailsync  # noqa: E402
from app import create_app  # noqa: E402

MAILBOX = "helpdesk-test@thehudsonschool.org"


def make_email(subject="Projector in W401 won't turn on", body="CB-494 also won't charge.\n",
               sender="Grace Hopper <ghopper@thehudsonschool.org>", msg_id="<a1@mail.gmail.com>",
               in_reply_to=None, html=None, attachment=None, headers=None):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, MAILBOX, subject, msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment:
        msg.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename=attachment)
    return msg.as_bytes()


class FakeGmail:
    """Stands in for gmail_api.GmailClient."""

    def __init__(self):
        self.inbox = []      # [gmail id, raw, thread id, labels]
        self.sent = []
        self.fail_send = False

    def deliver(self, raw, thread="t1"):
        self.inbox.append([f"g{len(self.inbox) + 1}", raw, thread, set()])

    def label_id(self, name):
        return name

    def list_ids(self, query, limit=50):
        return [m[0] for m in self.inbox if not m[3] & {mailsync.IMPORTED_LABEL, mailsync.SKIPPED_LABEL}]

    def get_raw(self, gmail_id):
        m = next(m for m in self.inbox if m[0] == gmail_id)
        return m[1], m[2]

    def add_label(self, gmail_id, label):
        next(m for m in self.inbox if m[0] == gmail_id)[3].add(label)

    def labels(self, gmail_id):
        return next(m for m in self.inbox if m[0] == gmail_id)[3]

    def send(self, raw, thread_id=None):
        if self.fail_send:
            raise gmail_api.GmailError("Google returned an error (500): backend error")
        self.sent.append((raw, thread_id))
        return f"s{len(self.sent)}", thread_id or "t-new"

    def address(self):
        return MAILBOX


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def gmail(app, monkeypatch):
    """A connected mailbox backed by FakeGmail."""
    fake = FakeGmail()
    data_dir = os.path.dirname(app.config["DATABASE"])
    gmail_api.save_secrets(data_dir, {"client_id": "id", "client_secret": "s", "refresh_token": "r",
                                      "connected_email": MAILBOX})
    app_settings.update(app.config["SETTINGS_PATH"], mail_mailbox=MAILBOX, mail_enabled=True)
    monkeypatch.setattr(mailsync, "get_client", lambda: fake)
    return fake


def text(resp):
    return resp.data.decode()


def db_for(app):
    return sqlite3.connect(app.config["DATABASE"])


def add_device(client, tag="CB-494", location="Room W401"):
    client.post("/devices/new", data={"asset_tag": tag, "name": "Chromebook", "status": "In Service",
                                      "new_location": location})


def check(client):
    return text(client.post("/helpdesk/email/check", follow_redirects=True))


# ------------------------------------------------------------------ reading emails


def test_parse_plain_html_attachments_and_quotes():
    reply = ("Still broken.\n\nOn Thu, Oct 30, 2026 at 2:05 PM IT Help Desk <\n"
             "helpdesk-test@thehudsonschool.org> wrote:\n> Hi Grace,\n> We opened ticket #1\n")
    parsed = mailsync.parse_message(make_email(body=reply, attachment="screen.png"))
    assert parsed["body"] == "Still broken."
    assert parsed["attachments"] == ["screen.png"]
    assert parsed["from_email"] == "ghopper@thehudsonschool.org" and parsed["from_name"] == "Grace Hopper"
    html_only = EmailMessage()
    html_only["From"], html_only["Subject"] = "a@thehudsonschool.org", "Hi"
    html_only.set_content("<div>Line one</div><div>Line <b>two</b></div><style>p{}</style>", subtype="html")
    assert mailsync.parse_message(html_only.as_bytes())["body"] == "Line one\n\nLine two"
    outlook = "Fixed now\n\n-----Original Message-----\nFrom: x\n"
    assert mailsync.parse_message(make_email(body=outlook))["body"] == "Fixed now"
    assert mailsync.parse_message(make_email(headers={"Auto-Submitted": "auto-replied"}))["automatic"]
    assert mailsync.parse_message(make_email(sender="mailer-daemon@googlemail.com"))["automatic"]
    assert not mailsync.parse_message(make_email())["automatic"]


# ------------------------------------------------------------------ importing


def test_new_email_becomes_ticket_with_device_location_and_acknowledgement(app, client, gmail):
    add_device(client)
    gmail.deliver(make_email(attachment="photo.jpg"))
    assert "1 new ticket, 0 replies added" in check(client)
    page = text(client.get("/helpdesk/1"))
    for expected in ("Projector in W401 won&#39;t turn on", "CB-494 also won&#39;t charge.", "Grace Hopper",
                     "Linked CB-494.", "Room W401", "Ticket created from an email.", "photo.jpg",
                     "automatic reply sent", "View in Gmail"):
        assert expected in page, expected
    row = db_for(app).execute("SELECT status, source, requester_email, gmail_thread_id FROM tickets").fetchone()
    assert row == ("New", "email", "ghopper@thehudsonschool.org", "t1")
    assert gmail.labels("g1") == {mailsync.IMPORTED_LABEL}
    raw, thread = gmail.sent[0]
    ack = mailsync.parse_message(raw)
    assert thread == "t1" and ack["subject"] == "Re: [Ticket #1] Projector in W401 won't turn on"
    assert "<a1@mail.gmail.com>" in ack["references"] and "opened ticket #1" in ack["body"]
    assert "Hi Grace," in ack["body"]


def test_replies_join_their_ticket_and_reopen_it(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    client.post("/helpdesk/1/update", data={"status": "Waiting"})
    our_id = db_for(app).execute("SELECT message_id FROM ticket_messages WHERE kind = 'auto'").fetchone()[0]
    # Same Gmail thread
    gmail.deliver(make_email(subject="Re: Projector", body="Tried again, no luck.", msg_id="<a2@x>"))
    # A reply to our email that Gmail put in a new thread
    gmail.deliver(make_email(subject="Re: hello", body="Also the cable is loose.", msg_id="<a3@x>",
                             in_reply_to=our_id), thread="t9")
    # Only the ticket number in the subject
    gmail.deliver(make_email(subject="Fwd: [Ticket #1] Projector", body="Forwarding a photo.",
                             msg_id="<a4@x>", sender="Ada <ada@thehudsonschool.org>"), thread="t10")
    assert "0 new tickets, 3 replies added" in check(client)
    page = text(client.get("/helpdesk/1"))
    for expected in ("Tried again, no luck.", "Also the cable is loose.", "Forwarding a photo.",
                     "Status: Waiting → Open (the requester replied)"):
        assert expected in page
    assert db_for(app).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert len(gmail.sent) == 1  # no acknowledgement for replies


def test_outside_senders_auto_replies_and_own_mail_skipped(app, client, gmail):
    gmail.deliver(make_email(sender="sales@vendor.com"))
    gmail.deliver(make_email(headers={"Auto-Submitted": "auto-replied"}, msg_id="<b@x>"), thread="t2")
    gmail.deliver(make_email(sender=MAILBOX, msg_id="<c@x>"), thread="t3")
    assert "0 new tickets, 0 replies added, 3 skipped" in check(client)
    assert all(gmail.labels(g) == {mailsync.SKIPPED_LABEL} for g in ("g1", "g2", "g3"))
    assert db_for(app).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert not gmail.sent
    # An outside address replying on an existing ticket (e.g. a vendor on CC) is accepted.
    gmail.deliver(make_email(), thread="t4")
    check(client)
    gmail.deliver(make_email(sender="support@vendor.com", body="Part shipped.", msg_id="<d@x>"), thread="t4")
    assert "1 reply added" in check(client)


def test_domain_filter_off_and_auto_reply_off(app, client, gmail):
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "mail_enabled": "1", "mail_allowed_domain": "",
                                         "mail_from_name": "Hudson IT", "mail_interval": "5"})
    gmail.deliver(make_email(sender="parent@gmail.com"))
    check(client)
    assert db_for(app).execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert not gmail.sent  # automatic reply was unticked
    cfg = app_settings.load(app.config["SETTINGS_PATH"])
    assert cfg["mail_interval"] == 5 and cfg["mail_from_name"] == "Hudson IT" and cfg["mail_auto_reply"] is False


def test_message_never_imported_twice(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    gmail.labels("g1").clear()  # as if adding the label had failed
    check(client)
    assert db_for(app).execute("SELECT COUNT(*) FROM ticket_messages WHERE kind = 'description'").fetchone()[0] == 1
    assert gmail.labels("g1") == {mailsync.IMPORTED_LABEL}


def test_failed_acknowledgement_still_creates_ticket(app, client, gmail):
    gmail.fail_send = True
    gmail.deliver(make_email())
    check(client)
    assert "The automatic reply couldn&#39;t be sent" in text(client.get("/helpdesk/1"))


def test_check_failure_is_recorded_and_shown(app, client, gmail, monkeypatch):
    def broken():
        raise gmail_api.GmailError("Couldn't reach Google (timed out). Check the internet connection.")
    monkeypatch.setattr(mailsync, "get_client", broken)
    assert "Couldn&#39;t check email: Couldn&#39;t reach Google" in check(client)
    assert "Last check failed" in text(client.get("/helpdesk/"))


# ------------------------------------------------------------------ replying


def test_reply_by_email_and_set_waiting(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    page = text(client.post("/helpdesk/1/reply", data={"body": "Swapping the lamp today.", "author": "Rafael",
                                                       "then": "Waiting"}, follow_redirects=True))
    assert "Reply sent to ghopper@thehudsonschool.org." in page and "emailed the requester" in page
    assert "Status: New → Waiting" in page
    raw, thread = gmail.sent[-1]
    sent = mailsync.parse_message(raw)
    assert thread == "t1" and sent["subject"] == "Re: [Ticket #1] Projector in W401 won't turn on"
    assert sent["body"].startswith("Swapping the lamp today.") and "Rafael" in sent["body"]
    assert "Rafael" in [r[0] for r in db_for(app).execute("SELECT name FROM options WHERE kind = 'technician'")]


def test_failed_reply_keeps_the_draft(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    gmail.fail_send = True
    page = text(client.post("/helpdesk/1/reply", data={"body": "Draft text here"}))
    assert "The reply wasn&#39;t sent" in page and "Draft text here" in page
    assert db_for(app).execute("SELECT COUNT(*) FROM ticket_messages WHERE kind = 'reply'").fetchone()[0] == 0


def test_reply_needs_connection(client):
    client.post("/helpdesk/new", data={"subject": "x", "requester_email": "a@thehudsonschool.org"})
    page = text(client.get("/helpdesk/1"))
    assert "Send reply" not in page and "connect the help desk mailbox" in page
    page = text(client.post("/helpdesk/1/reply", data={"body": "hello"}))
    assert "Email isn&#39;t connected yet" in page and "hello" in page


# ------------------------------------------------------------------ setup and sign-in


def client_file(kind="installed"):
    return (io.BytesIO(json.dumps({kind: {"client_id": "cid.apps.googleusercontent.com",
                                          "client_secret": "sec"}}).encode()), "client.json")


def test_setup_upload_connect_and_callback(app, client, monkeypatch):
    page = text(client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "client_file": client_file("web")},
                            content_type="multipart/form-data", follow_redirects=True))
    assert "Desktop app" in page
    page = text(client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "client_file": client_file()},
                            content_type="multipart/form-data", follow_redirects=True))
    assert "Email settings saved." in page and "Connect mailbox" in page
    resp = client.post("/helpdesk/email/connect", base_url="http://127.0.0.1:5000")
    assert resp.status_code == 302
    url = resp.headers["Location"]
    assert url.startswith(gmail_api.AUTH_URL) and "login_hint=helpdesk-test%40thehudsonschool.org" in url
    assert "code_challenge=" in url and "redirect_uri=http%3A%2F%2F127.0.0.1%3A5000%2F" in url
    state = re.search(r"state=([^&]+)", url).group(1)

    seen = {}
    monkeypatch.setattr(gmail_api, "exchange_code",
                        lambda c, code, uri, verifier: seen.update(code=code, uri=uri) or {"refresh_token": "rt"})
    monkeypatch.setattr(gmail_api.GmailClient, "address", lambda self: MAILBOX.upper())
    # Google comes back to the site root, which hands over to the callback.
    resp = client.get(f"/?state={state}&code=abc", base_url="http://127.0.0.1:5000")
    assert resp.status_code == 302 and "/helpdesk/email/oauth" in resp.headers["Location"]
    page = text(client.get(resp.headers["Location"], base_url="http://127.0.0.1:5000", follow_redirects=True))
    assert f"Connected to {MAILBOX}" in page and seen == {"code": "abc", "uri": "http://127.0.0.1:5000/"}
    stored = gmail_api.load_secrets(os.path.dirname(app.config["DATABASE"]))
    assert stored["refresh_token"] == "rt" and stored["client_id"].startswith("cid")
    assert "refresh_token" not in json.dumps(app_settings.load(app.config["SETTINGS_PATH"]))


def test_callback_rejects_wrong_account_and_stale_state(app, client, monkeypatch):
    client.post("/helpdesk/email", data={"mail_mailbox": MAILBOX, "client_file": client_file()},
                content_type="multipart/form-data")
    resp = client.post("/helpdesk/email/connect", base_url="http://127.0.0.1:5000")
    state = re.search(r"state=([^&]+)", resp.headers["Location"]).group(1)
    monkeypatch.setattr(gmail_api, "exchange_code", lambda *a: {"refresh_token": "rt"})
    monkeypatch.setattr(gmail_api.GmailClient, "address", lambda self: "rafael@thehudsonschool.org")
    page = text(client.get(f"/helpdesk/email/oauth?state={state}&code=x", base_url="http://127.0.0.1:5000",
                           follow_redirects=True))
    assert "You signed in as rafael@thehudsonschool.org" in page
    assert "refresh_token" not in gmail_api.load_secrets(os.path.dirname(app.config["DATABASE"]))
    page = text(client.get("/helpdesk/email/oauth?state=old&code=x", follow_redirects=True))
    assert "expired" in page


def test_changing_mailbox_needs_new_sign_in_and_disconnect(app, client, gmail):
    page = text(client.post("/helpdesk/email", data={"mail_mailbox": "helpdesk@thehudsonschool.org"},
                            follow_redirects=True))
    assert "Click Connect mailbox to sign in to it" in page
    assert not mailsync_connected(app)
    gmail_api.save_secrets(os.path.dirname(app.config["DATABASE"]),
                           {"client_id": "i", "client_secret": "s", "refresh_token": "r"})
    page = text(client.post("/helpdesk/email/disconnect", follow_redirects=True))
    assert "Disconnected" in page and not mailsync_connected(app)


def mailsync_connected(app):
    return bool(gmail_api.load_secrets(os.path.dirname(app.config["DATABASE"])).get("refresh_token"))


def test_email_setup_only_on_this_computer(client):
    client.post("/settings", data={"allow_network": "1"})
    phone = {"REMOTE_ADDR": "192.0.2.50"}
    assert client.get("/helpdesk/email", environ_base=phone).status_code == 403
    assert client.post("/helpdesk/email/connect", environ_base=phone).status_code == 403
    assert client.get("/helpdesk/", environ_base=phone).status_code == 200


def test_backup_leaves_out_the_mailbox_sign_in(app, client, gmail):
    z = zipfile.ZipFile(io.BytesIO(client.get("/backup/download").data))
    assert "gmail.json" not in z.namelist()
    assert "refresh_token" not in z.read("settings.json").decode()


# ------------------------------------------------------------------ the Gmail client itself


def test_gmail_client_requests(monkeypatch):
    import base64
    calls = []

    def fake_request(url, data=None, headers=None, method=None):
        calls.append((url, data, headers))
        if url == gmail_api.TOKEN_URL:
            return {"access_token": "at", "expires_in": 3600}
        if url.endswith("/profile"):
            return {"emailAddress": MAILBOX}
        if "/messages?" in url:
            return {"messages": [{"id": "new"}, {"id": "old"}]}
        if "/messages/old?" in url:
            return {"raw": base64.urlsafe_b64encode(b"Subject: hi\r\n\r\nbody").decode().rstrip("="),
                    "threadId": "t1"}
        if url.endswith("/messages/send"):
            return {"id": "s1", "threadId": data.get("threadId")}
        if url.endswith("/labels") and data is None:
            return {"labels": [{"name": "INBOX", "id": "INBOX"}]}
        if url.endswith("/labels"):
            return {"id": "Label_7"}
        return {}

    monkeypatch.setattr(gmail_api, "_request", fake_request)
    c = gmail_api.GmailClient("cid", "sec", "rt")
    assert c.address() == MAILBOX
    assert c.list_ids("in:inbox") == ["old", "new"]                # oldest first
    assert c.get_raw("old") == (b"Subject: hi\r\n\r\nbody", "t1")
    assert c.label_id(mailsync.IMPORTED_LABEL) == "Label_7" and c.label_id(mailsync.IMPORTED_LABEL) == "Label_7"
    assert c.send(b"raw mail", "t1") == ("s1", "t1")
    token_calls = [x for x in calls if x[0] == gmail_api.TOKEN_URL]
    assert len(token_calls) == 1 and token_calls[0][1]["grant_type"] == "refresh_token"
    assert all(x[2]["Authorization"] == "Bearer at" for x in calls if x[0] != gmail_api.TOKEN_URL)
    sent = next(x for x in calls if x[0].endswith("/messages/send"))[1]
    assert base64.urlsafe_b64decode(sent["raw"]) == b"raw mail"
    created = [x for x in calls if x[0].endswith("/labels") and x[1]]
    assert len(created) == 1 and created[0][1]["name"] == mailsync.IMPORTED_LABEL


def test_read_client_file_and_pkce():
    import base64
    import hashlib
    with pytest.raises(gmail_api.GmailError, match="couldn't be read"):
        gmail_api.read_client_file("not json")
    with pytest.raises(gmail_api.GmailError, match="client ID"):
        gmail_api.read_client_file('{"installed": {}}')
    verifier, challenge = gmail_api.pkce_pair()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
