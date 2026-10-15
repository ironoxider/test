import io
import json
import os
import re
import sqlite3
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app_settings  # noqa: E402
from app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")}).test_client()


def text(resp):
    return resp.data.decode()


def add_batch(client, **extra):
    data = {"tracking": "batch", "name": "USB-C charger", "quantity": "40", "min_quantity": "10",
            "status": "In Storage", "new_location": "Tech Office"}
    data.update(extra)
    return client.post("/devices/new", data=data, follow_redirects=True)


# ------------------------------------------------------------------ batches

def test_add_batch_without_asset_tag(client):
    page = text(add_batch(client))
    assert "Added USB-C charger (batch)." in page and "40</strong> on hand" in page
    listing = text(client.get("/"))
    assert ">Batch<" in listing and "USB-C charger" in listing
    # A second untagged batch is fine (asset tags only have to be unique when given).
    assert "Added Wired mouse (batch)." in text(add_batch(client, name="Wired mouse", quantity="25"))


def test_single_device_still_needs_asset_tag(client):
    page = text(client.post("/devices/new", data={"tracking": "single", "name": "Laptop",
                                                  "status": "In Service"}))
    assert "Asset tag is required" in page


def test_batch_quantity_validation(client):
    page = text(add_batch(client, quantity=""))
    assert "Quantity is required for a batch." in page
    page = text(add_batch(client, quantity="lots"))
    assert "Quantity must be a whole number" in page


def test_take_out_and_add_with_low_stock(client):
    add_batch(client)
    device_id = 1
    page = text(client.post(f"/devices/{device_id}/quantity", data={"amount": "35", "action": "remove"},
                            follow_redirects=True))
    assert "Took out 35. Now 5 on hand." in page and "Low stock" in page
    assert "Quantity" in page and "40 → 5" in page  # history
    listing = text(client.get("/"))
    assert "Low stock" in listing
    assert "USB-C charger" in text(client.get("/?stock=low"))
    page = text(client.post(f"/devices/{device_id}/quantity", data={"amount": "9", "action": "remove"},
                            follow_redirects=True))
    assert "Only 5 on hand" in page
    page = text(client.post(f"/devices/{device_id}/quantity", data={"amount": "20", "action": "add"},
                            follow_redirects=True))
    assert "Added 20. Now 25 on hand." in page
    assert "USB-C charger" not in text(client.get("/?stock=low"))


def test_quantity_route_only_for_batches(client):
    client.post("/devices/new", data={"asset_tag": "CB-1", "name": "Chromebook", "status": "In Service"})
    assert client.post("/devices/1/quantity", data={"amount": "1", "action": "add"}).status_code == 404


def test_import_batches_matched_by_name_and_location(client):
    csv_text = ("asset_tag,name,category,location,status,quantity,min_quantity\n"
                ",HDMI cable,Peripheral,Tech Office,In Storage,30,5\n"
                "CB-9,Chromebook,Chromebook,Room W401,In Service,,\n")
    page = text(client.post("/import", data={"file": (io.BytesIO(csv_text.encode()), "a.csv")},
                            content_type="multipart/form-data"))
    assert "Added 2, updated 0" in page
    again = ",HDMI cable,,Tech Office,,12,\n"
    page = text(client.post("/import", data={"file": (io.BytesIO(
        ("asset_tag,name,category,location,status,quantity,min_quantity\n" + again).encode()), "b.csv")},
        content_type="multipart/form-data"))
    assert "Added 0, updated 1" in page
    db = sqlite3.connect(client.application.config["DATABASE"])
    assert db.execute("SELECT quantity, min_quantity, is_batch FROM devices WHERE name='HDMI cable'"
                      ).fetchone() == (12, 5, 1)
    assert db.execute("SELECT quantity, is_batch FROM devices WHERE asset_tag='CB-9'").fetchone() == (1, 0)
    exported = text(client.get("/export.csv"))
    assert exported.splitlines()[0].endswith("quantity,min_quantity")


def test_old_database_is_upgraded_keeping_history_and_tickets(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            building TEXT, room TEXT, notes TEXT);
        CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_tag TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL, category TEXT, manufacturer TEXT, model TEXT, model_number TEXT,
            serial_number TEXT, manufacture_date TEXT,
            location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL, assigned_to TEXT,
            status TEXT NOT NULL DEFAULT 'In Service', purchase_date TEXT, warranty_expires TEXT,
            replacement_date TEXT, notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE device_history (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            changed_at TEXT NOT NULL DEFAULT (datetime('now')), field TEXT NOT NULL, old_value TEXT, new_value TEXT);
        CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE, ticket_number TEXT,
            opened_on TEXT NOT NULL, summary TEXT, status TEXT NOT NULL DEFAULT 'Open', closed_on TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO locations (name) VALUES ('Tech Office');
        INSERT INTO devices (asset_tag, name, location_id, serial_number) VALUES ('CB-494', 'Chromebook', 1, 'NX1');
        INSERT INTO device_history (device_id, field, new_value) VALUES (1, 'created', 'Tech Office');
        INSERT INTO tickets (device_id, ticket_number, opened_on) VALUES (1, '1042', '2026-10-01');
    """)
    db.commit()
    db.close()
    client = create_app({"TESTING": True, "DATABASE": str(path)}).test_client()
    page = text(client.get("/devices/1"))
    assert "CB-494" in page and "NX1" in page and "1042" in page and "Added to inventory" in page
    db = sqlite3.connect(path)
    cols = {r[1]: r for r in db.execute("PRAGMA table_info(devices)")}
    assert cols["asset_tag"][3] == 0 and "is_batch" in cols and "quantity" in cols
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert "Added Mice (batch)." in text(add_batch(client, name="Mice"))


# ------------------------------------------------------------------ backups

def test_download_backup_zip(client):
    client.post("/settings", data={"anthropic_api_key": "sk-ant-secret-1234", "helpdesk_email": "h@x.org"})
    add_batch(client)
    resp = client.get("/backup/download")
    assert resp.status_code == 200 and resp.mimetype == "application/zip"
    assert "Device Inventory backup" in resp.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(resp.data))
    assert set(z.namelist()) == {"inventory.db", "settings.json", "How to restore.txt"}
    settings = json.loads(z.read("settings.json"))
    assert settings["helpdesk_email"] == "h@x.org"
    assert "anthropic_api_key" not in settings and "secret_key" not in settings
    restored = z.extract("inventory.db", path=os.path.dirname(client.application.config["DATABASE"]) + "/r")
    assert sqlite3.connect(restored).execute("SELECT name FROM devices").fetchone() == ("USB-C charger",)


def test_back_up_now_to_folder(client, tmp_path):
    folder = tmp_path / "drive"
    page = text(client.post("/backup/now", data={"backup_dir": str(folder)}, follow_redirects=True))
    assert "Enter a backup folder that exists first" in page
    folder.mkdir()
    page = text(client.post("/backup/now", data={"backup_dir": str(folder)}, follow_redirects=True))
    assert "Backed up to" in page and "Last backup:" in page
    files = os.listdir(folder)
    assert len(files) == 1 and files[0].startswith("Device Inventory backup") and files[0].endswith(".zip")
    # The folder was remembered, so the button works without typing it again.
    os.remove(folder / files[0])
    client.post("/backup/now", data={})
    assert len(os.listdir(folder)) == 1


def test_backup_folder_setting_validated(client, tmp_path):
    page = text(client.post("/settings", data={"backup_dir": str(tmp_path / "nope")}, follow_redirects=True))
    assert "doesn&#39;t exist" in page
    client.post("/settings", data={"backup_dir": str(tmp_path)})
    assert f'value="{tmp_path}"' in text(client.get("/settings"))


def test_backups_only_from_this_computer(client):
    client.post("/settings", data={"allow_network": "1"})
    phone = {"REMOTE_ADDR": "192.0.2.50"}
    assert client.get("/backup/download", environ_base=phone).status_code == 403
    assert client.post("/backup/now", environ_base=phone).status_code == 403
    assert client.get("/", environ_base=phone).status_code == 200


# ------------------------------------------------------------------ data folder

def test_start_script_data_moves_to_documents(tmp_path, monkeypatch):
    app_dir, data_dir = tmp_path / "app", tmp_path / "Documents" / "Device Inventory"
    app_dir.mkdir()
    sqlite3.connect(app_dir / "inventory.db").execute("CREATE TABLE x (y)").connection.commit()
    (app_dir / "settings.json").write_text('{"helpdesk_email": "h@x.org"}')
    monkeypatch.setattr(app_settings, "RESOURCE_DIR", str(app_dir))
    assert app_settings.move_data_from_app_folder(str(data_dir)) == ("moved", str(app_dir / "inventory.db"))
    assert (data_dir / "inventory.db").exists() and (data_dir / "settings.json").exists()
    assert (app_dir / "inventory.db.moved").exists() and not (app_dir / "inventory.db").exists()
    # Nothing left to move the next time.
    assert app_settings.move_data_from_app_folder(str(data_dir)) is None


def test_start_script_data_left_alone_when_documents_has_data(tmp_path, monkeypatch):
    app_dir, data_dir = tmp_path / "app", tmp_path / "data"
    app_dir.mkdir()
    data_dir.mkdir()
    (app_dir / "inventory.db").write_bytes(b"old")
    (data_dir / "inventory.db").write_bytes(b"new")
    monkeypatch.setattr(app_settings, "RESOURCE_DIR", str(app_dir))
    assert app_settings.move_data_from_app_folder(str(data_dir))[0] == "both"
    assert (app_dir / "inventory.db").read_bytes() == b"old"
    assert (data_dir / "inventory.db").read_bytes() == b"new"


def test_default_data_dir_is_documents_when_run_from_source(monkeypatch, tmp_path):
    monkeypatch.delenv("INVENTORY_DATA_DIR", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    (tmp_path / "Documents").mkdir()
    assert app_settings.default_data_dir() == str(tmp_path / "Documents" / "Device Inventory")
    assert re.search(r"Device Inventory$", app_settings.default_data_dir())
