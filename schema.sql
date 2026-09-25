CREATE TABLE IF NOT EXISTS locations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    building TEXT,
    room     TEXT,
    notes    TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_tag        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name             TEXT NOT NULL,
    category         TEXT,
    manufacturer     TEXT,
    model            TEXT,
    model_number     TEXT,
    serial_number    TEXT,
    manufacture_date TEXT,
    location_id      INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    assigned_to      TEXT,
    status           TEXT NOT NULL DEFAULT 'In Service',
    purchase_date    TEXT,
    warranty_expires TEXT,
    replacement_date TEXT,
    notes            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(location_id);

CREATE TABLE IF NOT EXISTS device_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_device ON device_history(device_id);

-- Choices for the category and status drop-downs (managed on the Lists page).
CREATE TABLE IF NOT EXISTS options (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('category', 'status')),
    name       TEXT NOT NULL COLLATE NOCASE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, name)
);
