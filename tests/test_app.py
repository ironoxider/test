import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})
    return app.test_client()


def add_device(client, **overrides):
    data = {"asset_tag": "HS-001", "name": "Teacher laptop", "status": "In Service",
            "manufacturer": "Dell", "model": "Latitude 5440", "model_number": "P169G",
            "serial_number": "ABC123", "new_location": "Room 204"}
    data.update(overrides)
    return client.post("/devices/new", data=data, follow_redirects=True)


def test_add_and_list_device(client):
    resp = add_device(client)
    assert resp.status_code == 200
    assert b"Added HS-001" in resp.data
    page = client.get("/").data
    assert b"HS-001" in page and b"P169G" in page and b"Room 204" in page


def test_duplicate_asset_tag_rejected(client):
    add_device(client)
    resp = add_device(client, asset_tag="hs-001")
    assert b"already in use" in resp.data


def test_required_fields_and_date_validation(client):
    resp = add_device(client, asset_tag="", name="", purchase_date="13/01/2024")
    assert b"Asset tag is required" in resp.data
    assert b"Name is required" in resp.data
    assert b"YYYY-MM-DD" in resp.data


def test_search_and_filter(client):
    add_device(client)
    add_device(client, asset_tag="HS-002", name="Projector", serial_number="ZZZ999",
               new_location="Library", status="In Repair")
    assert b"HS-002" not in client.get("/?q=ABC123").data
    assert b"HS-002" in client.get("/?q=zzz999").data
    page = client.get("/?status=In+Repair").data
    assert b"HS-002" in page and b"HS-001" not in page


def test_edit_records_history(client):
    add_device(client)
    client.post("/devices/1/edit", data={"asset_tag": "HS-001", "name": "Teacher laptop",
                                         "status": "In Repair", "new_location": "IT Office"})
    page = client.get("/devices/1").data
    assert b"Room 204" in page and b"IT Office" in page
    assert b"In Service" in page and b"In Repair" in page


def test_location_delete_blocked_when_in_use(client):
    add_device(client)
    resp = client.post("/locations/1/delete", follow_redirects=True)
    assert b"still at this location" in resp.data
    client.post("/devices/1/delete")
    resp = client.post("/locations/1/delete", follow_redirects=True)
    assert b"Location deleted" in resp.data


def test_export_and_import_roundtrip(client):
    add_device(client)
    csv_data = client.get("/export.csv").data.decode()
    assert csv_data.splitlines()[0].startswith("asset_tag,name")
    assert "HS-001" in csv_data and "Room 204" in csv_data

    upload = ("Asset Tag,Name,Model Number,Location,Status\n"
              "HS-001,,NEWMODEL,,\n"
              "HS-100,Chromebook cart,C100,Cart A,in storage\n"
              "HS-101,Bad date,,,Nope\n")
    resp = client.post("/import", data={"file": (io.BytesIO(upload.encode()), "devices.csv")},
                       content_type="multipart/form-data")
    assert b"Added 1, updated 1" in resp.data
    assert b"Row 4" in resp.data

    page = client.get("/devices/1").data
    assert b"NEWMODEL" in page and b"Teacher laptop" in page and b"Room 204" in page
    page = client.get("/?q=HS-100").data
    assert b"Cart A" in page and b"In Storage" in page
