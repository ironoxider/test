#!/bin/bash
# Show the Help Desk board full-screen on the Pi's monitor once Device Inventory is up.
# Started from the desktop's autostart (see PI_SETUP.md).
URL="${BOARD_URL:-http://127.0.0.1:5000/helpdesk/board}"
for _ in $(seq 1 60); do
    curl -fs -o /dev/null "$URL" && break
    sleep 2
done
BROWSER=$(command -v chromium-browser || command -v chromium)
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --check-for-update-interval=31536000 "$URL"
