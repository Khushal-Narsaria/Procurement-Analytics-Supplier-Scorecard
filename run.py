"""Build the database, run the analysis and export docs/data.json for the dashboard.

    python run.py
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from procure.analytics import (  # noqa: E402
    HOLDING_RATE, MIN_SCORE_FOR_AWARD, ORDER_COST, TIERS, WEIGHTS, abc_classify, backtest,
    consolidation_savings, inventory_policy, pareto_count, scorecard,
)
from procure.generate import DAYS, END, SEED, START, generate  # noqa: E402

DB = ROOT / "data" / "procurement.db"


def query(conn, name: str) -> pd.DataFrame:
    return pd.read_sql_query((ROOT / "sql" / f"{name}.sql").read_text(), conn)


def build() -> tuple[sqlite3.Connection, dict]:
    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    conn = sqlite3.connect(DB)
    return conn, generate(conn)


def inventory(conn) -> tuple[pd.DataFrame, dict]:
    materials = pd.read_sql_query("SELECT * FROM material", conn).set_index("material_id")
    demand = pd.read_sql_query("SELECT material_id, qty FROM demand ORDER BY material_id, day", conn)
    lines = pd.read_sql_query(
        "SELECT material_id, julianday(order_date) - julianday(?) AS day,"
        " julianday(received_date) - julianday(order_date) AS lead FROM po_line WHERE received_date IS NOT NULL",
        conn, params=(START.isoformat(),))
    open_qty = pd.read_sql_query(
        "SELECT material_id, SUM(qty_ordered) AS q FROM po_line WHERE received_date IS NULL GROUP BY material_id",
        conn).set_index("material_id")["q"]
    rng = np.random.default_rng(SEED)
    rows, totals = [], {"rop": [0.0, 0.0, 0.0], "calendar": [0.0, 0.0, 0.0], "demand": 0.0}
    half = DAYS // 2
    for mid, m in materials.iterrows():
        series = demand.loc[demand["material_id"] == mid, "qty"].to_numpy()
        leads = lines.loc[lines["material_id"] == mid]
        # Year 1 sets the policy; year 2 tests it, so the backtest is out of sample.
        fit_leads = leads.loc[leads["day"] < half, "lead"].to_numpy()
        fit = inventory_policy(series[:half].mean(), series[:half].std(), fit_leads.mean(), fit_leads.std(), m["contract_price"])
        start = fit["reorder_point"] + fit["eoq"] / 2
        test, test_leads = series[half:], leads.loc[leads["day"] >= half, "lead"].to_numpy()
        a = backtest(test, test_leads, start, rng, reorder_point=fit["reorder_point"], order_qty=fit["eoq"])
        b = backtest(test, test_leads, start, rng, order_qty=series[:half].mean() * 30, every=30)
        for key, r in (("rop", a), ("calendar", b)):
            totals[key][0] += r["fill_rate"] * test.sum()
            totals[key][1] += r["avg_stock"] * m["contract_price"]
            totals[key][2] += r["orders"]
        totals["demand"] += test.sum()

        now = inventory_policy(series.mean(), series.std(), leads["lead"].mean(), leads["lead"].std(), m["contract_price"])
        on_order = int(open_qty.get(mid, 0))
        position = m["on_hand"] + on_order
        status = "Reorder now" if position <= now["reorder_point"] else "OK"
        if m["on_hand"] < series.mean() * leads["lead"].mean() and on_order == 0:
            status = "Stockout risk"
        rows.append({
            "material_id": int(mid), "name": m["name"], "category": m["category"], "on_hand": int(m["on_hand"]),
            "on_order": on_order, "daily_demand": round(float(series.mean()), 1), "lead_time": round(float(leads["lead"].mean()), 1),
            "safety_stock": int(round(now["safety_stock"])), "reorder_point": int(round(now["reorder_point"])),
            "eoq": int(round(now["eoq"])), "days_cover": round(float(m["on_hand"] / series.mean()), 1), "status": status,
        })
    summary = {
        k: {"fill_rate": round(totals[k][0] / totals["demand"], 4), "avg_stock_value": round(totals[k][1]),
            "orders": int(totals[k][2]),
            "annual_cost": round(totals[k][2] * ORDER_COST + HOLDING_RATE * totals[k][1])} for k in ("rop", "calendar")
    }
    return pd.DataFrame(rows), summary


def main() -> None:
    conn, latent = build()
    sc = scorecard(query(conn, "supplier_metrics"))
    lat = pd.DataFrame(latent).T.loc[sc["supplier_id"]]
    recovery = {
        "on_time": spearmanr(sc["on_time_rate"], lat["on_time"].to_numpy()).statistic,
        "reject_rate": spearmanr(sc["reject_rate"], lat["defect"].to_numpy()).statistic,
        "price": spearmanr(sc["price_variance"], lat["price_index"].to_numpy()).statistic,
    }
    by_cat, by_month = query(conn, "spend_by_category"), query(conn, "spend_by_month")
    total = float(sc["spend"].sum())

    mat = pd.read_sql_query(
        "SELECT m.material_id, m.name, m.category, SUM(p.qty_received * p.unit_price) AS spend FROM po_line p"
        " JOIN material m USING (material_id) WHERE p.received_date IS NOT NULL GROUP BY m.material_id", conn)
    mat["abc"] = abc_classify(mat.set_index("material_id")["spend"]).to_numpy()
    abc = mat.groupby("abc").agg(items=("spend", "size"), spend=("spend", "sum")).reset_index()

    savings = consolidation_savings(query(conn, "material_supplier_price"), sc.set_index("supplier_id")["score"])
    names, sup_names = mat.set_index("material_id")["name"], sc.set_index("supplier_id")["name"]
    overpaid = pd.read_sql_query(
        "SELECT SUM(p.qty_received * MAX(p.unit_price - m.contract_price, 0)) AS v FROM po_line p JOIN material m"
        " USING (material_id) WHERE p.received_date IS NOT NULL", conn)["v"][0]

    inv, policy = inventory(conn)
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT 'lines', COUNT(*) FROM po_line UNION ALL SELECT 'open', COUNT(*) FROM po_line WHERE received_date IS NULL"
        " UNION ALL SELECT 'demand', COUNT(*) FROM demand")}

    def r(v, n=1):
        return round(float(v), n)

    out = {
        "simulated": True, "seed": SEED, "period": [START.isoformat(), END.isoformat()],
        "counts": {"suppliers": len(sc), "materials": len(mat), "po_lines": counts["lines"], "open_lines": counts["open"],
                   "demand_rows": counts["demand"], "categories": len(by_cat)},
        "spend": {"total": round(total), "pareto_suppliers": pareto_count(sc.set_index("supplier_id")["spend"]),
                  "overpaid_vs_contract": round(float(overpaid)),
                  "by_category": [{"category": c, "spend": round(s), "suppliers": int(n)} for c, s, n in zip(by_cat["category"], by_cat["spend"], by_cat["suppliers"])],
                  "by_month": [{"month": m, "spend": round(s)} for m, s in zip(by_month["month"], by_month["spend"])],
                  "abc": [{"class": a, "items": int(i), "spend": round(s), "share": r(100 * s / total)} for a, i, s in zip(abc["abc"], abc["items"], abc["spend"])]},
        "weights": WEIGHTS, "tiers": [{"name": n, "min": m} for n, m in TIERS] + [{"name": "At risk", "min": 0}],
        "suppliers": [{
            "id": int(s.supplier_id), "name": s.name, "city": s.city, "lines": int(s.lines), "spend": round(s.spend),
            "on_time": r(100 * s.on_time_rate), "days_late": r(s.avg_days_late), "reject": r(100 * s.reject_rate, 2),
            "price_var": r(100 * s.price_variance, 2), "lead": r(s.lead_time_days), "delivery": r(s.delivery),
            "quality": r(s.quality), "price": r(s.price), "consistency": r(s.consistency), "score": r(s.score), "tier": s.tier,
        } for s in sc.itertuples()],
        "recovery": {k: round(float(v), 3) for k, v in recovery.items()},
        "savings": {"total": round(float(savings["saving"].sum())), "share_of_spend": r(100 * savings["saving"].sum() / total, 2),
                    "materials": len(savings), "min_score": MIN_SCORE_FOR_AWARD,
                    "top": [{"material": names[x.material_id], "to": sup_names[x.to_supplier], "saving": round(x.saving),
                             "pct": r(100 * x.saving / x.spend)} for x in savings.head(10).itertuples()]},
        "inventory": {"assumptions": {"service_level": 0.95, "order_cost": ORDER_COST, "holding_rate": HOLDING_RATE},
                      "backtest": policy,
                      "status": inv["status"].value_counts().to_dict(),
                      "items": inv.sort_values("days_cover").to_dict("records")},
    }
    (ROOT / "docs" / "data.json").write_text(json.dumps(out, separators=(",", ":")))
    show = {k: out[k] for k in ("counts", "recovery")}
    show["spend"] = {k: out["spend"][k] for k in ("total", "pareto_suppliers", "overpaid_vs_contract", "abc")}
    show["savings"] = {k: out["savings"][k] for k in ("total", "share_of_spend", "materials")}
    show["inventory"] = {"backtest": policy, "status": out["inventory"]["status"]}
    show["tiers"] = sc["tier"].value_counts().to_dict()
    print(json.dumps(show, indent=1))


if __name__ == "__main__":
    main()
