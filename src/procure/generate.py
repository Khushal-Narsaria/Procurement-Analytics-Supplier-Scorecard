"""Seeded simulator for two years of purchasing at a process plant.

No public data set carries supplier delivery, quality and price history together, so the demo data
are simulated. Every supplier has hidden "true" behaviour (on-time probability, defect rate, price
level). The analytics never see those values; tests use them to check the scorecard recovers them.
Supplier names are generated and do not refer to real companies.
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np

SEED = 7
START, DAYS = date(2024, 10, 1), 730
END = START + timedelta(days=DAYS - 1)
# category -> typical unit price in INR
CATEGORIES = {
    "Catalysts & Chemicals": 4200, "Valves & Fittings": 2600, "Rotating Equipment Spares": 9800,
    "Electrical": 1900, "Instrumentation": 6400, "Piping & Flanges": 1500, "Safety & PPE": 650,
    "Packaging": 240,
}
SUPPLIERS_PER_CATEGORY, MATERIALS_PER_CATEGORY = 5, 15
NAME_A = ["Aravali", "Kaveri", "Sahyadri", "Nilgiri", "Vindhya", "Narmada", "Godavari", "Konkan", "Malwa", "Deccan",
          "Satpura", "Tapti", "Kutch", "Coromandel", "Malabar", "Gomti", "Chambal", "Periyar", "Sabarmati", "Yamuna"]
NAME_B = ["Industrial Supplies", "Engineering Works", "Process Equipment", "Trading Co", "Technologies", "Enterprises"]
CITIES = ["Vadodara", "Jamnagar", "Surat", "Mumbai", "Pune", "Chennai", "Hyderabad", "Ahmedabad", "Coimbatore", "Kolkata"]
SCHEMA = Path(__file__).parents[2] / "sql" / "schema.sql"


def generate(conn: sqlite3.Connection, seed: int = SEED) -> dict:
    """Fill an empty database and return the hidden supplier behaviour keyed by supplier_id."""
    rng = np.random.default_rng(seed)
    conn.executescript(SCHEMA.read_text())
    latent, by_category, sid = {}, {}, 0
    names = [f"{a} {b}" for a in NAME_A for b in NAME_B]
    rng.shuffle(names)
    for cat in CATEGORIES:
        for _ in range(SUPPLIERS_PER_CATEGORY):
            sid += 1
            latent[sid] = {
                "on_time": float(np.clip(rng.beta(7, 2), 0.45, 0.98)),
                "late_days": float(rng.uniform(2, 12)),
                "defect": float(np.clip(rng.beta(1.3, 40), 0.002, 0.15)),
                "price_index": float(np.clip(rng.normal(1.0, 0.04), 0.93, 1.12)),
                "lead": int(rng.integers(7, 36)),
            }
            by_category.setdefault(cat, []).append(sid)
            conn.execute("INSERT INTO supplier VALUES (?,?,?)", (sid, names[sid - 1], str(rng.choice(CITIES))))

    days = [START + timedelta(days=i) for i in range(DAYS)]
    weekday = np.array([d.weekday() for d in days])
    mid, po_rows, demand_rows = 0, [], []
    for cat, base_price in CATEGORIES.items():
        for k in range(MATERIALS_PER_CATEGORY):
            mid += 1
            price = float(round(base_price * rng.lognormal(0, 0.5), 2))
            mean = float(rng.lognormal(3.0, 0.6))
            cv = float(rng.uniform(0.2, 0.6))
            series = rng.normal(mean, cv * mean, DAYS) * np.where(weekday == 6, 0.5, 1.0)  # quieter Sundays
            series = np.maximum(series.round(), 0).astype(int)
            demand_rows += [(mid, d.isoformat(), int(q)) for d, q in zip(days, series)]

            approved = rng.choice(by_category[cat], size=int(rng.integers(2, 4)), replace=False)
            share = rng.dirichlet(np.ones(len(approved)) * 2)
            mean_lead = np.mean([latent[int(s)]["lead"] for s in approved])
            on_hand = int(mean * mean_lead * rng.uniform(0.3, 2.0))
            conn.execute("INSERT INTO material VALUES (?,?,?,?,?)", (mid, f"{cat.split()[0]} item {k + 1:02d}", cat, price, on_hand))

            t = int(rng.integers(0, 10))
            while t < DAYS:
                s = int(rng.choice(approved, p=share))
                b = latent[s]
                ordered = max(1, int(mean * 10 * rng.uniform(0.8, 1.2)))
                order_day = days[t]
                promised = order_day + timedelta(days=b["lead"])
                if rng.random() < b["on_time"]:
                    received = promised - timedelta(days=int(rng.integers(0, 3)))
                else:
                    received = promised + timedelta(days=1 + int(rng.poisson(b["late_days"])))
                got = ordered if rng.random() > 0.05 else int(ordered * rng.uniform(0.9, 0.99))
                rejected = int(rng.binomial(got, b["defect"]))
                paid = round(price * b["price_index"] * rng.normal(1, 0.015), 2)
                is_open = received > END
                po_rows.append((s, mid, order_day.isoformat(), promised.isoformat(), None if is_open else received.isoformat(),
                                ordered, 0 if is_open else got, 0 if is_open else rejected, paid))
                t += int(rng.integers(8, 13))
    conn.executemany("INSERT INTO demand VALUES (?,?,?)", demand_rows)
    conn.executemany(
        "INSERT INTO po_line (supplier_id, material_id, order_date, promised_date, received_date, qty_ordered,"
        " qty_received, qty_rejected, unit_price) VALUES (?,?,?,?,?,?,?,?,?)", po_rows)
    conn.commit()
    return latent
