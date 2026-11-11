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
#
# What is shown, first match wins: a plugged-in USB stick, then the local copy of the network
# folder (NETWORK_SHARE), then MEDIA_DIR.
#
#   sudo av-display --sync    copy the network folder now and show what happened (for testing)

CONF=/etc/av-display.conf
MEDIA_DIR=/home/pi/media
IMAGE_SECONDS=10
SHUFFLE=no
AUDIO=hdmi
USE_USB=yes
USB_FOLDER=slideshow
NETWORK_SHARE=
NETWORK_USER=
NETWORK_PASSWORD=
NETWORK_DOMAIN=
NETWORK_OPTIONS=
NETWORK_SYNC_MINUTES=15
[ -f "$CONF" ] && . "$CONF"

TTY=${AV_TTY:-/dev/tty1}
USB_MOUNT=/mnt/av-display-usb
IMAGE_EXT="jpg jpeg png gif bmp"
VIDEO_EXT="mp4 m4v mov mkv avi h264 mpg mpeg"
NET_MOUNT=/mnt/av-display-share
NET_COPY=/var/lib/av-display/network
FBI_PID=
SYNC_PID=

log() { echo "av-display: $*" >&2; }

has_ext() {   # has_ext FILE "ext list"
    local ext="${1##*.}"; ext="${ext,,}"
    [[ " $2 " == *" $ext "* ]]
}

screen_setup() {
    # No cursor, no screen blanking, and black text on black, so the console never shows
    # any writing in the moments between photos and videos.
    setterm --foreground black --background black --cursor off --blank 0 --powersave off \
        >"$TTY" 2>/dev/null
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

start_fbi() {   # start_fbi [fbi options] FILES...   (runs in the background, sets FBI_PID)
    # One fbi shows a whole run of photos with its own timer (-t), so there is no gap
    # between them: it keeps the old photo up while it opens the next one.
    # -T 1: first console  -a: fit to screen  --noverbose: no file name bar
    # fbi must read from the screen's console: with no input (</dev/null) it treats that
    # as "quit" and closes at once.
    fbi -T 1 -a --noverbose --nocomments -t "$IMAGE_SECONDS" "$@" <"$TTY" >/dev/null 2>&1 &
    FBI_PID=$!
}

show_images() {   # show_images FILES...   shows each once, returns after the last one
    start_fbi --once "$@"
    local started=$SECONDS
    wait "$FBI_PID" 2>/dev/null
    FBI_PID=
    if [ $(( SECONDS - started )) -lt 2 ]; then
        log "fbi closed right after opening $1 (unreadable photo, or no access to $TTY)"
        sleep 2
    fi
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

sync_network() {
    # Copy the network folder to the SD card. Playing from the local copy keeps videos smooth
    # on a B+ (its network is slow) and keeps the slideshow going when the network is down.
    # Only changed files are copied; files deleted from the share are deleted from the copy.
    local creds=/run/av-display.credentials opts=ro,uid=0,gid=0
    # Accept the share written any common way: smb://server/share, \\server\share, //server/share
    local share
    share=$(printf '%s' "$NETWORK_SHARE" | sed -E 's#\\#/#g; s#^[Ss][Mm][Bb]:##; s#^/*#//#; s#/+$##')
    mkdir -p "$NET_MOUNT" "$NET_COPY"
    mountpoint -q "$NET_MOUNT" && umount -l "$NET_MOUNT"
    if [ -n "$NETWORK_USER" ]; then
        ( umask 077
          printf 'username=%s\npassword=%s\n' "$NETWORK_USER" "$NETWORK_PASSWORD" >"$creds"
          [ -n "$NETWORK_DOMAIN" ] && printf 'domain=%s\n' "$NETWORK_DOMAIN" >>"$creds" )
        opts+=",credentials=$creds"
    else
        opts+=",guest"
    fi
    [ -n "$NETWORK_OPTIONS" ] && opts+=",$NETWORK_OPTIONS"

    local err
    if ! err=$(mount -t cifs "$share" "$NET_MOUNT" -o "$opts" 2>&1); then
        log "could not open network folder $share: $err"
        rm -f "$creds"
        return 1
    fi
    rm -f "$creds"
    # --modify-window: Windows/NAS file times are only accurate to 2 seconds.
    # Hidden files are skipped; rsync's own temporary files are hidden, so a half-copied
    # video is never shown.
    if err=$(rsync -rt --delete --modify-window=2 --exclude='.*' --stats \
             "$NET_MOUNT/" "$NET_COPY/" 2>&1); then
        log "network folder copied: $(echo "$err" | grep -E 'Number of (regular )?files transferred' | head -1)"
    else
        log "copying the network folder failed (is the SD card full?): $(echo "$err" | tail -3)"
    fi
    umount -l "$NET_MOUNT"
}

sync_loop() {
    while true; do
        sync_network
        sleep $(( NETWORK_SYNC_MINUTES * 60 ))
    done
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
    if [ -n "$NETWORK_SHARE" ] && [ -n "$(ls -A "$NET_COPY" 2>/dev/null)" ]; then
        echo "$NET_COPY"; return
    fi
    echo "$MEDIA_DIR"
}

list_media() {
    local f
    find "$1" -type f ! -name '.*' 2>/dev/null | sort | while IFS= read -r f; do
        if has_ext "$f" "$IMAGE_EXT" || has_ext "$f" "$VIDEO_EXT"; then echo "$f"; fi
    done
}

play_list() {   # plays the list once: each run of photos in one fbi, videos in between
    local f photos=()
    for f in "$@"; do
        [ -f "$f" ] || continue
        if has_ext "$f" "$VIDEO_EXT"; then
            [ ${#photos[@]} -gt 0 ] && show_images "${photos[@]}"
            photos=()
            play_video "$f"
        else
            photos+=("$f")
        fi
    done
    [ ${#photos[@]} -gt 0 ] && show_images "${photos[@]}"
}

if [ "$1" = --sync ]; then
    [ -n "$NETWORK_SHARE" ] || { echo "NETWORK_SHARE is not set in $CONF"; exit 1; }
    sync_network && ls -lh "$NET_COPY"
    exit
fi

if [ -n "$NETWORK_SHARE" ]; then
    sync_loop &
    SYNC_PID=$!
fi

trap '[ -n "$SYNC_PID" ] && kill "$SYNC_PID" 2>/dev/null; stop_image; setterm --default --cursor on >"$TTY" 2>/dev/null; clear >"$TTY" 2>/dev/null; exit 0' TERM INT
screen_setup

LOOP_LIST=   # the photo list the looping fbi is showing (photos-only folders)

while true; do
    folder=$(pick_folder)
    mapfile -t files < <(list_media "$folder")
    if [ ${#files[@]} -eq 0 ]; then
        stop_image; LOOP_LIST=
        log "no photos or videos in $folder; checking again in 30 seconds"
        sleep 30
        continue
    fi

    has_video=no
    for f in "${files[@]}"; do has_ext "$f" "$VIDEO_EXT" && has_video=yes && break; done

    if [ $has_video = no ]; then
        # Photos only: leave one fbi looping through them (no break at all, even with a single
        # photo), and only restart it when the files change.
        list="${files[*]}"
        if [ "$list" != "$LOOP_LIST" ] || ! kill -0 "$FBI_PID" 2>/dev/null; then
            stop_image
            if [ "$SHUFFLE" = yes ]; then start_fbi -u "${files[@]}"; else start_fbi "${files[@]}"; fi
            LOOP_LIST=$list
        fi
        sleep 30
        continue
    fi

    stop_image; LOOP_LIST=
    if [ "$SHUFFLE" = yes ]; then
        mapfile -t files < <(printf '%s\n' "${files[@]}" | shuf)
    fi
    play_list "${files[@]}"
done
