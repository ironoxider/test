"""Device inventory tracker.

A small Flask + SQLite web app for tracking devices, where they are,
who has them, and their model / serial numbers.

Run with:  python app.py   (then open http://127.0.0.1:5000)
"""

import csv
import io
import os
import sqlite3
from datetime import date

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATUSES = ["In Service", "In Storage", "In Repair", "Loaned Out", "Lost", "Retired"]

CATEGORIES = [
    "Laptop",
    "Desktop",
    "Chromebook",
    "Tablet",
    "Phone",
    "Monitor",
    "Printer",
    "Projector",
    "Network",
    "Server",
    "Peripheral",
    "Other",
]

# Editable device fields, in display/CSV order. `location` is handled
# separately because it is stored as a foreign key.
DEVICE_FIELDS = [
    ("asset_tag", "Asset Tag"),
    ("name", "Name"),
    ("category", "Category"),
    ("manufacturer", "Manufacturer"),
    ("model", "Model"),
    ("model_number", "Model Number"),
    ("serial_number", "Serial Number"),
    ("assigned_to", "Assigned To"),
    ("status", "Status"),
    ("purchase_date", "Purchase Date"),
    ("warranty_expires", "Warranty Expires"),
    ("notes", "Notes"),
]
FIELD_LABELS = dict(DEVICE_FIELDS, location="Location")
CSV_COLUMNS = [f for f, _ in DEVICE_FIELDS[:7]] + ["location"] + [f for f, _ in DEVICE_FIELDS[7:]]

SORTABLE = {
    "asset_tag": "d.asset_tag",
    "name": "d.name",
    "category": "d.category",
    "model": "d.model",
    "model_number": "d.model_number",
    "location": "l.name",
    "assigned_to": "d.assigned_to",
    "status": "d.status",
    "warranty_expires": "d.warranty_expires",
}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("INVENTORY_SECRET_KEY", "dev-change-me"),
        DATABASE=os.environ.get("INVENTORY_DB", os.path.join(BASE_DIR, "inventory.db")),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    register_routes(app)
    app.jinja_env.globals.update(
        STATUSES=STATUSES,
        CATEGORIES=CATEGORIES,
        FIELD_LABELS=FIELD_LABELS,
        today=lambda: date.today().isoformat(),
    )
    return app


# ---------------------------------------------------------------- database


def get_db():
    if "db" not in g:
        from flask import current_app

        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with open(os.path.join(BASE_DIR, "schema.sql")) as f:
        get_db().executescript(f.read())


def clean(value):
    """Strip whitespace and turn empty strings into NULL."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_or_create_location(db, name):
    name = clean(name)
    if not name:
        return None
    row = db.execute("SELECT id FROM locations WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO locations (name) VALUES (?)", (name,)).lastrowid


def location_name(db, location_id):
    if location_id is None:
        return None
    row = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    return row["name"] if row else None


def validate_device(data, db, device_id=None):
    errors = []
    if not data.get("asset_tag"):
        errors.append("Asset tag is required.")
    if not data.get("name"):
        errors.append("Name is required.")
    if data.get("status") not in STATUSES:
        errors.append(f"Status must be one of: {', '.join(STATUSES)}.")
    for field in ("purchase_date", "warranty_expires"):
        if data.get(field):
            try:
                date.fromisoformat(data[field])
            except ValueError:
                errors.append(f"{FIELD_LABELS[field]} must be a date in YYYY-MM-DD format.")
    if data.get("asset_tag"):
        row = db.execute(
            "SELECT id FROM devices WHERE asset_tag = ? AND id IS NOT ?",
            (data["asset_tag"], device_id),
        ).fetchone()
        if row:
            errors.append(f"Asset tag '{data['asset_tag']}' is already in use.")
    return errors


def save_device(db, data, device_id=None):
    """Insert or update a device, recording field changes in history.

    `data` holds DEVICE_FIELDS plus `location_id`. Returns the device id.
    """
    columns = [f for f, _ in DEVICE_FIELDS] + ["location_id"]
    if device_id is None:
        cur = db.execute(
            f"INSERT INTO devices ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            [data.get(c) for c in columns],
        )
        device_id = cur.lastrowid
        db.execute(
            "INSERT INTO device_history (device_id, field, new_value) VALUES (?, 'created', ?)",
            (device_id, location_name(db, data.get("location_id"))),
        )
        return device_id

    old = db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    changes = []
    for col in columns:
        if old[col] != data.get(col):
            if col == "location_id":
                changes.append(
                    ("location", location_name(db, old[col]), location_name(db, data.get(col)))
                )
            else:
                changes.append((col, old[col], data.get(col)))
    if changes:
        db.execute(
            f"UPDATE devices SET {', '.join(c + ' = ?' for c in columns)}, "
            "updated_at = datetime('now') WHERE id = ?",
            [data.get(c) for c in columns] + [device_id],
        )
        db.executemany(
            "INSERT INTO device_history (device_id, field, old_value, new_value) VALUES (?, ?, ?, ?)",
            [(device_id, f, o, n) for f, o, n in changes],
        )
    return device_id


def query_devices(db, args):
    """Return devices matching the search/filter/sort options in `args`."""
    where, params = [], []
    q = clean(args.get("q"))
    if q:
        like = f"%{q}%"
        searchable = [
            "d.asset_tag", "d.name", "d.manufacturer", "d.model", "d.model_number",
            "d.serial_number", "d.assigned_to", "d.notes", "l.name",
        ]
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in searchable) + ")")
        params += [like] * len(searchable)
    if clean(args.get("location")):
        if args["location"] == "none":
            where.append("d.location_id IS NULL")
        else:
            where.append("d.location_id = ?")
            params.append(args["location"])
    for field in ("status", "category"):
        if clean(args.get(field)):
            where.append(f"d.{field} = ?")
            params.append(args[field])

    sort = SORTABLE.get(args.get("sort"), "d.asset_tag")
    direction = "DESC" if args.get("dir") == "desc" else "ASC"
    sql = (
        "SELECT d.*, l.name AS location FROM devices d "
        "LEFT JOIN locations l ON l.id = d.location_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY {sort} IS NULL, {sort} COLLATE NOCASE {direction}, d.id"
    )
    return db.execute(sql, params).fetchall()


def form_to_device(form, db):
    data = {f: clean(form.get(f)) for f, _ in DEVICE_FIELDS}
    data["status"] = data["status"] or STATUSES[0]
    loc = clean(form.get("location_id"))
    new_loc = clean(form.get("new_location"))
    if new_loc:
        data["location_id"] = get_or_create_location(db, new_loc)
    elif loc:
        data["location_id"] = int(loc)
    else:
        data["location_id"] = None
    return data


# ------------------------------------------------------------------ routes


def register_routes(app):
    @app.route("/")
    def index():
        db = get_db()
        devices = query_devices(db, request.args)
        locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
        stats = db.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(status = 'In Service') AS in_service,"
            " SUM(status = 'In Repair') AS in_repair,"
            " SUM(warranty_expires IS NOT NULL AND warranty_expires < date('now')"
            "     AND status NOT IN ('Retired', 'Lost')) AS out_of_warranty"
            " FROM devices"
        ).fetchone()
        return render_template(
            "index.html", devices=devices, locations=locations, stats=stats, args=request.args
        )

    @app.route("/devices/new", methods=["GET", "POST"])
    def device_new():
        db = get_db()
        device = {"status": STATUSES[0]}
        errors = []
        if request.method == "POST":
            device = form_to_device(request.form, db)
            errors = validate_device(device, db)
            if not errors:
                device_id = save_device(db, device)
                db.commit()
                flash(f"Added {device['asset_tag']}.", "success")
                if request.form.get("add_another"):
                    return redirect(url_for("device_new"))
                return redirect(url_for("device_view", device_id=device_id))
            db.rollback()
        locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
        return render_template(
            "device_form.html", device=device, locations=locations, errors=errors, new=True
        )

    @app.route("/devices/<int:device_id>")
    def device_view(device_id):
        db = get_db()
        device = db.execute(
            "SELECT d.*, l.name AS location FROM devices d "
            "LEFT JOIN locations l ON l.id = d.location_id WHERE d.id = ?",
            (device_id,),
        ).fetchone()
        if device is None:
            abort(404)
        history = db.execute(
            "SELECT * FROM device_history WHERE device_id = ? ORDER BY id DESC", (device_id,)
        ).fetchall()
        return render_template("device_view.html", device=device, history=history)

    @app.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
    def device_edit(device_id):
        db = get_db()
        row = db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            abort(404)
        device, errors = dict(row), []
        if request.method == "POST":
            device = form_to_device(request.form, db)
            device["id"] = device_id
            errors = validate_device(device, db, device_id)
            if not errors:
                save_device(db, device, device_id)
                db.commit()
                flash("Changes saved.", "success")
                return redirect(url_for("device_view", device_id=device_id))
            db.rollback()
        locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
        return render_template(
            "device_form.html", device=device, locations=locations, errors=errors, new=False
        )

    @app.route("/devices/<int:device_id>/delete", methods=["POST"])
    def device_delete(device_id):
        db = get_db()
        row = db.execute("SELECT asset_tag FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            abort(404)
        db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        db.commit()
        flash(f"Deleted {row['asset_tag']}.", "success")
        return redirect(url_for("index"))

    @app.route("/locations", methods=["GET", "POST"])
    def locations():
        db = get_db()
        if request.method == "POST":
            name = clean(request.form.get("name"))
            if not name:
                flash("Location name is required.", "error")
            else:
                try:
                    db.execute(
                        "INSERT INTO locations (name, building, room, notes) VALUES (?, ?, ?, ?)",
                        (name, clean(request.form.get("building")),
                         clean(request.form.get("room")), clean(request.form.get("notes"))),
                    )
                    db.commit()
                    flash(f"Added location {name}.", "success")
                except sqlite3.IntegrityError:
                    flash(f"A location named '{name}' already exists.", "error")
            return redirect(url_for("locations"))
        rows = db.execute(
            "SELECT l.*, COUNT(d.id) AS device_count FROM locations l "
            "LEFT JOIN devices d ON d.location_id = l.id GROUP BY l.id ORDER BY l.name"
        ).fetchall()
        return render_template("locations.html", locations=rows)

    @app.route("/locations/<int:location_id>/edit", methods=["GET", "POST"])
    def location_edit(location_id):
        db = get_db()
        loc = db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
        if loc is None:
            abort(404)
        if request.method == "POST":
            name = clean(request.form.get("name"))
            if not name:
                flash("Location name is required.", "error")
            else:
                try:
                    db.execute(
                        "UPDATE locations SET name = ?, building = ?, room = ?, notes = ? WHERE id = ?",
                        (name, clean(request.form.get("building")),
                         clean(request.form.get("room")), clean(request.form.get("notes")),
                         location_id),
                    )
                    db.commit()
                    flash("Location saved.", "success")
                    return redirect(url_for("locations"))
                except sqlite3.IntegrityError:
                    flash(f"A location named '{name}' already exists.", "error")
        return render_template("location_form.html", location=loc)

    @app.route("/locations/<int:location_id>/delete", methods=["POST"])
    def location_delete(location_id):
        db = get_db()
        count = db.execute(
            "SELECT COUNT(*) FROM devices WHERE location_id = ?", (location_id,)
        ).fetchone()[0]
        if count:
            flash(f"Can't delete: {count} device(s) are still at this location. Move them first.",
                  "error")
        else:
            db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
            db.commit()
            flash("Location deleted.", "success")
        return redirect(url_for("locations"))

    @app.route("/export.csv")
    def export_csv():
        devices = query_devices(get_db(), request.args)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for d in devices:
            writer.writerow([d[c] if d[c] is not None else "" for c in CSV_COLUMNS])
        filename = f"device-inventory-{date.today().isoformat()}.csv"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.route("/import", methods=["GET", "POST"])
    def import_csv():
        if request.method == "GET":
            return render_template("import.html", columns=CSV_COLUMNS, result=None)
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file to import.", "error")
            return redirect(url_for("import_csv"))
        text = upload.read().decode("utf-8-sig", errors="replace")
        result = import_rows(get_db(), csv.DictReader(io.StringIO(text)))
        return render_template("import.html", columns=CSV_COLUMNS, result=result)


def import_rows(db, reader):
    """Import CSV rows. Rows whose asset tag already exists update that device."""
    header_map = {}
    for h in reader.fieldnames or []:
        key = h.strip().lower().replace(" ", "_")
        if key in CSV_COLUMNS:
            header_map[h] = key
    result = {"added": 0, "updated": 0, "errors": []}
    if "asset_tag" not in header_map.values():
        result["errors"].append("CSV must have an 'asset_tag' column.")
        return result

    for line_no, raw in enumerate(reader, start=2):
        row = {key: clean(raw.get(h)) for h, key in header_map.items()}
        if not any(row.values()):
            continue
        existing = None
        if row.get("asset_tag"):
            existing = db.execute(
                "SELECT * FROM devices WHERE asset_tag = ?", (row["asset_tag"],)
            ).fetchone()
        # Start from the existing record so columns missing from the CSV are kept.
        data = dict(existing) if existing else {"status": STATUSES[0]}
        for key, value in row.items():
            if key != "location" and (value is not None or not existing):
                data[key] = value
        data["status"] = data.get("status") or STATUSES[0]
        # Accept status case-insensitively.
        for s in STATUSES:
            if data["status"].lower() == s.lower():
                data["status"] = s
        if "location" in row and (row["location"] or not existing):
            data["location_id"] = get_or_create_location(db, row["location"])

        device_id = existing["id"] if existing else None
        errors = validate_device(data, db, device_id)
        if errors:
            result["errors"].append(f"Row {line_no}: " + " ".join(errors))
            continue
        save_device(db, data, device_id)
        result["updated" if existing else "added"] += 1
    db.commit()
    return result


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("INVENTORY_HOST", "127.0.0.1")
    port = int(os.environ.get("INVENTORY_PORT", "5000"))
    app.run(host=host, port=port, debug=os.environ.get("INVENTORY_DEBUG") == "1")
