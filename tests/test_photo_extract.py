import io
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import photo_extract  # noqa: E402
from app import CATEGORIES, create_app  # noqa: E402


class FakeClient:
    """Stands in for anthropic.Anthropic; records the request and returns `reply`."""

    def __init__(self, reply, stop_reason="end_turn"):
        self.calls = []
        response = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(reply))],
        )

        def create(**kwargs):
            self.calls.append(kwargs)
            return response

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


LABEL_REPLY = {"manufacturer": "Dell", "model": "Latitude 5440", "model_number": "P169G",
               "serial_number": " 7XK2QW3 ", "category": "Laptop", "notes": None}


def test_extract_builds_request_and_cleans_result():
    client = FakeClient(LABEL_REPLY)
    result = photo_extract.extract_device_info([(b"jpegdata", "image/jpeg")], CATEGORIES, client)
    assert result["fields"] == {"manufacturer": "Dell", "model": "Latitude 5440",
                                "model_number": "P169G", "serial_number": "7XK2QW3",
                                "category": "Laptop"}
    call = client.calls[0]
    assert call["model"] == photo_extract.DEFAULT_MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"


def test_extract_drops_unknown_category_and_blanks():
    client = FakeClient(dict(LABEL_REPLY, category="Toaster", model=" ", serial_number=None))
    fields = photo_extract.extract_device_info([(b"x", "image/png")], CATEGORIES, client)["fields"]
    assert "category" not in fields and "model" not in fields and "serial_number" not in fields


@pytest.mark.parametrize("images,message", [
    ([], "No photos"),
    ([(b"x", "image/heic")], "JPEG, PNG"),
    ([(b"x", "image/jpeg")] * 5, "at most"),
])
def test_extract_rejects_bad_input(images, message):
    with pytest.raises(photo_extract.ExtractionError, match=message):
        photo_extract.extract_device_info(images, CATEGORIES, FakeClient(LABEL_REPLY))


def test_extract_refusal():
    client = FakeClient({}, stop_reason="refusal")
    with pytest.raises(photo_extract.ExtractionError, match="declined"):
        photo_extract.extract_device_info([(b"x", "image/jpeg")], CATEGORIES, client)


@pytest.fixture
def client(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")}).test_client()


def test_api_extract_not_configured(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/api/extract")
    assert resp.status_code == 503 and "Settings" in resp.get_json()["error"]
    assert b"AI label reading is off" in client.get("/devices/new").data


def test_api_extract_success(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}

    def fake_extract(images, categories, api_key=None):
        seen["images"] = images
        return {"fields": {"serial_number": "7XK2QW3"}, "notes": None}

    monkeypatch.setattr(photo_extract, "extract_device_info", fake_extract)
    resp = client.post("/api/extract", content_type="multipart/form-data",
                       data={"photos": (io.BytesIO(b"img"), "label.jpg", "image/jpeg")})
    assert resp.status_code == 200
    assert resp.get_json()["fields"]["serial_number"] == "7XK2QW3"
    assert seen["images"] == [(b"img", "image/jpeg")]
    assert b"Read label with AI" in client.get("/devices/new").data


def test_api_extract_error_is_json(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    resp = client.post("/api/extract", content_type="multipart/form-data", data={})
    assert resp.status_code == 400 and "No photos" in resp.get_json()["error"]


def test_settings_saves_key_and_enables_ai(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/settings", data={"anthropic_api_key": "not-a-key"}, follow_redirects=True)
    assert b"look like an Anthropic API key" in resp.data
    resp = client.post("/settings", data={"anthropic_api_key": "sk-ant-test-1234"},
                       follow_redirects=True)
    assert b"Settings saved" in resp.data and "…1234".encode() in resp.data
    assert b"sk-ant-test-1234" not in resp.data  # never echoed back
    assert b"Read label with AI" in client.get("/devices/new").data

    seen = {}
    monkeypatch.setattr(photo_extract, "extract_device_info",
                        lambda images, categories, api_key=None: seen.update(key=api_key)
                        or {"fields": {}, "notes": None})
    client.post("/api/extract", content_type="multipart/form-data",
                data={"photos": (io.BytesIO(b"img"), "a.jpg", "image/jpeg")})
    assert seen["key"] == "sk-ant-test-1234"

    client.post("/settings", data={"remove_key": "1"})
    assert b"AI label reading is off" in client.get("/devices/new").data


def test_settings_network_toggle(client):
    resp = client.post("/settings", data={"allow_network": "1"}, follow_redirects=True)
    assert b"Close and reopen" in resp.data
    assert b'name="allow_network" value="1" checked' in resp.data


def test_form_accepts_heic_photos(client):
    page = client.get("/devices/new").data
    assert b'accept="image/*,.heic,.heif"' in page
    assert b"vendor/heic2any.min.js" in page
    assert client.get("/static/vendor/heic2any.min.js").status_code == 200
