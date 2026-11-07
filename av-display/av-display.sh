#!/bin/bash
# AV Display: endless full-screen slideshow of photos and videos from a folder.
# Made for Raspberry Pi (including the original Pi 1 B+) running Raspberry Pi OS Lite, no desktop.
#
#   Videos  -> omxplayer (uses the Pi's hardware video decoder, needed on a B+)
#              falls back to mpv on systems without omxplayer
#   Photos  -> fbi (draws straight to the screen, no desktop needed)
#
# Settings come from /etc/av-display.conf (see av-display.conf). The folder is re-read every
# time round, so files you add or delete show up on the next pass without restarting.

CONF=/etc/av-display.conf
MEDIA_DIR=/home/pi/media
IMAGE_SECONDS=10
SHUFFLE=no
AUDIO=hdmi
USE_USB=yes
USB_FOLDER=slideshow
[ -f "$CONF" ] && . "$CONF"

TTY=/dev/tty1
USB_MOUNT=/mnt/av-display-usb
IMAGE_EXT="jpg jpeg png gif bmp"
VIDEO_EXT="mp4 m4v mov mkv avi h264 mpg mpeg"
FBI_PID=

log() { echo "av-display: $*" >&2; }

has_ext() {   # has_ext FILE "ext list"
    local ext="${1##*.}"; ext="${ext,,}"
    [[ " $2 " == *" $ext "* ]]
}

screen_setup() {
    # No cursor, no screen blanking, no text on the screen.
    setterm --cursor off --blank 0 --powersave off >"$TTY" 2>/dev/null
    clear >"$TTY" 2>/dev/null
    dmesg --console-off 2>/dev/null
}

stop_image() {
    if [ -n "$FBI_PID" ]; then
        kill "$FBI_PID" 2>/dev/null
        wait "$FBI_PID" 2>/dev/null
        FBI_PID=
    fi
    pkill -x fbi 2>/dev/null
}

show_image() {
    local old="$FBI_PID"
    # -T 1: draw on the first console  -a: fit to screen  --noverbose: no file name bar
    fbi -T 1 -a --noverbose --nocomments "$1" </dev/null >/dev/null 2>&1 &
    FBI_PID=$!
    # Let the new photo appear before closing the old one, to avoid a black flash.
    sleep 1
    [ -n "$old" ] && kill "$old" 2>/dev/null && wait "$old" 2>/dev/null
    sleep $(( IMAGE_SECONDS > 1 ? IMAGE_SECONDS - 1 : 1 ))
}

play_video() {
    stop_image
    if command -v omxplayer >/dev/null; then
        # -b: black background  --no-osd/--no-keys: nothing on screen, ignore the keyboard
        omxplayer -b --no-osd --no-keys --aspect-mode letterbox -o "$AUDIO" "$1" \
            </dev/null >/dev/null 2>&1
    elif command -v mpv >/dev/null; then
        mpv --really-quiet --fs --no-osc --osd-level=0 --vo=drm --hwdec=auto "$1" \
            </dev/null >/dev/null 2>&1
    else
        log "no video player installed (omxplayer or mpv); skipping $1"
        sleep 1
    fi
    screen_setup
}

pick_folder() {
    # A USB stick with a folder called "slideshow" (or files at the top) wins over MEDIA_DIR.
    if [ "$USE_USB" = yes ]; then
        if ! mountpoint -q "$USB_MOUNT"; then
            local dev
            for dev in /dev/sd[a-z]1 /dev/sd[a-z]; do
                [ -b "$dev" ] || continue
                mkdir -p "$USB_MOUNT"
                mount -o ro "$dev" "$USB_MOUNT" 2>/dev/null && break
            done
        elif ! ls "$USB_MOUNT" >/dev/null 2>&1; then
            umount -l "$USB_MOUNT" 2>/dev/null   # stick was pulled out
        fi
        if mountpoint -q "$USB_MOUNT"; then
            if [ -d "$USB_MOUNT/$USB_FOLDER" ]; then echo "$USB_MOUNT/$USB_FOLDER"; return; fi
            echo "$USB_MOUNT"; return
        fi
    fi
    echo "$MEDIA_DIR"
}

list_media() {
    local f
    find "$1" -type f ! -name '.*' 2>/dev/null | sort | while IFS= read -r f; do
        if has_ext "$f" "$IMAGE_EXT" || has_ext "$f" "$VIDEO_EXT"; then echo "$f"; fi
    done | { if [ "$SHUFFLE" = yes ]; then shuf; else cat; fi; }
}

trap 'stop_image; setterm --cursor on >"$TTY" 2>/dev/null; exit 0' TERM INT
screen_setup

while true; do
    folder=$(pick_folder)
    mapfile -t files < <(list_media "$folder")
    if [ ${#files[@]} -eq 0 ]; then
        log "no photos or videos in $folder; checking again in 30 seconds"
        sleep 30
        continue
    fi
    for f in "${files[@]}"; do
        [ -f "$f" ] || continue
        if has_ext "$f" "$VIDEO_EXT"; then play_video "$f"; else show_image "$f"; fi
    done
done
