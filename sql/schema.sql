CREATE TABLE supplier (
    supplier_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL
);
CREATE TABLE material (
    material_id    INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    contract_price REAL NOT NULL CHECK (contract_price > 0),
    on_hand        INTEGER NOT NULL CHECK (on_hand >= 0)
);
CREATE TABLE po_line (
    po_line_id    INTEGER PRIMARY KEY,
    supplier_id   INTEGER NOT NULL REFERENCES supplier (supplier_id),
    material_id   INTEGER NOT NULL REFERENCES material (material_id),
    order_date    TEXT NOT NULL,
    promised_date TEXT NOT NULL,
    received_date TEXT,                      -- NULL while the order is still open
    qty_ordered   INTEGER NOT NULL CHECK (qty_ordered > 0),
    qty_received  INTEGER NOT NULL DEFAULT 0,
    qty_rejected  INTEGER NOT NULL DEFAULT 0 CHECK (qty_rejected <= qty_received),
    unit_price    REAL NOT NULL CHECK (unit_price > 0)
);
CREATE TABLE demand (
    material_id INTEGER NOT NULL REFERENCES material (material_id),
    day         TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    PRIMARY KEY (material_id, day)
);
CREATE INDEX idx_po_supplier ON po_line (supplier_id);
CREATE INDEX idx_po_material ON po_line (material_id);
