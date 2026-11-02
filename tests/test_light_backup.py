import importlib.util
import io
import json
import os
import sqlite3
import sys
import urllib.error
import zipfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app_settings  # noqa: E402
import autobackup  # noqa: E402
import mailsync  # noqa: E402
from test_email import FakeGmail, MAILBOX, make_email  # noqa: E402
import gmail_api  # noqa: E402
from app import create_app  # noqa: E402

PHONE = {"REMOTE_ADDR": "192.0.2.50"}


@pytest.fixture
def app(tmp_path):
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


def text(resp):
    return resp.data.decode()


def light(client, **kw):
    return client.get("/helpdesk/light/status", **kw).get_json()


def check(client):
    client.post("/helpdesk/email/check")


def set_light(app, **options):
    app_settings.update(app.config["SETTINGS_PATH"], **options)


# ------------------------------------------------------------------ when the light is on


def test_new_ticket_lights_until_silenced_then_next_one_lights_again(app, client, gmail):
    assert light(client)["light"] is False
    gmail.deliver(make_email())
    check(client)
    state = light(client)
    assert state["light"] is True and state["reason"] == "new" and state["ticket"]["id"] == 1
    assert client.post("/helpdesk/light/ack").get_json()["light"] is False
    import time
    time.sleep(1.1)  # alerts are compared to the second
    gmail.deliver(make_email(subject="Printer jam", msg_id="<p@x>"), thread="t2")
    check(client)
    assert light(client)["ticket"]["id"] == 2


def test_opening_the_ticket_turns_it_off_unless_switched_off(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    client.get("/helpdesk/1")
    assert light(client)["light"] is False
    set_light(app, light_clear_on_open=False)
    gmail.deliver(make_email(subject="Another", msg_id="<b@x>"), thread="t2")
    check(client)
    client.get("/helpdesk/2")
    assert light(client)["light"] is True


def test_urgent_emails_become_high_and_light_even_if_new_tickets_dont(app, client, gmail):
    set_light(app, light_new=False)
    gmail.deliver(make_email(subject="Normal request"))
    gmail.deliver(make_email(subject="URGENT: projector down for assembly", msg_id="<u@x>"), thread="t2")
    gmail.deliver(make_email(subject="Can you look", msg_id="<i@x>", headers={"X-Priority": "1 (Highest)"}), thread="t3")
    check(client)
    db = sqlite3.connect(app.config["DATABASE"])
    assert [r[0] for r in db.execute("SELECT priority FROM tickets ORDER BY id")] == ["Normal", "High", "High"]
    state = light(client)
    assert state["light"] and state["reason"] == "urgent" and state["alerts"] == 2
    assert "marked urgent or important" in text(client.get("/helpdesk/2"))


def test_replies_light_only_when_switched_on(app, client, gmail):
    gmail.deliver(make_email())
    check(client)
    client.get("/helpdesk/1")
    gmail.deliver(make_email(subject="Re: Projector", body="Any news?", msg_id="<r1@x>"))
    check(client)
    assert light(client)["light"] is False
    set_light(app, light_replies=True)
    gmail.deliver(make_email(subject="Re: Projector", body="Hello?", msg_id="<r2@x>"))
    check(client)
    assert light(client)["reason"] == "reply"


def test_raising_priority_lights_and_closing_clears(app, client):
    # follow_redirects: saving shows the ticket page, which must not count as "seen" for these.
    client.post("/helpdesk/new", data={"subject": "Typed in", "priority": "Normal"}, follow_redirects=True)
    assert light(client)["light"] is False
    client.post("/helpdesk/new", data={"subject": "Phone call: lab down", "priority": "Urgent"},
                follow_redirects=True)
    state = light(client)
    assert state["ticket"]["id"] == 2 and state["reason"] == "raised"
    client.post("/helpdesk/light/ack")
    import time
    time.sleep(1.1)
    client.post("/helpdesk/1/update", data={"priority": "High"}, follow_redirects=True)
    assert light(client)["ticket"]["id"] == 1
    client.post("/helpdesk/1/update", data={"status": "Closed"})
    assert light(client)["light"] is False


def test_test_button_and_board_banner(app, client):
    page = text(client.post("/helpdesk/light", data={"action": "test"}, follow_redirects=True))
    assert "within a few seconds" in page and "The light is ON</strong>" in page
    state = light(client)
    assert state["light"] and state["reason"] == "test"
    live = text(client.get("/helpdesk/board/live"))
    assert "Light on:" in live and "Silence" in live
    client.post("/helpdesk/light/ack", data={"next": "/helpdesk/board"})
    assert light(client)["light"] is False and "Light on:" not in text(client.get("/helpdesk/board/live"))


def test_light_settings_saved(app, client):
    client.post("/helpdesk/light", data={"light_replies": "1"})
    cfg = app_settings.load(app.config["SETTINGS_PATH"])
    assert cfg["light_replies"] is True and cfg["light_new"] is False and cfg["light_clear_on_open"] is False


def test_key_lets_a_pi_elsewhere_read_and_silence_only(app, client):
    page = text(client.get("/helpdesk/light"))
    key = app_settings.load(app.config["SETTINGS_PATH"])["light_key"]
    assert key and key in page
    assert client.get("/helpdesk/light/status", environ_base=PHONE).status_code == 403
    assert client.get("/helpdesk/light/status", environ_base=PHONE, headers={"X-Helpdesk-Key": "wrong"}).status_code == 403
    ok = client.get("/helpdesk/light/status", environ_base=PHONE, headers={"X-Helpdesk-Key": key})
    assert ok.status_code == 200 and "light" in ok.get_json()
    assert client.post("/helpdesk/light/ack", environ_base=PHONE, headers={"X-Helpdesk-Key": key}).status_code == 200
    assert client.get("/helpdesk/", environ_base=PHONE, headers={"X-Helpdesk-Key": key}).status_code == 403
    client.post("/settings", data={"allow_network": "1"})
    assert client.get("/helpdesk/light", environ_base=PHONE).status_code == 403  # setup stays local
    client.post("/helpdesk/light", data={"action": "new_key"})
    assert app_settings.load(app.config["SETTINGS_PATH"])["light_key"] != key


# ------------------------------------------------------------------ automatic backups


def test_backup_due():
    cfg = {"backup_auto": True, "backup_time": "02:00", "last_auto_backup": None}
    assert not autobackup.due(cfg, datetime(2026, 11, 3, 1, 59))
    assert autobackup.due(cfg, datetime(2026, 11, 3, 2, 0))
    assert not autobackup.due(cfg | {"last_auto_backup": "2026-11-03"}, datetime(2026, 11, 3, 9, 0))
    assert not autobackup.due(cfg | {"backup_auto": False}, datetime(2026, 11, 3, 9, 0))


def test_automatic_backup_saves_prunes_and_emails(app, client, gmail, tmp_path):
    folder = tmp_path / "Backups"
    folder.mkdir()
    for day in range(1, 6):
        (folder / f"{autobackup.PREFIX}2026-10-0{day}.zip").write_bytes(b"old")
    (folder / "Device Inventory backup 2026-10-01 0900.zip").write_bytes(b"manual")
    page = text(client.post("/settings", data={"backup_dir": str(folder), "backup_auto": "1", "backup_time": "02:00",
                                               "backup_keep": "3", "backup_email": "rafael@thehudsonschool.org"},
                            follow_redirects=True))
    assert "Settings saved" in page
    with app.app_context():
        result, error = autobackup.run(datetime(2026, 11, 3, 2, 0))
    assert error is None and "saved to" in result and "emailed to rafael@thehudsonschool.org" in result
    names = sorted(os.listdir(folder))
    assert names == ["Device Inventory auto-backup 2026-10-04.zip", "Device Inventory auto-backup 2026-10-05.zip",
                     "Device Inventory auto-backup 2026-11-03.zip", "Device Inventory backup 2026-10-01 0900.zip"]
    assert "inventory.db" in zipfile.ZipFile(folder / "Device Inventory auto-backup 2026-11-03.zip").namelist()
    import email
    import email.policy
    sent = email.message_from_bytes(gmail.sent[-1][0], policy=email.policy.default)
    assert sent["To"] == "rafael@thehudsonschool.org" and "Backup 2026-11-03" in sent["Subject"]
    attachment = next(sent.iter_attachments())
    assert attachment.get_filename() == "Device Inventory auto-backup 2026-11-03.zip"
    assert "inventory.db" in zipfile.ZipFile(io.BytesIO(attachment.get_content())).namelist()
    assert "Last automatic backup: 2026-11-03 02:00" in text(client.get("/settings"))


def test_automatic_backup_problem_is_shown(app, client, tmp_path):
    folder = tmp_path / "drive"
    folder.mkdir()
    client.post("/settings", data={"backup_dir": str(folder), "backup_auto": "1", "backup_time": "02:00"})
    folder.rmdir()  # the drive was unplugged
    with app.app_context():
        _, error = autobackup.run(datetime(2026, 11, 3, 2, 0))
    assert "isn't there" in error
    assert "Automatic backup problem" in text(client.get("/"))
    assert "had a problem" in text(client.get("/settings"))


def test_backup_settings_validation(client):
    page = text(client.post("/settings", data={"backup_dir": "", "backup_auto": "1"}, follow_redirects=True))
    assert "enter a backup folder, an email address or an offsite copy" in page
    page = text(client.post("/settings", data={"backup_dir": "", "backup_time": "2am"}, follow_redirects=True))
    assert "must look like 02:00" in page


# ------------------------------------------------------------------ the Pi script


def load_pi_script():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi", "helpdesk_light.py")
    spec = importlib.util.spec_from_file_location("helpdesk_light", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHub:
    def __init__(self, states):
        self.states, self.url, self.silenced = list(states), "http://pi", 0

    def status(self):
        state = self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    def silence(self):
        self.silenced += 1
        return {"light": False}


def test_pi_script_follows_the_status_and_fails_safe():
    pl = load_pi_script()
    args = pl.parse_args(["--dry-run", "--interval", "0"])
    relay = pl.PrintRelay()
    seen = []
    hub = FakeHub([{"light": True, "reason_text": "New ticket", "ticket": {"id": 1, "subject": "x"}},
                   {"light": True}, urllib.error.URLError("down"), {"light": True}, {"light": False}])
    pl.run(args, hub=hub, relay=relay, cycles=5, sleep=lambda s: seen.append(relay.is_active))
    assert seen == [True, True, False, True, False]


def test_pi_script_quiet_hours_and_test_mode():
    pl = load_pi_script()
    assert pl.in_quiet_hours("17:30-07:00", datetime(2026, 11, 3, 22, 0))
    assert pl.in_quiet_hours("17:30-07:00", datetime(2026, 11, 3, 6, 59))
    assert not pl.in_quiet_hours("17:30-07:00", datetime(2026, 11, 3, 12, 0))
    assert pl.in_quiet_hours("12:00-13:00", datetime(2026, 11, 3, 12, 30))
    with pytest.raises(SystemExit):
        pl.in_quiet_hours("evenings")
    relay, steps = pl.PrintRelay(), []
    args = pl.parse_args(["--dry-run", "--test"])
    pl.run(args, hub=FakeHub([]), relay=relay, sleep=lambda s: steps.append(relay.is_active))
    assert steps == [True] and relay.is_active is False


def test_pi_script_against_the_real_app(app, client, gmail):
    """The script's HTTP calls work against Device Inventory itself."""
    pl = load_pi_script()
    gmail.deliver(make_email())
    check(client)

    class ClientHub(pl.Hub):
        def _call(self, path, method="GET"):
            resp = client.open(path, method=method)
            return json.loads(resp.data)

    hub = ClientHub("http://127.0.0.1:5000", "")
    assert hub.status()["light"] is True
    assert hub.silence()["light"] is False


class FakeRclone:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, cmd, capture_output, text, timeout):
        import subprocess
        if self.fail == "missing":
            raise FileNotFoundError("rclone")
        self.calls.append(cmd)
        if cmd[1] == "copyto":
            assert os.path.exists(cmd[2]) and zipfile.is_zipfile(cmd[2])
        if self.fail == "auth":
            return subprocess.CompletedProcess(cmd, 1, "", "2026/11/03 ERROR : x\nFailed to create file system: "
                                               "401 bad_auth_token\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_offsite_copy_to_backblaze_with_rclone(app, client, monkeypatch):
    import subprocess
    fake = FakeRclone()
    monkeypatch.setattr(subprocess, "run", fake)
    page = text(client.post("/settings", data={"backup_dir": "", "backup_auto": "1", "backup_time": "02:00",
                                               "backup_rclone": "b2:hudson-it-backups/device-inventory/",
                                               "backup_offsite_days": "90"}, follow_redirects=True))
    assert "Settings saved" in page and "Run the automatic backup now" in page
    page = text(client.post("/backup/auto-now", follow_redirects=True))
    assert "copied to b2:hudson-it-backups/device-inventory/" in page
    copy, delete = fake.calls
    name = os.path.basename(copy[2])
    assert copy[:2] == ["rclone", "copyto"] and copy[3] == f"b2:hudson-it-backups/device-inventory/{name}"
    assert name.startswith(autobackup.PREFIX)
    assert delete == ["rclone", "delete", "b2:hudson-it-backups/device-inventory", "--min-age", "90d",
                      "--include", f"{autobackup.PREFIX}*.zip", "--b2-hard-delete"]


def test_offsite_copy_problems_are_reported(app, client, monkeypatch):
    import subprocess
    client.post("/settings", data={"backup_dir": "", "backup_auto": "1", "backup_rclone": "b2:bucket"})
    monkeypatch.setattr(subprocess, "run", FakeRclone(fail="missing"))
    page = text(client.post("/backup/auto-now", follow_redirects=True))
    assert "rclone isn&#39;t installed" in page
    monkeypatch.setattr(subprocess, "run", FakeRclone(fail="auth"))
    page = text(client.post("/backup/auto-now", follow_redirects=True))
    assert "401 bad_auth_token" in page
    assert "Automatic backup problem" in text(client.get("/"))
    page = text(client.post("/settings", data={"backup_dir": "", "backup_rclone": "just-a-bucket"},
                            follow_redirects=True))
    assert "should look like" in page


# ------------------------------------------------------------------ the board's siren


@pytest.fixture
def no_light(monkeypatch):
    import alerts
    monkeypatch.setitem(alerts._light_seen, "at", 0.0)


def test_siren_shows_when_no_light_is_connected(app, client, no_light):
    client.post("/helpdesk/new", data={"subject": "Lab down", "priority": "Urgent"}, follow_redirects=True)
    assert '<body class="board-page siren">' in text(client.get("/helpdesk/board"))
    assert 'data-siren="1"' in text(client.get("/helpdesk/board/live"))
    assert "No light is connected right now, so the board shows the siren" in text(client.get("/helpdesk/light"))
    # The Pi's light script checks in: the real light takes over.
    status = client.get("/helpdesk/light/status", headers={"X-Helpdesk-Client": "light"}).get_json()
    assert status["light_connected"] is True
    assert 'data-siren=""' in text(client.get("/helpdesk/board/live"))
    assert "A light is connected" in text(client.get("/helpdesk/light"))


def test_siren_modes_and_only_while_the_light_is_on(app, client, no_light):
    assert 'data-siren=""' in text(client.get("/helpdesk/board/live"))  # nothing to alert about
    client.post("/helpdesk/new", data={"subject": "Lab down", "priority": "Urgent"}, follow_redirects=True)
    client.post("/helpdesk/light", data={"light_new": "1", "light_urgent": "1", "board_siren": "never"})
    assert 'data-siren=""' in text(client.get("/helpdesk/board/live"))
    client.post("/helpdesk/light", data={"light_new": "1", "light_urgent": "1", "board_siren": "always"})
    client.get("/helpdesk/light/status", headers={"X-Helpdesk-Client": "light"})
    assert 'data-siren="1"' in text(client.get("/helpdesk/board/live"))
    client.post("/helpdesk/light/ack")
    assert 'data-siren=""' in text(client.get("/helpdesk/board/live"))
    assert app_settings.load(app.config["SETTINGS_PATH"])["board_siren"] == "always"


def test_pi_script_identifies_itself(monkeypatch):
    pl = load_pi_script()
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen.update(req.headers)
        return Resp(b'{"light": false}')

    monkeypatch.setattr(pl.urllib.request, "urlopen", fake_urlopen)
    pl.Hub("http://vm:5000", "secret").status()
    assert seen["X-helpdesk-client"] == "light" and seen["X-helpdesk-key"] == "secret"
