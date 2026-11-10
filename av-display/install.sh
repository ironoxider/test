#!/bin/bash
# Installs AV Display on Raspberry Pi OS (Lite). Run from this folder: sudo ./install.sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo ./install.sh"; exit 1; }
cd "$(dirname "$0")"

USER_NAME=${SUDO_USER:-pi}
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)

echo "== Installing programs"
apt-get update
apt-get install -y fbi cifs-utils rsync
if apt-cache show omxplayer >/dev/null 2>&1; then
    apt-get install -y omxplayer
else
    echo "omxplayer is not available on this OS version; installing mpv instead."
    echo "(On a Pi 1 B+ use Raspberry Pi OS Legacy 'Buster' Lite for smooth video; see README.)"
    apt-get install -y mpv
fi

echo "== Installing AV Display"
install -m 755 av-display.sh /usr/local/bin/av-display
if [ -f /etc/av-display.conf ]; then
    echo "Keeping your existing /etc/av-display.conf"
    if ! grep -q '^NETWORK_SHARE=' /etc/av-display.conf; then
        sed -n '/^# ---- Network folder/,$p' av-display.conf >>/etc/av-display.conf
        echo "Added the network folder settings to /etc/av-display.conf"
    fi
else
    sed "s#/home/pi/media#$HOME_DIR/media#" av-display.conf >/etc/av-display.conf
fi
# Root-only: it can hold the network folder password.
chmod 600 /etc/av-display.conf
mkdir -p "$HOME_DIR/media"
chown "$USER_NAME": "$HOME_DIR/media"

echo "== Pi settings for video"
BOOT=/boot/config.txt
[ -f /boot/firmware/config.txt ] && BOOT=/boot/firmware/config.txt
if command -v omxplayer >/dev/null; then
    # omxplayer needs video memory and the older graphics driver.
    grep -q '^gpu_mem=' "$BOOT" || echo 'gpu_mem=128' >>"$BOOT"
    sed -i 's/^dtoverlay=vc4-kms-v3d/#dtoverlay=vc4-kms-v3d/; s/^dtoverlay=vc4-fkms-v3d/#dtoverlay=vc4-fkms-v3d/' "$BOOT"
fi
# Keep the TV signal on even if no TV was connected at power-on.
grep -q '^hdmi_force_hotplug=' "$BOOT" || echo 'hdmi_force_hotplug=1' >>"$BOOT"
CMDLINE=/boot/cmdline.txt
[ -f /boot/firmware/cmdline.txt ] && CMDLINE=/boot/firmware/cmdline.txt
# No screen blanking, no boot messages or logo on the screen.
grep -q 'consoleblank=0' "$CMDLINE" || sed -i '1 s/$/ consoleblank=0 logo.nologo quiet loglevel=1 vt.global_cursor_default=0/' "$CMDLINE"

echo "== Starting at power-on"
install -m 644 av-display.service /etc/systemd/system/av-display.service
systemctl daemon-reload
systemctl enable av-display.service

echo
echo "Done. Put photos and videos in $HOME_DIR/media (or on a USB stick), then: sudo reboot"
