"""Device inventory tracker.

A small Flask + SQLite web app for tracking devices, where they are,
who has them, and their model / serial numbers.

Run with:  python app.py   (then open http://127.0.0.1:5000)
"""

import csv
import io
import os
import sqlite3
from datetime import date, timedelta

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import app_settings
import photo_extract

# Starting choices for the drop-downs. After the first run they live in the
# `options` table and are edited on the Lists page.
DEFAULT_STATUSES = ["In Service", "In Storage", "In Repair", "Loaned Out", "Lost", "Retired"]

DEFAULT_CATEGORIES = [
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
    ("manufacture_date", "Manufacture Date"),
    ("assigned_to", "Assigned To"),
    ("status", "Status"),
    ("purchase_date", "Purchase Date"),
    ("warranty_expires", "Warranty Expires"),
    ("replacement_date", "Replace By"),
    ("notes", "Notes"),
]
FIELD_LABELS = dict(DEVICE_FIELDS, location="Location")
CSV_COLUMNS = [f for f, _ in DEVICE_FIELDS[:8]] + ["location"] + [f for f, _ in DEVICE_FIELDS[8:]]
DATE_FIELDS = ("manufacture_date", "purchase_date", "warranty_expires", "replacement_date")
DEFAULT_LIFESPAN_YEARS = 3
DEFAULT_REMINDER_DAYS = 90
# Devices with these statuses are gone, so they don't need warranty or replacement warnings.
INACTIVE_STATUSES = ("Retired", "Lost")
OPTION_KINDS = {"category": "Categories", "status": "Statuses"}
NEW_OPTION = "__new__"  # value of the "+ Add new…" entry in the form drop-downs

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
    "replacement_date": "d.replacement_date",
}


def create_app(test_config=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(app_settings.RESOURCE_DIR, "templates"),
        static_folder=os.path.join(app_settings.RESOURCE_DIR, "static"),
    )
    app.config.update(DATABASE=app_settings.default_db_path(), MAX_CONTENT_LENGTH=25 * 1024 * 1024)
    if test_config:
        app.config.update(test_config)
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    app.config.setdefault("SETTINGS_PATH", app_settings.settings_path(app.config["DATABASE"]))
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = app_settings.secret_key(app.config["SETTINGS_PATH"])

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    register_routes(app)
    @app.context_processor
    def option_lists():
        db = get_db()
        return {"STATUSES": list_options(db, "status"),
                "CATEGORIES": list_options(db, "category")}

    app.jinja_env.globals.update(
        FIELD_LABELS=FIELD_LABELS,
        NEW_OPTION=NEW_OPTION,
        ai_enabled=lambda: photo_extract.is_configured(saved_api_key()),
        today=lambda: date.today().isoformat(),
        lifespan_years=lambda: int(app_setting("lifespan_years", DEFAULT_LIFESPAN_YEARS)),
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
    db = get_db()
    columns = {r["name"] for r in db.execute("PRAGMA table_info(devices)")}
    for column in ("manufacture_date", "replacement_date"):
        if columns and column not in columns:  # database from an older version
            db.execute(f"ALTER TABLE devices ADD COLUMN {column} TEXT")
    with open(os.path.join(app_settings.RESOURCE_DIR, "schema.sql")) as f:
        db.executescript(f.read())
    defaults = {"category": DEFAULT_CATEGORIES, "status": DEFAULT_STATUSES}
    for kind, names in defaults.items():
        if db.execute("SELECT 1 FROM options WHERE kind = ?", (kind,)).fetchone():
            continue
        # First run: start from the defaults plus anything already used on devices.
        used = [r[0] for r in db.execute(
            f"SELECT DISTINCT {kind} FROM devices WHERE {kind} IS NOT NULL ORDER BY {kind}")]
        for name in names + used:
            ensure_option(db, kind, name)
    db.commit()


def app_setting(key, default):
    from flask import current_app

    value = app_settings.load(current_app.config["SETTINGS_PATH"]).get(key)
    return default if value is None else value


def add_years(start, years):
    try:
        return start.replace(year=start.year + years)
    except ValueError:  # 29 February -> 28 February
        return start.replace(year=start.year + years, day=28)


def fill_replacement_date(data):
    """Default the replacement date to purchase (or manufacture) date + the device lifespan."""
    if data.get("replacement_date"):
        return
    for field in ("purchase_date", "manufacture_date"):
        try:
            start = date.fromisoformat(data.get(field) or "")
        except ValueError:
            continue
        years = int(app_setting("lifespan_years", DEFAULT_LIFESPAN_YEARS))
        data["replacement_date"] = add_years(start, years).isoformat()
        return


def list_options(db, kind):
    return [r["name"] for r in db.execute(
        "SELECT name FROM options WHERE kind = ? ORDER BY sort_order, id", (kind,))]


def find_option(db, kind, name):
    """The stored spelling of `name` (matched ignoring case), or None."""
    row = db.execute(
        "SELECT name FROM options WHERE kind = ? AND name = ?", (kind, name)).fetchone()
    return row["name"] if row else None


def ensure_option(db, kind, name):
    """Return the stored spelling of `name`, adding it to the end of the list if new."""
    name = clean(name)
    if not name:
        return None
    existing = find_option(db, kind, name)
    if existing:
        return existing
    db.execute(
        "INSERT INTO options (kind, name, sort_order) VALUES (?, ?, "
        "(SELECT COALESCE(MAX(sort_order), 0) + 1 FROM options WHERE kind = ?))",
        (kind, name, kind))
    return name


def saved_api_key():
    from flask import current_app

    return app_settings.load(current_app.config["SETTINGS_PATH"]).get("anthropic_api_key")


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
    statuses = list_options(db, "status")
    if data.get("status") not in statuses:
        errors.append(f"Status must be one of: {', '.join(statuses)}.")
    for field in DATE_FIELDS:
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


def reminder_days():
    return int(app_setting("reminder_days", DEFAULT_REMINDER_DAYS))


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
    if args.get("replace") == "due":
        where.append("d.replacement_date IS NOT NULL AND d.replacement_date <= date('now', ?) "
                     f"AND d.status NOT IN ({', '.join('?' * len(INACTIVE_STATUSES))})")
        params += [f"+{reminder_days()} days", *INACTIVE_STATUSES]
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


def assigned_people(db):
    """Everyone devices have been assigned to, for the "Assigned to" drop-down."""
    return [r[0] for r in db.execute(
        "SELECT assigned_to FROM devices WHERE assigned_to IS NOT NULL "
        "GROUP BY assigned_to COLLATE NOCASE ORDER BY assigned_to COLLATE NOCASE")]


def form_to_device(form, db):
    data = {f: clean(form.get(f)) for f, _ in DEVICE_FIELDS}
    # A name typed into "+ Add new…" becomes a new list entry (undone if the save fails).
    for kind in OPTION_KINDS:
        if data[kind] == NEW_OPTION:
            data[kind] = None
        data[kind] = ensure_option(db, kind, data[kind])
    data["status"] = data["status"] or list_options(db, "status")[0]
    fill_replacement_date(data)
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
        inactive = ", ".join("?" * len(INACTIVE_STATUSES))
        stats = db.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(warranty_expires IS NOT NULL AND warranty_expires < date('now')"
            f"     AND status NOT IN ({inactive})) AS out_of_warranty,"
            " SUM(replacement_date IS NOT NULL AND replacement_date < date('now')"
            f"     AND status NOT IN ({inactive})) AS replace_overdue,"
            " SUM(replacement_date IS NOT NULL AND replacement_date >= date('now')"
            "     AND replacement_date <= date('now', ?)"
            f"     AND status NOT IN ({inactive})) AS replace_soon"
            " FROM devices",
            [*INACTIVE_STATUSES, *INACTIVE_STATUSES, f"+{reminder_days()} days", *INACTIVE_STATUSES],
        ).fetchone()
        counts = dict(db.execute("SELECT status, COUNT(*) FROM devices GROUP BY status").fetchall())
        status_counts = [(s, counts[s]) for s in list_options(db, "status") if counts.get(s)]
        return render_template(
            "index.html", devices=devices, locations=locations, stats=stats,
            status_counts=status_counts, args=request.args, reminder_days=reminder_days(),
            soon_date=(date.today() + timedelta(days=reminder_days())).isoformat(),
        )

    @app.route("/devices/new", methods=["GET", "POST"])
    def device_new():
        db = get_db()
        device = {"status": list_options(db, "status")[0]}
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
            "device_form.html", device=device, locations=locations, errors=errors, new=True,
            people=assigned_people(db),
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
            "device_form.html", device=device, locations=locations, errors=errors, new=False,
            people=assigned_people(db),
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

    @app.route("/api/extract", methods=["POST"])
    def api_extract():
        api_key = saved_api_key()
        if not photo_extract.is_configured(api_key):
            return jsonify(error="AI photo reading isn't set up. Add an Anthropic API key "
                                 "on the Settings page."), 503
        images = [
            (f.read(), f.mimetype)
            for f in request.files.getlist("photos")
            if f and f.filename
        ]
        try:
            result = photo_extract.extract_device_info(
                images, list_options(get_db(), "category"), api_key=api_key)
        except photo_extract.ExtractionError as e:
            return jsonify(error=str(e)), 400
        return jsonify(result)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        path = app.config["SETTINGS_PATH"]
        if request.method == "POST":
            changes = {"allow_network": bool(request.form.get("allow_network"))}
            for key, low, high in (("lifespan_years", 1, 20), ("reminder_days", 0, 730)):
                try:
                    value = int(request.form.get(key, ""))
                except ValueError:
                    continue
                if low <= value <= high:
                    changes[key] = value
            new_key = clean(request.form.get("anthropic_api_key"))
            if request.form.get("remove_key"):
                changes["anthropic_api_key"] = None
            elif new_key:
                if not new_key.startswith("sk-ant-"):
                    flash("That doesn't look like an Anthropic API key (they start with sk-ant-).",
                          "error")
                    return redirect(url_for("settings"))
                changes["anthropic_api_key"] = new_key
            before = app_settings.load(path).get("allow_network", False)
            app_settings.update(path, **changes)
            msg = "Settings saved."
            if changes["allow_network"] != before:
                msg += " Close and reopen Device Inventory for the network change to take effect."
            flash(msg, "success")
            return redirect(url_for("settings"))
        data = app_settings.load(path)
        key = data.get("anthropic_api_key")
        return render_template(
            "settings.html",
            key_hint=f"…{key[-4:]}" if key else None,
            env_key=bool(os.environ.get("ANTHROPIC_API_KEY")),
            allow_network=data.get("allow_network", False),
            lifespan_years=data.get("lifespan_years", DEFAULT_LIFESPAN_YEARS),
            reminder_days=data.get("reminder_days", DEFAULT_REMINDER_DAYS),
            db_path=os.path.abspath(app.config["DATABASE"]),
            network_urls=app.config.get("NETWORK_URLS", []),
        )

    @app.route("/lists")
    def lists():
        db = get_db()
        sections = []
        for kind, title in OPTION_KINDS.items():
            rows = db.execute(
                f"SELECT o.id, o.name, (SELECT COUNT(*) FROM devices d WHERE d.{kind} = o.name) AS used "
                "FROM options o WHERE o.kind = ? ORDER BY o.sort_order, o.id", (kind,)).fetchall()
            sections.append({"kind": kind, "title": title, "rows": rows})
        return render_template("lists.html", sections=sections)

    def get_option(option_id):
        row = get_db().execute("SELECT * FROM options WHERE id = ?", (option_id,)).fetchone()
        if row is None:
            abort(404)
        return row

    def renumber(db, kind):
        ids = [r["id"] for r in db.execute(
            "SELECT id FROM options WHERE kind = ? ORDER BY sort_order, id", (kind,))]
        db.executemany("UPDATE options SET sort_order = ? WHERE id = ?",
                       [(i, oid) for i, oid in enumerate(ids)])
        return ids

    @app.route("/lists/<kind>/add", methods=["POST"])
    def option_add(kind):
        if kind not in OPTION_KINDS:
            abort(404)
        db = get_db()
        name = clean(request.form.get("name"))
        if not name:
            flash("Type a name to add.", "error")
        elif find_option(db, kind, name):
            flash(f"'{name}' is already in the list.", "error")
        else:
            ensure_option(db, kind, name)
            db.commit()
            flash(f"Added '{name}'.", "success")
        return redirect(url_for("lists") + f"#{kind}")

    @app.route("/lists/option/<int:option_id>/rename", methods=["POST"])
    def option_rename(option_id):
        db = get_db()
        opt = get_option(option_id)
        kind, old = opt["kind"], opt["name"]
        new = clean(request.form.get("name"))
        clash = new and db.execute(
            "SELECT 1 FROM options WHERE kind = ? AND name = ? AND id != ?",
            (kind, new, option_id)).fetchone()
        if not new:
            flash("The name can't be blank.", "error")
        elif clash:
            flash(f"'{new}' is already in the list.", "error")
        elif new != old:
            db.execute("UPDATE options SET name = ? WHERE id = ?", (new, option_id))
            ids = [r["id"] for r in db.execute(f"SELECT id FROM devices WHERE {kind} = ?", (old,))]
            db.execute(f"UPDATE devices SET {kind} = ?, updated_at = datetime('now') "
                       f"WHERE {kind} = ?", (new, old))
            db.executemany(
                "INSERT INTO device_history (device_id, field, old_value, new_value) VALUES (?, ?, ?, ?)",
                [(i, kind, old, new) for i in ids])
            db.commit()
            flash(f"Renamed '{old}' to '{new}'" + (f" on {len(ids)} device(s)." if ids else "."),
                  "success")
        return redirect(url_for("lists") + f"#{kind}")

    @app.route("/lists/option/<int:option_id>/move", methods=["POST"])
    def option_move(option_id):
        db = get_db()
        opt = get_option(option_id)
        ids = renumber(db, opt["kind"])
        i = ids.index(option_id)
        j = i - 1 if request.form.get("direction") == "up" else i + 1
        if 0 <= j < len(ids):
            db.execute("UPDATE options SET sort_order = ? WHERE id = ?", (j, ids[i]))
            db.execute("UPDATE options SET sort_order = ? WHERE id = ?", (i, ids[j]))
        db.commit()
        return redirect(url_for("lists") + f"#{opt['kind']}")

    @app.route("/lists/option/<int:option_id>/delete", methods=["POST"])
    def option_delete(option_id):
        db = get_db()
        opt = get_option(option_id)
        kind, name = opt["kind"], opt["name"]
        used = db.execute(f"SELECT COUNT(*) FROM devices WHERE {kind} = ?", (name,)).fetchone()[0]
        if used:
            flash(f"Can't remove '{name}': {used} device(s) use it. Change them first, "
                  "or rename it instead.", "error")
        elif kind == "status" and len(list_options(db, "status")) == 1:
            flash("There must be at least one status.", "error")
        else:
            db.execute("DELETE FROM options WHERE id = ?", (option_id,))
            db.commit()
            flash(f"Removed '{name}'.", "success")
        return redirect(url_for("lists") + f"#{kind}")

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
        data = dict(existing) if existing else {}
        for key, value in row.items():
            if key != "location" and (value is not None or not existing):
                data[key] = value
        statuses = list_options(db, "status")
        data["status"] = find_option(db, "status", data.get("status") or statuses[0]) or data["status"]
        if data.get("category"):
            data["category"] = ensure_option(db, "category", data["category"])
        fill_replacement_date(data)
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
