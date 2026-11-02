"""Automatic daily backups: a dated .zip in the backup folder and/or emailed to an address.

Runs from the background loop (mailsync.start_background) once a day at the set time, or as
soon as Device Inventory starts if it wasn't running then. Only automatic backups are pruned
(the newest `backup_keep` are kept); ones made with "Back up now" are never deleted.
"""

import os
import re
import subprocess
import tempfile
from datetime import datetime

from flask import current_app

import app as core
import app_settings

PREFIX = "Device Inventory auto-backup "
DEFAULTS = {"backup_auto": False, "backup_time": "02:00", "backup_keep": 30, "backup_email": "",
            # Offsite copy with rclone (e.g. Backblaze B2): "remote:bucket/folder", and days to keep there.
            "backup_rclone": "", "backup_offsite_days": 90}
RCLONE_RE = re.compile(r"^[\w.-]+:\S.*$")


def config():
    data = app_settings.load(current_app.config["SETTINGS_PATH"])
    return {k: data.get(k, v) for k, v in DEFAULTS.items()} | {
        "backup_dir": data.get("backup_dir"), "last_auto_backup": data.get("last_auto_backup"),
        "auto_backup_result": data.get("auto_backup_result"), "auto_backup_error": data.get("auto_backup_error")}


def due(cfg, now=None):
    now = now or datetime.now()
    if not cfg["backup_auto"] or cfg.get("last_auto_backup") == now.strftime("%Y-%m-%d"):
        return False
    try:
        hour, minute = (int(x) for x in str(cfg["backup_time"]).split(":"))
    except ValueError:
        hour, minute = 2, 0
    return (now.hour, now.minute) >= (hour, minute)


def prune(folder, keep):
    """Delete the oldest automatic backups in `folder`, keeping the newest `keep`."""
    pattern = re.compile(re.escape(PREFIX) + r"\d{4}-\d{2}-\d{2}\.zip$")
    mine = sorted(name for name in os.listdir(folder) if pattern.match(name))
    for name in mine[:max(0, len(mine) - max(1, int(keep)))]:
        os.remove(os.path.join(folder, name))


def run(now=None, send_email=None):
    """Make today's automatic backup. Returns (what was done, error or None)."""
    now = now or datetime.now()
    cfg = config()
    folder, address = cfg["backup_dir"], (cfg["backup_email"] or "").strip()
    remote = (cfg["backup_rclone"] or "").strip()
    if not folder and not address and not remote:
        return None, "Choose a backup folder, an email address or an offsite copy for automatic backups."
    name = f"{PREFIX}{now:%Y-%m-%d}.zip"
    data = core.backup_bytes(current_app.config["DATABASE"], current_app.config["SETTINGS_PATH"])
    done, errors = [], []
    if folder:
        if not os.path.isdir(folder):
            errors.append(f"The backup folder {folder} isn't there (is the drive plugged in?).")
        else:
            try:
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(data)
                prune(folder, cfg["backup_keep"])
                done.append(f"saved to {folder}")
            except OSError as e:
                errors.append(f"Couldn't save to {folder}: {e.strerror or e}.")
    if remote:
        try:
            copy_offsite(remote, name, data, cfg["backup_offsite_days"])
            done.append(f"copied to {remote}")
        except OffsiteError as e:
            errors.append(f"The offsite copy to {remote} failed: {e}")
    if address:
        try:
            (send_email or _email_backup)(address, name, data)
            done.append(f"emailed to {address}")
        except Exception as e:  # report any email problem without losing the folder copy
            errors.append(f"Couldn't email it to {address}: {e}")
    result = f"{now:%Y-%m-%d %H:%M}: " + (", ".join(done) if done else "nothing saved")
    app_settings.update(current_app.config["SETTINGS_PATH"], last_auto_backup=now.strftime("%Y-%m-%d"),
                        auto_backup_result=result, auto_backup_error=" ".join(errors) or None)
    return result, (" ".join(errors) or None)


class OffsiteError(Exception):
    pass


def rclone(*args, timeout=900):
    """Run rclone; returns its output or raises OffsiteError with its last error line."""
    try:
        done = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise OffsiteError("rclone isn't installed (on the Pi: sudo apt install rclone)")
    except subprocess.TimeoutExpired:
        raise OffsiteError("rclone took too long (no internet?)")
    if done.returncode != 0:
        lines = [l for l in (done.stderr or done.stdout).strip().splitlines() if l.strip()]
        raise OffsiteError(lines[-1] if lines else f"rclone stopped with code {done.returncode}")
    return done.stdout


def copy_offsite(remote, name, data, keep_days):
    """Upload one backup with rclone, then delete automatic backups there older than keep_days."""
    target = remote.rstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        rclone("copyto", path, f"{target}/{name}")
    if keep_days and int(keep_days) > 0:
        # --b2-hard-delete removes old copies for good on Backblaze B2 (other remotes ignore it).
        rclone("delete", target, "--min-age", f"{int(keep_days)}d", "--include", f"{PREFIX}*.zip",
               "--b2-hard-delete")


def _email_backup(address, name, data):
    import mailsync
    mailsync.send_attachment(address, f"[Device Inventory] Backup {name[len(PREFIX):-4]}",
                             "The daily Device Inventory backup is attached. See How to restore.txt inside.",
                             name, data)


def tick():
    """Called from the background loop every few seconds."""
    if due(config()):
        _, error = run()
        if error:
            print(f"Automatic backup problem: {error}", flush=True)
