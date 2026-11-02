CREATE TABLE IF NOT EXISTS locations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    building TEXT,
    room     TEXT,
    notes    TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_tag        TEXT UNIQUE COLLATE NOCASE,  -- optional for batches
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
    is_batch         INTEGER NOT NULL DEFAULT 0,  -- 1 = identical items counted by quantity
    quantity         INTEGER NOT NULL DEFAULT 1,
    min_quantity     INTEGER,                     -- reorder below this (batches)
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

-- Choices for the drop-downs managed on the Lists page.
CREATE TABLE IF NOT EXISTS options (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,  -- category, status, ticket_category, technician
    name       TEXT NOT NULL COLLATE NOCASE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, name)
);

-- Help desk: tickets, the devices each one is about, and its conversation.
CREATE TABLE IF NOT EXISTS tickets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    subject           TEXT NOT NULL,
    requester_name    TEXT,
    requester_email   TEXT,
    status            TEXT NOT NULL DEFAULT 'Open',    -- New, Open, Waiting, Closed
    priority          TEXT NOT NULL DEFAULT 'Normal',  -- Low, Normal, High, Urgent
    category          TEXT,
    assigned_to       TEXT,
    location_id       INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    source            TEXT NOT NULL DEFAULT 'manual',  -- manual, email, spiceworks
    spiceworks_number TEXT,
    gmail_thread_id   TEXT,                            -- the ticket's email thread
    unread            INTEGER NOT NULL DEFAULT 0,      -- 1 = new email or reply nobody has opened yet
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_hd_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_hd_tickets_location ON tickets(location_id);

CREATE TABLE IF NOT EXISTS ticket_devices (
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    PRIMARY KEY (ticket_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_ticket_devices_device ON ticket_devices(device_id);

-- kind: description (the original request), note (internal), reply (to the requester),
-- auto (the automatic "we got it" email), notify (an email to the IT team about the ticket),
-- incoming (a reply from the requester), event (a change such as "Status: Open -> Closed").
CREATE TABLE IF NOT EXISTS ticket_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    author       TEXT,
    author_email TEXT,
    body         TEXT NOT NULL,
    message_id   TEXT,  -- the email's Message-ID header, to match replies
    gmail_id     TEXT,  -- Gmail's id for the email, so it's only imported once
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_message_id ON ticket_messages(message_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_messages_gmail ON ticket_messages(gmail_id);
CREATE INDEX IF NOT EXISTS idx_tickets_thread ON tickets(gmail_thread_id);
