"""Start Device Inventory and open it in the web browser.

This is the entry point of the packaged (double-click) app. It can also be run
from source with:  python launcher.py
"""

import os
import re
import socket
import sys
import tempfile
import threading
import traceback
import urllib.request
import webbrowser

import app_settings

APP_NAME = "Device Inventory"
FIRST_PORT = 5000


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            # Match the server: a port released moments ago (TIME_WAIT) is reusable.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def running_version(port):
    """Version of the Device Inventory answering on this port.

    None if nothing (or something else) is there; "" for versions older than 1.4,
    which didn't report a version.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/settings", timeout=2) as resp:
            page = resp.read(4096).decode("utf-8", "replace")
    except Exception:
        return None
    if APP_NAME not in page:
        return None
    match = re.search(r'<meta name="app-version" content="([^"]*)"', page)
    return match.group(1) if match else ""


def lan_addresses():
    addrs = set()
    try:
        # Doesn't send anything; just picks the interface used for outbound traffic.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def selftest():
    """Start the app against a throwaway database and load each page. Used by the build."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["INVENTORY_DATA_DIR"] = tmp
        from app import create_app

        client = create_app({"TESTING": True}).test_client()
        for path in ["/", "/devices/new", "/locations", "/lists", "/import", "/settings",
                     "/static/vendor/zxing.min.js", "/static/vendor/heic2any.min.js",
                     "/static/photo-fill.js"]:
            status = client.get(path).status_code
            print(f"{status} {path}")
            if status != 200:
                return 1
        import anthropic  # noqa: F401  (make sure the AI library was bundled)
        import waitress  # noqa: F401
    print("selftest ok")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()

    from waitress import serve

    from app import create_app

    app = create_app()
    settings = app_settings.load(app.config["SETTINGS_PATH"])
    allow_network = settings.get("allow_network", False)
    host = os.environ.get("INVENTORY_HOST") or ("0.0.0.0" if allow_network else "127.0.0.1")

    port = int(os.environ.get("INVENTORY_PORT", FIRST_PORT))
    older_copy = None
    for candidate in range(port, port + 20):
        if port_free(host, candidate):
            port = candidate
            break
        version = running_version(candidate)
        if version == app_settings.APP_VERSION:
            print(f"{APP_NAME} is already running. Opening it in your browser.")
            webbrowser.open(f"http://127.0.0.1:{candidate}/")
            return 0
        if version is not None and older_copy is None:
            older_copy = (candidate, version or "an older version")
    else:
        print(f"Couldn't find a free port between {port} and {port + 19}.")
        return 1

    local_url = f"http://127.0.0.1:{port}/"
    network_urls = [f"http://{a}:{port}/" for a in lan_addresses()] if host == "0.0.0.0" else []
    app.config["NETWORK_URLS"] = network_urls

    if older_copy:
        print("!" * 60)
        print(f" A different copy of {APP_NAME} ({older_copy[1]}) is still running")
        print(f" at http://127.0.0.1:{older_copy[0]}/ - its window may be minimized.")
        print(" Close that window so you don't end up using the old one.")
        print("!" * 60)
        print()
    print("=" * 60)
    print(f" {APP_NAME} {app_settings.APP_VERSION} is running.")
    print(f" Open in your browser: {local_url}")
    for url in network_urls:
        print(f" On a phone on the same network: {url}")
    print(f" Your data is saved in: {os.path.abspath(app.config['DATABASE'])}")
    print()
    print(" Keep this window open while you use it.")
    print(" To stop, close this window (or press Ctrl+C).")
    print("=" * 60, flush=True)

    if not os.environ.get("INVENTORY_NO_BROWSER"):
        threading.Timer(1.0, webbrowser.open, args=[local_url]).start()
    serve(app, host=host, port=port, threads=8, _quiet=True)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 0
    except ModuleNotFoundError as e:
        print(f"\nDevice Inventory can't start: the Python add-on '{e.name}' isn't installed.")
        if os.name == "nt":
            print('Double-click "Start Device Inventory.bat" instead; it installs everything automatically.')
        else:
            print("Run ./start.sh instead (on a Mac, double-click \"Start Device Inventory.command\");")
            print("it installs everything automatically.")
        code = 1
    except Exception:
        traceback.print_exc()
        code = 1
    if code and app_settings.FROZEN and "--selftest" not in sys.argv:
        # Keep the window open so the error can be read.
        input("\nSomething went wrong (details above). Press Enter to close.")
    sys.exit(code)
