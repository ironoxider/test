# Device Inventory

A small web app for keeping track of devices: what you have, where it is, who has it,
and its model and serial numbers. It runs on Flask with a single SQLite file, so there
is no database server to set up.

## Features

- **Devices**: asset tag, name, category, manufacturer, model, model number, serial number,
  location, assigned-to, status, purchase date, warranty expiry, notes
- **Locations**: add, rename, or delete them (a location that still has devices can't be deleted).
  You can also create a new location straight from the device form.
- **Search and filter**: search across tag, name, model, model number, serial, person, notes and
  location. Filter by location, category and status, and sort by any column.
- **History**: every change to a device is logged (for example "Location: Room 204 → IT Office").
- **Dashboard counts**: total devices, in service, in repair, and out of warranty.
- **Fill from photos**: on the Add/Edit device form, take or choose photos of the device label.
  Barcodes are read right in the browser (free, and works offline). If an Anthropic API key is set,
  **Read label with AI** also reads the printed text and fills in the manufacturer, model, model number,
  serial number and category.
- **CSV export and import**: export the current filtered view. When you import, a row whose asset
  tag already exists updates that device, and any other row adds a new one.

## Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

The data is stored in `inventory.db` next to `app.py`. To back it up, copy that file.

### Reading labels from photos (optional)

Barcode scanning works with no setup. To have the AI read the printed label text as well:

1. Create an API key at https://console.anthropic.com (this needs an account with billing set up).
2. Set it before starting the app:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
   python app.py
   ```

How it works:

- The browser shrinks photos (longest side 2000 px) before uploading them. Each **Read label with AI** click
  sends them to Anthropic's API. That typically costs a few cents per click (1–5¢), depending on how many photos you send. The photos are **not** saved by the app.
- Fields that are empty get filled in and highlighted. If a field already has a different value, you get
  a "Use photo value" button instead, so your typing is never overwritten.
  **Always check the highlighted fields before saving.**
- If the serial number the AI read matches a barcode on the label, the app says so. That's a good sign it's correct.
- Each barcode found gets buttons to use it as the serial number, model number or asset tag.
- Best results: photograph the label straight on, fill the frame with it, and avoid glare. Add a
  second photo of the whole device if the label doesn't show the model name.
- **Using a phone:** start the app with `INVENTORY_HOST=0.0.0.0` and open `http://<computer's IP>:5000`
  on a phone on the same network. "Choose / take photos" then offers the camera.

The AI model defaults to `claude-opus-5`. Set `INVENTORY_AI_MODEL` to use another one.
If a request is declined by the model's safety checks, it is automatically retried on a fallback model.

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `INVENTORY_DB` | `./inventory.db` | Path to the SQLite database file |
| `INVENTORY_HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` to allow other computers on your network |
| `INVENTORY_PORT` | `5000` | Port |
| `INVENTORY_SECRET_KEY` | `dev-change-me` | Session signing key. Set it to a random value if you share the app |
| `ANTHROPIC_API_KEY` | unset | Turns on AI label reading (see above) |
| `INVENTORY_AI_MODEL` | `claude-opus-5` | Claude model used to read labels |
| `INVENTORY_DEBUG` | unset | Set to `1` for Flask debug mode (only on your own machine) |

> **Note:** the app has no login. It is meant for your own computer or a trusted internal
> network. Don't expose it to the internet as-is.

## CSV format

Columns, in any order (only `asset_tag` is required; headers like `Asset Tag` also work):

```
asset_tag,name,category,manufacturer,model,model_number,serial_number,location,assigned_to,status,purchase_date,warranty_expires,notes
```

Dates use `YYYY-MM-DD`. Status must be one of: In Service, In Storage, In Repair, Loaned Out,
Lost, Retired (upper/lower case doesn't matter).

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
