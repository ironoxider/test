# AV Display

A Raspberry Pi that turns on with the TV and plays an endless full-screen slideshow of the
**photos and videos** in a folder (or on a USB stick). No keyboard, mouse or desktop needed.

## Will my Pi 1 B+ work?

**Yes, with the right setup.** The B+ is slow (single core, 512 MB), but it has a hardware video
decoder that plays **1080p H.264** smoothly. The catch is that only `omxplayer` uses that decoder,
and newer Raspberry Pi OS versions removed it. So on a B+:

| | |
|---|---|
| **OS** | **Raspberry Pi OS (Legacy) Lite, "Buster", 32-bit**. Not the newest version, not the desktop version. |
| **Videos** | **H.264 in .mp4**, **1080p or 720p**, up to 30 fps. Other formats (H.265/HEVC, VP9, most 4K, many phone videos) will stutter or not play: convert them first (see below). |
| **Photos** | JPG or PNG. Resize big camera photos to **1920×1080**: a 12-megapixel photo takes the B+ several seconds to open. |
| **SD card** | 8 GB or more. |
| **Power** | A good 5V 2A supply. |

On a Pi 2, 3 or 4 the same steps work and everything is quicker. On a Pi 4/5 with the newest
Raspberry Pi OS Lite, the installer uses `mpv` instead of `omxplayer` automatically.

Buster no longer gets security updates, so keep these display Pis on a trusted network
(or off the network once set up).

## Set up a Pi

1. On your computer, open **Raspberry Pi Imager**. Choose your Pi model, then
   **Raspberry Pi OS (other) → Raspberry Pi OS (Legacy) Lite**. If Imager doesn't offer Buster for
   your model, download *raspios_oldstable_lite_armhf* (2023-05-03, Buster) from
   downloads.raspberrypi.com/raspios_oldstable_lite_armhf/images/ and pick **Use custom** in Imager.
2. Under **Edit settings**: set a user name (`pi`) and password, Wi-Fi if needed, and on the Services
   tab **Enable SSH** so you can update it from your desk. Write the card.
3. Start the Pi with a keyboard and the TV connected (or SSH in), then:

   ```bash
   sudo apt update && sudo apt install -y git
   git clone -b claude/nifty-maxwell-33w88k https://github.com/ironoxider/test.git ~/device-inventory
   cd ~/device-inventory/av-display
   sudo ./install.sh
   ```

4. Put photos and videos in `/home/pi/media` (see below) and `sudo reboot`. The slideshow starts on
   its own a few seconds after power-on, every time.

To set up more Pis, either repeat this, or set up one and copy its SD card (Win32 Disk Imager /
`dd`) to the others.

## Changing what's shown

- **USB stick (easiest):** put the files in a folder named `slideshow` on a USB stick and plug it in.
  While it's plugged in, the Pi shows the stick instead of `/home/pi/media`. Pull it out to go back.
  New files are picked up at the end of the current pass.
- **Over the network:** copy files into `/home/pi/media` with WinSCP / Cyberduck / `scp`
  (host `pi@<pi-name>.local`). Changes show on the next pass.
- **Order:** files play in name order, including sub-folders. Number them: `01-welcome.jpg`,
  `02-open-house.mp4`, ... Or set `SHUFFLE=yes`.

## Settings

Edit with `sudo nano /etc/av-display.conf`, then `sudo systemctl restart av-display`:

| Setting | Default | |
|---|---|---|
| `MEDIA_DIR` | `/home/pi/media` | Folder to show |
| `IMAGE_SECONDS` | `10` | Seconds per photo (videos play to the end) |
| `SHUFFLE` | `no` | `yes` for random order |
| `AUDIO` | `hdmi` | Video sound: `hdmi`, `local` (headphone jack), `both` |
| `USE_USB` | `yes` | Show a plugged-in USB stick instead |
| `USB_FOLDER` | `slideshow` | Folder on the stick (the whole stick if it isn't there) |

## Converting videos for a B+

Free **HandBrake** (handbrake.fr): preset **Fast 1080p30** (or **Fast 720p30**), format MP4. Or with ffmpeg:

```bash
ffmpeg -i input.mov -c:v libx264 -profile:v high -level 4.0 -vf "scale=-2:1080" -r 30 -c:a aac -b:a 128k output.mp4
```

Shrink photos to 1920×1080 with any photo editor, or on a Mac/Linux with ImageMagick:

```bash
mogrify -resize 1920x1080\> *.jpg
```

## Troubleshooting

- **Black screen, no slideshow:** `sudo systemctl status av-display` and `journalctl -u av-display`.
  "no photos or videos in ..." means the folder is empty or the files have other extensions.
- **Video plays as a black screen or not at all on a B+:** the OS is too new (no `omxplayer`), or the
  video isn't H.264. Check with `which omxplayer`.
- **Photos take a long time to appear:** resize them to 1920×1080.
- **No picture if the TV was off at power-on:** the installer sets `hdmi_force_hotplug=1`; if the
  picture is the wrong size, add `hdmi_group=1` and `hdmi_mode=16` (1080p) to `/boot/config.txt`.
- **Get a login screen back:** `sudo systemctl stop av-display` (or SSH in), then
  `sudo systemctl disable av-display` to stop it starting at power-on.

## Files

- `av-display.sh`: the slideshow (installed as `/usr/local/bin/av-display`)
- `av-display.conf`: settings (installed as `/etc/av-display.conf`)
- `av-display.service`: starts it at power-on on the TV screen
- `install.sh`: installs all of the above and the Pi settings
