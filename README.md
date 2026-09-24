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

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `INVENTORY_DB` | `./inventory.db` | Path to the SQLite database file |
| `INVENTORY_HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` to allow other computers on your network |
| `INVENTORY_PORT` | `5000` | Port |
| `INVENTORY_SECRET_KEY` | `dev-change-me` | Session signing key. Set it to a random value if you share the app |
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
