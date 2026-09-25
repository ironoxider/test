# Device Inventory

A small web app for keeping track of devices: what you have, where it is, who has it,
and its model and serial numbers. It runs on Flask with a single SQLite file, so there
is no database server to set up.

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

When run from source, the data is stored in `inventory.db` in the app's folder.

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
- **Using a phone:** on the **Settings** page, tick "Allow phones and other computers on this network"
  and reopen the app. The window and the Settings page then show an address like `http://192.168.1.20:5000`
  to open on a phone on the same Wi-Fi. "Choose / take photos" then offers the camera.

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
