# Raspberry Pi: help desk server, board and office light

This sets up a Raspberry Pi 4 (4 GB) in the IT office to:

- run **Device Inventory** all the time, so help desk email is read, the digest and backups go out, and
  everyone on the school network can open it;
- show the **Help Desk board** full-screen on a monitor;
- switch the **office light** through a relay when tickets need attention, with a **button** to silence it.

Where it says `pi`, use the user name you choose in step 1.

## What you need

- Raspberry Pi 4 (4 GB), the **official power supply** (5V 3A), and a case with a fan or heatsink.
- A **USB SSD**: a 2.5" SATA SSD (120–250 GB is plenty) in a USB 3 enclosure, or a ready-made USB SSD.
  Plug it into a **blue USB 3 port**.
- A monitor (micro-HDMI to HDMI cable) for the board.
- Your 5V relay module and the light, and a push button (momentary, normally open).
- A network cable is best; Wi-Fi also works.

## 1. Put Raspberry Pi OS on the SSD

1. On your computer, install **Raspberry Pi Imager** (raspberrypi.com/software) and connect the SSD to it.
2. Choose **Raspberry Pi 4**, **Raspberry Pi OS (64-bit)** (the one with the desktop), and the SSD as storage.
3. When it asks, **Edit settings**: hostname `helpdesk`, a user name and password, your time zone, and
   (on the Services tab) **Enable SSH** if you want to manage it from your desk. Write the SSD.
4. Plug the SSD into the Pi (no SD card) and power it on. Pi 4s from 2021 on start from USB by themselves.
   If yours doesn't: write **Misc utility images → Bootloader (Pi 4) → USB Boot** to an SD card with Imager,
   start the Pi from it once (the green light blinks steadily when done), then remove it.
5. In your DHCP server, **reserve an IP address** for the Pi, so links in help desk emails keep working.

## 2. Install Device Inventory

Open a Terminal on the Pi (or connect with `ssh pi@helpdesk.local`):

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv python3-gpiozero
git clone -b claude/device-inventory-tracker-1kk247 https://github.com/ironoxider/test.git ~/device-inventory
cd ~/device-inventory
./start.sh
```

(Or download the code as a ZIP from GitHub on that branch and unzip it to `/home/pi/device-inventory`.)

The first `./start.sh` installs what it needs and opens Device Inventory. Close it with **Ctrl+C** once it works.

### Bring your existing data over

On the computer that runs Device Inventory now, close Device Inventory, then copy these files from its data
folder (shown in **Settings → Your data & backups**, normally `Documents/Device Inventory`) to
`/home/pi/Documents/Device Inventory/` on the Pi (a USB stick works, or `scp`):

- `inventory.db`: devices, tickets, history
- `settings.json`: settings, including help desk email setup
- `gmail.json`: the help desk mailbox sign-in, so you don't need to connect it again

Only one copy of Device Inventory should read the help desk mailbox: once the Pi is running, stop using the
old computer's copy (or untick "Check this mailbox" there).

## 3. Start it at power-on

```bash
cd ~/device-inventory
sudo cp pi/device-inventory.service /etc/systemd/system/
sudo nano /etc/systemd/system/device-inventory.service   # change "pi" if your user name is different
sudo systemctl daemon-reload
sudo systemctl enable --now device-inventory
systemctl status device-inventory    # should say "active (running)"
```

Then open `http://helpdesk.local:5000` (or the Pi's IP address, port 5000) from your computer, go to
**Settings** and tick **Allow phones and other computers on this network** so the IT team can use it and the
links in emails work. To see its messages: `journalctl -u device-inventory -f`.

## 4. Backups

In **Settings → Your data & backups**:

- **Backup folder**: plug one of your backup drives into the Pi; it appears under `/media/pi/<drive name>`.
  Make a folder there (e.g. `/media/pi/Backup/Inventory backups`) and enter it.
- Tick **Back up automatically every day** (2:00 AM is fine; the Pi is always on), and keep 30.
- **Also email each automatic backup to** an address of yours (optional: another offsite copy, in Gmail).
- **Offsite copy**: Backblaze B2, see below.
- Click **Back up now** once to check. If the drive is ever missing, the Devices page shows a warning.

### Offsite backups to Backblaze B2

Each automatic backup can also be uploaded to a Backblaze B2 bucket with **rclone**. Old copies there are
deleted after the number of days you choose (90 by default), so the bucket stays small: each backup is a few
megabytes.

**In Backblaze** (backblaze.com, signed in):

1. **B2 Cloud Storage → Buckets → Create a Bucket**: a name of your choice (bucket names are unique across all
   of Backblaze, e.g. `hudson-it-backups`), **Files in Bucket: Private**, **Default Encryption: Enable**.
2. **Application Keys → Add a New Application Key**: name it `device-inventory-pi`, **Allow access to bucket:**
   only the bucket above, **Type of access: Read and Write**. Copy the **keyID** and **applicationKey**: the
   applicationKey is only shown once. This key can't touch anything else in your Backblaze account.

**On the Pi** (as the same user Device Inventory runs as):

```bash
sudo apt install -y rclone
rclone config create b2 b2 account YOUR_KEY_ID key YOUR_APPLICATION_KEY
rclone mkdir b2:hudson-it-backups/device-inventory      # use your bucket name; no output means it worked
```

**In Device Inventory → Settings → Your data & backups**: set **Offsite copy** to
`b2:hudson-it-backups/device-inventory`, keep **Back up automatically** ticked, **Save settings**, then click
**Run the automatic backup now**. The message should say "copied to b2:…", and the file appears in Backblaze
under **Browse Files**. If it fails, the message (and the warning on the Devices page) shows rclone's error.

**To restore from Backblaze**: download the .zip from **Browse Files** in the Backblaze website (or
`rclone copy "b2:hudson-it-backups/device-inventory/Device Inventory auto-backup 2026-11-03.zip" ~/`), unzip it,
and follow *How to restore.txt* inside.

The key is kept in `~/.config/rclone/rclone.conf`, readable only by that user. (rclone works the same way if
Device Inventory runs on a Windows or Mac computer instead: install rclone from rclone.org and run the same
`rclone config create` command there.)

## 5. The board on the monitor

1. **Turn off screen blanking**: `sudo raspi-config` → Display Options → Screen Blanking → No.
2. Make the board open at login:

```bash
mkdir -p ~/.config/labwc
echo "/home/pi/device-inventory/pi/start-board.sh &" >> ~/.config/labwc/autostart
```

   (On an older Pi OS that uses Wayfire, add `board = /home/pi/device-inventory/pi/start-board.sh` under
   `[autostart]` in `~/.config/wayfire.ini` instead.)
3. Restart (`sudo reboot`). The board opens full-screen. Click **Sound off** once if you want the chime.
   Press **Alt+F4** to close it.

## 6. The office light and button

### Wiring

Pins are BCM numbers; physical pin numbers are in brackets (pin 1 is the corner pin nearest the SD slot).

| From | To |
|---|---|
| Relay module **VCC** | **5V** (pin 2) |
| Relay module **GND** | **GND** (pin 6) |
| Relay module **IN** | **GPIO17** (pin 11) |
| Button, one leg | **GPIO27** (pin 13) |
| Button, other leg | **GND** (pin 14) |
| Light's power | through the relay's **COM** and **NO** (normally open) contacts |

The button needs no resistor (the Pi's internal pull-up is used). If the light is mains-powered, keep the
mains side in a proper enclosure, as you would with any relay project.

### Test

```bash
python3 ~/device-inventory/pi/helpdesk_light.py --test
```

The relay should click and the light come on for 3 seconds. If it's **on all the time and off during the
test**, your relay switches on a LOW input: add `--relay-active-low` to the commands below.

### Start it at power-on

```bash
cd ~/device-inventory
sudo cp pi/helpdesk-light.service /etc/systemd/system/
sudo nano /etc/systemd/system/helpdesk-light.service   # user name, pins, --relay-active-low, --quiet-hours
sudo systemctl daemon-reload
sudo systemctl enable --now helpdesk-light
journalctl -u helpdesk-light -f    # shows "Light on: ..." and button presses
```

Useful options on the `ExecStart` line:

- `--quiet-hours 17:30-07:00`: never turn the light on in that time (tickets still arrive as usual).
- `--relay-pin` / `--button-pin`: other GPIO pins. `--button-pin 0` if there's no button.

### Choosing when it lights

In Device Inventory: **Help Desk → Office light** (on the Pi's own screen or from the Pi's address). Choose
new tickets, high/urgent tickets and/or requester replies, and whether opening the ticket turns it off.
**Test the light** turns it on for 15 seconds; **Silence** (also on the board) does what the button does.

## Updating Device Inventory later

```bash
cd ~/device-inventory && git pull
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart device-inventory helpdesk-light
```

## Good to know

- Shut the Pi down with `sudo shutdown -h now` (or the desktop menu) rather than pulling the power. A small
  UPS is worth it.
- There's no sign-in yet: anyone on the school network who knows the address can open Device Inventory
  while "Allow phones and other computers" is on. Google sign-in for IT staff is the next step.
