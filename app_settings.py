"""Where the app keeps its data, and the small settings file stored next to the database."""

import json
import os
import secrets
import shutil
import socket
import sys
import time

# Shown at the bottom of every page; bump it with each release so people can tell
# which version they're running (and so the launcher can spot an older copy).
APP_VERSION = "1.8"

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
    """Folder for the database and settings: "Documents/Device Inventory".

    Both the packaged app and the start scripts use it, so the data stays put when a newer
    version is downloaded and is easy to find and back up.
    """
    if os.environ.get("INVENTORY_DATA_DIR"):
        return os.environ["INVENTORY_DATA_DIR"]
    home = os.path.expanduser("~")
    documents = os.path.join(home, "Documents")
    return os.path.join(documents if os.path.isdir(documents) else home, "Device Inventory")


def move_data_from_app_folder(data_dir):
    """Versions before 1.8 run from the start scripts kept their data in the app's own folder.

    Move it to `data_dir` the first time a newer version starts. The old files are renamed
    (not deleted) so nothing is lost. Returns (outcome, old database path) or None, where
    outcome is "moved", or "both" if `data_dir` already had a database (nothing is moved).
    """
    if FROZEN:
        return None
    old_db = os.path.join(RESOURCE_DIR, "inventory.db")
    new_db = os.path.join(data_dir, "inventory.db")
    if not os.path.exists(old_db) or os.path.abspath(old_db) == os.path.abspath(new_db):
        return None
    if os.path.exists(new_db):
        return ("both", old_db)
    os.makedirs(data_dir, exist_ok=True)
    shutil.copy2(old_db, new_db)
    old_settings = os.path.join(RESOURCE_DIR, "settings.json")
    new_settings = os.path.join(data_dir, "settings.json")
    if os.path.exists(old_settings) and not os.path.exists(new_settings):
        shutil.copy2(old_settings, new_settings)
        os.replace(old_settings, old_settings + ".moved")
    os.replace(old_db, old_db + ".moved")
    return ("moved", old_db)


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
