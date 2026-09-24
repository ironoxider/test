#!/bin/bash
# Start Device Inventory from the source code (Mac, Linux, Chromebook).
# First run: sets up a private Python environment in .venv and installs what
# the app needs. After that it just starts the app and opens your browser.
cd "$(dirname "$0")" || exit 1

fail() {
    echo
    echo "$1"
    echo
    read -r -p "Press Enter to close." _
    exit 1
}

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$candidate"
        break
    fi
done
[ -n "$PY" ] || fail "Python 3.9 or newer is needed but wasn't found.
  Mac:                 install it from https://www.python.org/downloads/
  Chromebook / Linux:  sudo apt install python3 python3-venv"

if [ ! -x .venv/bin/python ]; then
    echo "First-time setup: installing what Device Inventory needs (about a minute)..."
    rm -rf .venv
    if ! "$PY" -m venv .venv; then
        rm -rf .venv
        fail "Couldn't create the Python environment.
  Chromebook / Linux: run  sudo apt install python3-venv  and then try again."
    fi
    FRESH=1
fi

if ! .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt; then
    if [ -n "$FRESH" ]; then
        rm -rf .venv
        fail "Installing the app's add-ons failed (see the messages above). Check the internet connection and try again."
    fi
    echo "Couldn't check for updates to the app's add-ons (offline?). Starting anyway..."
fi

.venv/bin/python launcher.py
