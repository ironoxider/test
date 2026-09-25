"""Read device details (make, model, model number, serial) from photos using Claude.

Requires an Anthropic API key, saved on the Settings page or set in the
ANTHROPIC_API_KEY environment variable.
"""

import base64
import json
import os
from datetime import date

import anthropic

DEFAULT_MODEL = "claude-opus-5"
ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024

EXTRACT_FIELDS = ["manufacturer", "model", "model_number", "serial_number", "manufacture_date",
                  "category"]

PROMPT = """These photos show an IT device and/or its identification label (the sticker \
on the bottom, back, or inside the battery bay). Read the label and identify the device \
so an inventory record can be filled in.

Fields:
- manufacturer: brand, e.g. "Dell", "HP", "Lenovo", "Apple", "Epson".
- model: marketing model name, e.g. "Latitude 5440", "MacBook Air 13-inch M2", "PowerLite 1781W".
- model_number: the regulatory/model/type number printed on the label, e.g. "P169G", \
"A2681", "20XW-00AB". For Lenovo, use the MTM ("Type"). For Apple, use the "Model Axxxx" number.
- serial_number: the serial number ("S/N", "Serial No.", Dell "Service Tag", Apple "Serial"). \
Copy it exactly as printed.
- manufacture_date: the manufacture date ("Mfg. Date", "Date of Manufacture", "MFD") as \
YYYY-MM-DD. If only a month and year are printed, use the 1st of that month and say so in notes. \
Use null if no manufacture date is printed; don't guess it from the model.
- category: one of the allowed values that best fits the device.

Rules:
- Only report text you can actually read. Use null for anything you can't read or aren't \
sure of - a blank field is much better than a wrong serial number.
- Don't confuse the serial with part numbers (P/N), UPC/EAN, MAC addresses, IMEI, FCC ID, \
or regulatory numbers.
- If a character is ambiguous (0/O, 1/I, 5/S, 8/B), say which in notes.
- notes: one short sentence about anything uncertain, or null if everything was clear."""


def _nullable_string():
    return {"type": ["string", "null"]}


def output_schema(categories):
    return {
        "type": "object",
        "properties": {
            "manufacturer": _nullable_string(),
            "model": _nullable_string(),
            "model_number": _nullable_string(),
            "serial_number": _nullable_string(),
            "manufacture_date": _nullable_string(),
            "category": {"anyOf": [{"type": "string", "enum": list(categories)}, {"type": "null"}]},
            "notes": _nullable_string(),
        },
        "required": EXTRACT_FIELDS + ["notes"],
        "additionalProperties": False,
    }


class ExtractionError(Exception):
    """A problem to show the user (bad input, API failure, refusal)."""


def is_configured(api_key=None):
    return bool(api_key or os.environ.get("ANTHROPIC_API_KEY"))


def extract_device_info(images, categories, client=None, api_key=None):
    """Return a dict of device fields read from `images`.

    `images` is a list of (bytes, media_type) tuples.
    """
    if not images:
        raise ExtractionError("No photos were uploaded.")
    if len(images) > MAX_IMAGES:
        raise ExtractionError(f"Upload at most {MAX_IMAGES} photos at a time.")
    content = []
    for data, media_type in images:
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise ExtractionError("Photos must be JPEG, PNG, WebP or GIF images.")
        if len(data) > MAX_IMAGE_BYTES:
            raise ExtractionError("A photo is too large (max 5 MB each).")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": PROMPT})

    # With no saved key, the SDK falls back to the ANTHROPIC_API_KEY environment variable.
    client = client or anthropic.Anthropic(api_key=api_key or None, timeout=90.0)
    try:
        response = client.beta.messages.create(
            model=os.environ.get("INVENTORY_AI_MODEL", DEFAULT_MODEL),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": output_schema(categories)},
            },
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise ExtractionError("The Anthropic API key is invalid. Check it on the Settings page.")
    except anthropic.PermissionDeniedError:
        raise ExtractionError("The Anthropic API key doesn't have access to this model.")
    except anthropic.RateLimitError:
        raise ExtractionError("Too many requests right now. Wait a minute and try again.")
    except anthropic.BadRequestError as e:
        raise ExtractionError(f"The photo couldn't be processed: {e.message}")
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"The AI service returned an error ({e.status_code}). Try again.")
    except anthropic.APIConnectionError:
        raise ExtractionError("Couldn't reach the AI service. Check the internet connection.")

    if response.stop_reason == "refusal":
        raise ExtractionError("The AI declined to read this photo. Enter the details manually.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The AI response was cut off. Try again with fewer photos.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("The AI didn't return any details. Try a clearer photo.")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise ExtractionError("The AI returned an unreadable answer. Try again.")

    fields = {}
    for key in EXTRACT_FIELDS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value.strip()
    if fields.get("category") not in categories:
        fields.pop("category", None)
    if "manufacture_date" in fields:
        try:
            parsed = date.fromisoformat(fields["manufacture_date"])
            if not (1980 <= parsed.year <= date.today().year):
                raise ValueError
        except ValueError:
            fields.pop("manufacture_date")
    notes = result.get("notes") if isinstance(result.get("notes"), str) else None
    return {"fields": fields, "notes": notes}
