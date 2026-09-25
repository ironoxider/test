import io
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import photo_extract  # noqa: E402
from app import create_app  # noqa: E402
from tests.test_photo_extract import FakeClient, LABEL_REPLY  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})


@pytest.fixture
def client(app):
    return app.test_client()


def add(client, **fields):
    data = {"asset_tag": "HS-1", "name": "Laptop", "status": "In Service"}
    data.update(fields)
    return client.post("/devices/new", data=data, follow_redirects=True)


def db_row(app, sql, *params):
    con = sqlite3.connect(app.config["DATABASE"])
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


# ------------------------------------------------------------ new fields

def test_manufacture_date_saved_and_validated(client):
    page = add(client, manufacture_date="2023-05-01").data
    assert b"Manufacture Date" in page and b"2023-05-01" in page
    assert b"Manufacture Date must be a date" in add(client, asset_tag="HS-2",
                                                      manufacture_date="May 2023").data


@pytest.mark.parametrize("fields,expected", [
    ({"purchase_date": "2024-08-15"}, "2027-08-15"),
    ({"manufacture_date": "2023-01-10"}, "2026-01-10"),                    # no purchase date
    ({"purchase_date": "2024-02-29"}, "2027-02-28"),                       # leap day
    ({"purchase_date": "2024-08-15", "replacement_date": "2025-12-31"}, "2025-12-31"),  # typed in
    ({}, None),
])
def test_replacement_date_defaults(app, client, fields, expected):
    add(client, **fields)
    assert db_row(app, "SELECT replacement_date FROM devices")[0] == expected


def test_lifespan_setting_changes_default(app, client):
    client.post("/settings", data={"lifespan_years": "5", "reminder_days": "30"})
    add(client, purchase_date="2024-08-15")
    assert db_row(app, "SELECT replacement_date FROM devices")[0] == "2029-08-15"
    assert b'name="lifespan_years" min="1" max="20" value="5"' in client.get("/settings").data


def test_replacement_reminders(client):
    today = date.today()
    add(client, asset_tag="OLD", replacement_date=(today - timedelta(days=10)).isoformat())
    add(client, asset_tag="SOON", replacement_date=(today + timedelta(days=20)).isoformat())
    add(client, asset_tag="LATER", replacement_date=(today + timedelta(days=400)).isoformat())
    add(client, asset_tag="GONE", status="Retired",
        replacement_date=(today - timedelta(days=10)).isoformat())
    page = client.get("/").data.decode()
    assert "Replacement reminder" in page
    assert "1 device(s) past their replacement date, 1 due in the next 90 days" in page
    due = client.get("/?replace=due").data.decode()
    assert "OLD" in due and "SOON" in due and "LATER" not in due and "GONE" not in due


def test_no_reminder_when_nothing_due(client):
    add(client, replacement_date=(date.today() + timedelta(days=400)).isoformat())
    assert "Replacement reminder" not in client.get("/").data.decode()


def test_assigned_to_offers_past_entries(client):
    add(client, asset_tag="A", assigned_to="Ms. Rivera")
    add(client, asset_tag="B", assigned_to="Science Dept")
    add(client, asset_tag="C", assigned_to="ms. rivera")  # same person, different case
    page = client.get("/devices/new").data.decode()
    assert '<datalist id="people">' in page
    assert page.count('value="Ms. Rivera"') + page.count('value="ms. rivera"') == 1
    assert 'value="Science Dept"' in page


# ------------------------------------------------------------ managed lists

def test_new_category_and_status_from_form(app, client):
    add(client, category="Smart Board", status="On Order")
    lists = client.get("/lists").data.decode()
    assert "Smart Board" in lists and "On Order" in lists
    form = client.get("/devices/new").data.decode()
    assert "<option >Smart Board</option>" in form or "<option>Smart Board</option>" in form


def test_failed_save_does_not_keep_new_option(client):
    add(client, asset_tag="", category="Typo Category")
    assert "Typo Category" not in client.get("/lists").data.decode()


def test_rename_updates_devices_and_history(app, client):
    add(client, category="Laptop")
    opt = db_row(app, "SELECT id FROM options WHERE kind='category' AND name='Laptop'")[0]
    resp = client.post(f"/lists/option/{opt}/rename", data={"name": "Notebook"},
                       follow_redirects=True)
    assert b"on 1 device(s)" in resp.data
    assert db_row(app, "SELECT category FROM devices")[0] == "Notebook"
    history = client.get("/devices/1").data.decode()
    assert "Laptop → Notebook" in history


def test_rename_to_existing_name_is_refused(app, client):
    opt = db_row(app, "SELECT id FROM options WHERE kind='category' AND name='Laptop'")[0]
    resp = client.post(f"/lists/option/{opt}/rename", data={"name": "desktop"},
                       follow_redirects=True)
    assert b"already in the list" in resp.data


def test_delete_blocked_when_in_use(app, client):
    add(client, category="Tablet")
    opt = db_row(app, "SELECT id FROM options WHERE kind='category' AND name='Tablet'")[0]
    assert b"1 device(s) use it" in client.post(f"/lists/option/{opt}/delete",
                                               follow_redirects=True).data
    client.post("/devices/1/delete")
    assert b"Removed" in client.post(f"/lists/option/{opt}/delete", follow_redirects=True).data


def test_move_changes_order_and_default_status(app, client):
    opt = db_row(app, "SELECT id FROM options WHERE kind='status' AND name='In Storage'")[0]
    client.post(f"/lists/option/{opt}/move", data={"direction": "up"})
    form = client.get("/devices/new").data.decode()
    assert form.index("In Storage") < form.index("In Service")
    assert "<option selected>In Storage</option>" in form  # first status is the default


def test_add_duplicate_option_refused(client):
    resp = client.post("/lists/status/add", data={"name": "in service"}, follow_redirects=True)
    assert b"already in the list" in resp.data


def test_filters_use_managed_lists(client):
    client.post("/lists/category/add", data={"name": "3D Printer"})
    assert b"3D Printer" in client.get("/").data


# ------------------------------------------------------------ CSV + AI + upgrade

def test_csv_new_columns_and_unknown_values(app, client):
    upload = ("asset_tag,name,category,status,manufacture_date,purchase_date\n"
              "C-1,Cart,Charging Cart,In Service,2022-03-01,2022-06-01\n"
              "C-2,Thing,,Misplaced,,\n")
    resp = client.post("/import", data={"file": (io.BytesIO(upload.encode()), "d.csv")},
                       content_type="multipart/form-data")
    assert b"Added 1" in resp.data and b"Status must be one of" in resp.data
    row = db_row(app, "SELECT * FROM devices WHERE asset_tag='C-1'")
    assert row["category"] == "Charging Cart" and row["replacement_date"] == "2025-06-01"
    header = client.get("/export.csv").data.decode().splitlines()[0]
    assert "manufacture_date" in header and "replacement_date" in header


def test_ai_reads_manufacture_date():
    fields = photo_extract.extract_device_info(
        [(b"x", "image/jpeg")], ["Laptop"],
        FakeClient(dict(LABEL_REPLY, manufacture_date="2023-05-01")))["fields"]
    assert fields["manufacture_date"] == "2023-05-01"
    fields = photo_extract.extract_device_info(
        [(b"x", "image/jpeg")], ["Laptop"],
        FakeClient(dict(LABEL_REPLY, manufacture_date="05/2023")))["fields"]
    assert "manufacture_date" not in fields


def test_upgrades_database_from_older_version(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE
            COLLATE NOCASE, building TEXT, room TEXT, notes TEXT);
        CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_tag TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, category TEXT,
            manufacturer TEXT, model TEXT, model_number TEXT, serial_number TEXT,
            location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL, assigned_to TEXT,
            status TEXT NOT NULL DEFAULT 'In Service', purchase_date TEXT, warranty_expires TEXT,
            notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO devices (asset_tag, name, category) VALUES ('OLD-1', 'Old laptop', 'Smart Board');
    """)
    con.close()
    client = create_app({"TESTING": True, "DATABASE": str(path)}).test_client()
    assert b"OLD-1" in client.get("/").data
    assert b"Smart Board" in client.get("/lists").data  # custom category kept in the list
    edit = client.get("/devices/1/edit").data.decode()
    assert "<option selected>Smart Board</option>" in edit
    assert 'name="manufacture_date"' in edit and 'name="replacement_date"' in edit
