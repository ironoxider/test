"""Where the app keeps its data, and the small settings file stored next to the database."""

import json
import os
import secrets
import socket
import sys
import time

# Shown at the bottom of every page; bump it with each release so people can tell
# which version they're running (and so the launcher can spot an older copy).
APP_VERSION = "1.5"

FROZEN = getattr(sys, "frozen", False)  # True when running as a packaged (PyInstaller) app

# Read-only files that ship with the app (templates, static, schema.sql).
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


_lan_cache = (0.0, [])


def lan_addresses():
    """This computer's addresses on the local network, most likely one first."""
    global _lan_cache
    if time.time() - _lan_cache[0] < 60:
        return _lan_cache[1]
    found = []
    try:
        # Doesn't send anything; just picks the interface used for outbound traffic.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    addrs = []
    for a in found:
        if a not in addrs and not a.startswith(("127.", "169.254.", "0.")):
            addrs.append(a)
    _lan_cache = (time.time(), addrs)
    return addrs


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
