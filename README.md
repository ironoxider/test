# Device Inventory

The Hudson School's device inventory and IT help desk (Hoboken, New Jersey).

A small web app for keeping track of devices: what you have, where it is, who has it,
and its model and serial numbers, plus the help desk that turns email to the help desk
mailbox into tickets. It runs on Flask with a single SQLite file, so there is no database
server to set up.

## Features

- **Devices**: asset tag, name, category, manufacturer, model, model number, serial number,
  manufacture date, location, assigned-to, status, purchase date, warranty expiry, replacement date, notes
- **Drop-downs you control**: the category and status lists are managed on the **Lists** page (add, rename,
  reorder, remove), or add a new entry straight from the device form with "+ Add new…". "Assigned to"
  suggests everyone you've entered before, and you can still type a new name.
- **Replacement reminders**: each device gets a "Replace by" date, 3 years after its purchase date
  (or manufacture date) unless you change it. The Devices page warns about devices that are overdue or due
  within 90 days. Both numbers can be changed in Settings.
- **Locations**: add, rename, or delete them (a location that still has devices can't be deleted).
  You can also create a new location straight from the device form.
- **Search and filter**: search across tag, name, model, model number, serial, person, notes and
  location. Filter by location, category and status, and sort by any column.
- **History**: every change to a device is logged (for example "Location: Room 204 → IT Office").
- **Dashboard counts**: total devices, a count per status, out of warranty, and due for replacement.
  Click a count to see those devices.
- **Fill from photos**: on the Add/Edit device form, take or choose photos of the device label.
  Barcodes are read right in the browser (free, and works offline). If an Anthropic API key is set,
  **Read label with AI** also reads the printed text and fills in the manufacturer, model, model number,
  serial number and category.
- **Batches**: besides individual devices with asset tags, add a batch of identical items counted by quantity
  (chargers, mice, cables). The asset tag is optional for a batch. Use **Take out** and **Add** on its page to change
  the count (each change goes in the history), and set a reorder level to see **Low stock** on the Devices page.
  In a CSV, a row with no asset tag is a batch, matched by name and location.
- **Backups**: in **Settings → Your data & backups**, **Back up now** saves a dated .zip (inventory, settings without
  the API key, and restore steps) to a folder you choose, such as a Google Drive folder. **Download a backup**
  saves the same file through the browser.
- **Automatic daily backups** (Settings → Your data & backups): once a day at a set time, a dated .zip goes to the
  backup folder (the last 30 are kept), and optionally by email and to an offsite copy with rclone, such as a
  Backblaze B2 bucket (old offsite copies deleted after 90 days). Problems show on the Devices page.
- **Raspberry Pi server, board and office light**: see `pi/PI_SETUP.md` to run Device Inventory on a Raspberry Pi
  at power-on, show the live board on a monitor, and switch a light through a relay when new or urgent tickets
  arrive (`pi/helpdesk_light.py`), with a button to silence it. Choose what lights it under Help Desk → Office light.
  Device Inventory can also run on a server or virtual machine with the Pi only showing the board and driving the
  light (Setup B in the guide). When no light is connected, the board shows a siren animation instead.
- **Help Desk** (replacing Spiceworks, stage 1): a ticket queue for IT staff, under **Help Desk** in the menu.
  - Each ticket has a requester, subject and description, status (New, Open, Waiting, Closed), priority,
    category, the IT staff member it's assigned to, a location, and any number of linked devices.
  - The ticket page shows the whole history: the request, internal notes, and every change ("Status: Open →
    Closed", "Linked CB-494").
  - **Devices are linked automatically** when a ticket's subject, description or an email reply mentions their
    asset tag (written CB-494, CB494, cb 494, or AC-92 for AC-092) or their serial number. Only devices in the
    inventory are linked; a spelling that could mean two devices is left for you to choose. If the ticket has no
    location yet, it takes the room of the linked devices, or a single room the text names (e.g. "W401").
    Unlinking a device keeps it unlinked, and it's offered as a one-click **Link** button instead.
  - Start a ticket from **+ New ticket**, or from a device's page (the device and its room are filled in).
    Device pages list their tickets, the Locations page counts open tickets per room, and the queue filters by
    status, assignee, priority, category and location and searches ticket numbers, text, people and asset tags.
  - Ticket categories and IT staff names are managed on the **Lists** page.
  - Entries from the old per-device ticket log (version 1.7–1.8) become help desk tickets, keeping their
    Spiceworks number (linked to Spiceworks if its address is set in Settings).
  - **Report a problem** on a device page opens an email to the help desk address set in Settings, with the
    device's details filled in.
- **Help desk email** (stage 2): connect a Google Workspace mailbox under **Help Desk → Email setup** (the page
  walks through creating an "Internal" OAuth client in Google Cloud and signing in once as the mailbox; the access
  is limited to that one mailbox and saved in `gmail.json`, which backups leave out).
  - While Device Inventory runs, the mailbox is checked every few minutes (or click **Check now**). A new email
    from an allowed address becomes a **New** ticket; asset tags and a room it mentions are linked; an optional
    automatic reply gives the ticket number. **Its wording is yours to edit** (Email setup → Automatic reply),
    with placeholders such as `{first_name}`, `{ticket}`, `{subject}`, `{team}` and `{school}` and a live preview;
    "Use the standard message" puts the original back. The subject stays `Re: [Ticket #N] …` so replies thread.
  - Replies by email (same thread, a reply to one of our emails, or "[Ticket #12]" in the subject) are added to
    their ticket and reopen it. Attachments are listed with a link to the email in Gmail.
  - **Reply by email** on a ticket sends from the mailbox, threaded with the conversation, and can set the ticket
    to Waiting or Closed.
  - If Device Inventory or its computer was off, mail waits in the inbox and is picked up about 15 seconds after
    it starts again. Tickets and messages keep the time the email was **sent**, so ages in the queue, board and
    digest stay right; a ticket picked up late says when the email was sent. (Don't archive mail in the help desk
    mailbox: only the inbox is read.)
  - Auto-replies, bounces and outside senders are labelled "Help Desk/Skipped" in Gmail instead of becoming
    tickets; imported mail is labelled "Help Desk/Imported". Nothing is deleted.
  - **The IT team** (addresses set on the Email setup page) gets an email for each new ticket and each requester
    reply, with the details and a link to the ticket. **Replying to one of these adds an internal note** to the
    ticket instead of reaching the requester.
  - **Daily digest**: at a set time (weekdays only, if you like) the team gets one email listing every open ticket
    by priority, with links; **Send the digest now** shows what it looks like.
  - Links in these emails use this computer's network address, so they open on other computers while Device
    Inventory is running with "Allow phones and other computers" on in Settings.
- **Unread tickets and the live board**: tickets with a new email or reply nobody has opened yet are **unread**:
  marked in the queue (with an Unread tab) and turning the Help Desk count in the menu red. **Live board** (on the
  Help Desk page) is a full-screen view for a monitor in the IT office: the school seal and a large clock, then
  counts and three columns: **Needs attention** (unread), the **Open queue**, and the **To-do** list from
  Todoist. On a large screen each column is a fixed box, and **a column with more than fits scrolls through it
  slowly and loops**, pausing at the top of each pass and while the mouse is over it (a resting pointer doesn't
  count); a newly arrived ticket brings Needs attention back to the top. **Scrolling** in the header switches
  between normal, fast, slow and off (remembered on that screen). It refreshes every 10 seconds without losing its
  place; new arrivals flash, with an optional chime (click "Sound off" to turn it on). New email appears after the next mailbox check (every 2 minutes by default; 1 minute is the shortest).
  - **To-do list from Todoist** (Help Desk → To-do list): paste a Todoist API token (Todoist → Settings →
    Integrations → Developer) and choose a project and what to show (due today/overdue/undated, only due, all, or
    any Todoist filter such as `#IT & (today | overdue)`). Overdue items come first, coloured by Todoist priority;
    tick the circle on the board to complete one in Todoist. The list is fetched once a minute; the token stays
    on this computer and isn't put in backups. Without Todoist the board keeps two columns.
  - Still to come: running on an always-on campus server with Google sign-in for IT staff (stage 3).
- **CSV export and import**: export the current filtered view. When you import, a row whose asset
  tag already exists updates that device, and any other row adds a new one.

## Download and run (no programming needed)

1. On GitHub, open the repository's **Actions** tab and click the latest successful **Build app** run.
   Under **Artifacts**, download the file for your computer:
   - **Windows:** `DeviceInventory-windows`
   - **Mac (M1/M2/M3/M4, 2020 or newer):** `DeviceInventory-mac-apple-silicon`
   - **Linux:** `DeviceInventory-linux`
2. Unzip the download. On a Mac there is a second zip inside, so double-click that one too.
3. Double-click **DeviceInventory**. A small window opens showing the address, and your web browser
   opens the app. **Keep that window open while you use it. Close it to stop the app.**

The first time you open it, your computer may warn you, because the program isn't signed by a
registered developer:

- **Windows** ("Windows protected your PC"): click **More info**, then **Run anyway**.
- **Mac** ("cannot be opened" or "Apple could not verify"): click **Done**, then open
  **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to DeviceInventory.

Your data is saved in **Documents/Device Inventory/inventory.db**. To back it up, copy that file
somewhere safe. The **Settings** page in the app shows the exact location. Replacing the program with
a newer version keeps your data.

Intel Macs and Chromebooks can't run the downloads; use "Run from source" below instead.

**Which version am I running?** The version number is at the bottom of every page. If you still see an old
version after updating, an older copy is probably still running: close every Device Inventory window, then
open the new one. (A new version also warns you in its window when an older copy is still running.)

## Run from source

Use this if the download doesn't work on your computer (for example on a Chromebook or an Intel Mac).
You need Python 3.9 or newer ([python.org/downloads](https://www.python.org/downloads/); on Windows,
tick **"Add python.exe to PATH"** in the installer).

1. On GitHub, click **Code → Download ZIP** and unzip it.
2. Start the app with the start script for your computer. The first run installs everything the app
   needs (about a minute); after that it starts in a few seconds.
   - **Windows:** double-click **Start Device Inventory.bat**
   - **Mac:** double-click **Start Device Inventory.command** (the first time, right-click it and choose **Open**)
   - **Chromebook / Linux:** in the Terminal, go to the folder and run `./start.sh`.
     If it says the Python environment couldn't be created, run `sudo apt install python3-venv` first.

The start scripts install the app's add-ons into a private `.venv` folder, so you don't need to run
`pip` yourself. Running `python app.py` directly without doing that causes "No module named ..." errors.

When run from source the data is in the same place, **Documents/Device Inventory**, so unzipping a newer version somewhere else keeps your data. (Versions before 1.8 kept it in the app's folder; the first start of a newer version moves it, and leaves the old file renamed to `inventory.db.moved`.)

### Building the program yourself

```bash
pip install pyinstaller
pyinstaller DeviceInventory.spec     # result is in dist/
```

The **Build app** GitHub Actions workflow does this for Windows, macOS and Linux on every push.
Pushing a tag such as `v1.0.0` also puts the files on the repository's Releases page.

### Reading labels from photos (optional)

Barcode scanning works with no setup. To have the AI read the printed label text as well:

1. Create an API key at https://console.anthropic.com (this needs an account with billing set up).
2. In the app, open **Settings**, paste the key and click **Save settings**. It takes effect straight away.
   The key is saved in `settings.json` next to the database. (You can set the `ANTHROPIC_API_KEY`
   environment variable instead.)

How it works:

- The browser shrinks photos (longest side 2000 px) before uploading them. Each **Read label with AI** click
  sends them to Anthropic's API. That typically costs a few cents per click (1–5¢), depending on how many photos you send. The photos are **not** saved by the app.
- Fields that are empty get filled in and highlighted. If a field already has a different value, you get
  a "Use photo value" button instead, so your typing is never overwritten.
  **Always check the highlighted fields before saving.**
- If the serial number the AI read matches a barcode on the label, the app says so. That's a good sign it's correct.
- Each barcode found gets buttons to use it as the serial number, model number or asset tag.
- iPhone photos in **HEIC** format work in every browser. If the browser can't open them itself (Chrome,
  Edge and Firefox can't), the app converts them to JPEG on your computer first. This takes a few seconds per photo.
- Best results: photograph the label straight on, fill the frame with it, and avoid glare. Add a
  second photo of the whole device if the label doesn't show the model name.
- **Using a phone:** on the **Settings** page, tick "Allow phones and other computers on this network" and
  click Save (no restart needed). Settings then shows the phone address (like `http://192.168.1.20:5000/`)
  and a QR code to scan with the phone's camera. The phone must be on the same Wi-Fi. "Choose / take photos"
  then offers the camera. If the phone can't connect, Settings has a "Phone still can't connect?" checklist
  (port number, firewall, Wi-Fi that blocks devices from reaching each other, Chromebook port forwarding).
  Settings itself, with the API key, can only be opened on the computer running the app.

The AI model defaults to `claude-opus-5`. Set `INVENTORY_AI_MODEL` to use another one.
If a request is declined by the model's safety checks, it is automatically retried on a fallback model.

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `INVENTORY_DATA_DIR` | see above | Folder for the database and `settings.json` |
| `INVENTORY_DB` | `<data dir>/inventory.db` | Path to the SQLite database file |
| `INVENTORY_HOST` | `127.0.0.1` | Address to listen on. Overrides the Settings network option |
| `INVENTORY_PORT` | `5000` | Port |
| `INVENTORY_SECRET_KEY` | random, saved in `settings.json` | Session signing key |
| `ANTHROPIC_API_KEY` | unset | API key for AI label reading, if not saved on the Settings page |
| `INVENTORY_AI_MODEL` | `claude-opus-5` | Claude model used to read labels |
| `INVENTORY_DEBUG` | unset | Set to `1` for Flask debug mode (only on your own machine) |

> **Note:** the app has no login. It is meant for your own computer or a trusted internal
> network. Don't expose it to the internet as-is.

## CSV format

Columns, in any order (only `asset_tag` is required; headers like `Asset Tag` also work):

```
asset_tag,name,category,manufacturer,model,model_number,serial_number,manufacture_date,location,assigned_to,status,purchase_date,warranty_expires,replacement_date,notes
```

Dates use `YYYY-MM-DD`. Status must be one of the statuses on the Lists page (upper/lower case doesn't
matter). A category that isn't on the list yet is added to it. If `replacement_date` is blank, it's filled in
from the purchase or manufacture date.

## Tests

```bash
pip install pytest
python -m pytest
```

The tests use a fake AI client, so they don't need an API key and don't cost anything.

## Third-party code

`static/vendor/zxing.min.js` is [ZXing for JS](https://github.com/zxing-js/library) 0.21.3 (Apache 2.0,
see `static/vendor/zxing-LICENSE.txt`). The app uses it to read barcodes in browsers that don't have a
built-in barcode reader.

`static/vendor/heic2any.min.js` is [heic2any](https://github.com/alexcorvi/heic2any) 0.0.4 (MIT), which
includes [libheif](https://github.com/strukturag/libheif) (LGPL-3.0). See `static/vendor/heic2any-LICENSE.txt`.
The app uses it to convert iPhone HEIC photos, and only loads it when a HEIC photo is picked.

`static/vendor/qrcode.js` is [QR Code Generator](https://github.com/kazuhikoarase/qrcode-generator) 2.0.4
by Kazuhiko Arase (MIT, license in the file header). It draws the phone-address QR code on the Settings page.
