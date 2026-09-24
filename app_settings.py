"""Where the app keeps its data, and the small settings file stored next to the database."""

import json
import os
import secrets
import sys

FROZEN = getattr(sys, "frozen", False)  # True when running as a packaged (PyInstaller) app

# Read-only files that ship with the app (templates, static, schema.sql).
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def default_data_dir():
    """Folder for the database and settings.

    When running from source this is the project folder. The packaged app uses
    "Documents/Device Inventory" so the data is easy to find and back up.
    """
    if os.environ.get("INVENTORY_DATA_DIR"):
        return os.environ["INVENTORY_DATA_DIR"]
    if not FROZEN:
        return RESOURCE_DIR
    home = os.path.expanduser("~")
    documents = os.path.join(home, "Documents")
    return os.path.join(documents if os.path.isdir(documents) else home, "Device Inventory")


def default_db_path():
    return os.environ.get("INVENTORY_DB") or os.path.join(default_data_dir(), "inventory.db")


def settings_path(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "settings.json")


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    if os.name != "nt":
        os.chmod(tmp, 0o600)  # the file can hold an API key
    os.replace(tmp, path)


def update(path, **changes):
    data = load(path)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    save(path, data)
    return data


def secret_key(path):
    """Session signing key: from the environment, or generated once and saved."""
    if os.environ.get("INVENTORY_SECRET_KEY"):
        return os.environ["INVENTORY_SECRET_KEY"]
    data = load(path)
    if not data.get("secret_key"):
        data = update(path, secret_key=secrets.token_hex(32))
    return data["secret_key"]
