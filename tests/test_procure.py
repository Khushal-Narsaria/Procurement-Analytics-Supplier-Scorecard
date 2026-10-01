import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procure.analytics import (  # noqa: E402
    SERVICE_Z, abc_classify, backtest, consolidation_savings, inventory_policy, pareto_count, scorecard, tier,
)
from procure.generate import generate  # noqa: E402


@pytest.fixture(scope="module")
def db():
    conn = sqlite3.connect(":memory:")
    latent = generate(conn)
    return conn, latent


def metrics(conn) -> pd.DataFrame:
    return pd.read_sql_query((ROOT / "sql/supplier_metrics.sql").read_text(), conn)


def test_generation_is_deterministic(db):
    other = sqlite3.connect(":memory:")
    generate(other)
    q = "SELECT COUNT(*), SUM(qty_ordered), ROUND(SUM(unit_price), 2) FROM po_line"
    assert db[0].execute(q).fetchone() == other.execute(q).fetchone()


def test_data_integrity(db):
    conn = db[0]
    assert conn.execute("SELECT COUNT(*) FROM po_line WHERE qty_rejected > qty_received OR qty_received > qty_ordered").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM po_line WHERE promised_date < order_date").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM po_line p LEFT JOIN supplier s USING (supplier_id) WHERE s.supplier_id IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(DISTINCT supplier_id) FROM po_line").fetchone()[0] == 40


def test_sql_metrics_match_a_hand_calculation():
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "sql/schema.sql").read_text())
    conn.execute("INSERT INTO supplier VALUES (1, 'S', 'X')")
    conn.execute("INSERT INTO material VALUES (1, 'M', 'C', 100.0, 0)")
    rows = [  # order, promised, received, ordered, received qty, rejected, price
        ("2025-01-01", "2025-01-11", "2025-01-10", 100, 100, 0, 110.0),   # on time
        ("2025-02-01", "2025-02-11", "2025-02-15", 100, 100, 10, 100.0),  # 4 days late, 10 rejected
        ("2025-03-01", "2025-03-11", None, 100, 0, 0, 100.0),             # still open: ignored
    ]
    conn.executemany("INSERT INTO po_line (supplier_id, material_id, order_date, promised_date, received_date,"
                     " qty_ordered, qty_received, qty_rejected, unit_price) VALUES (1,1,?,?,?,?,?,?,?)", rows)
    m = metrics(conn).iloc[0]
    assert m["lines"] == 2 and m["spend"] == pytest.approx(21000)
    assert m["on_time_rate"] == pytest.approx(0.5) and m["avg_days_late"] == pytest.approx(2.0)
    assert m["reject_rate"] == pytest.approx(0.05) and m["price_variance"] == pytest.approx(0.05)
    assert m["lead_time_days"] == pytest.approx((9 + 14) / 2)


def test_scorecard_recovers_hidden_supplier_behaviour(db):
    conn, latent = db
    sc = scorecard(metrics(conn))
    lat = pd.DataFrame(latent).T.loc[sc["supplier_id"]]
    assert spearmanr(sc["on_time_rate"], lat["on_time"].to_numpy()).statistic > 0.9
    assert spearmanr(sc["reject_rate"], lat["defect"].to_numpy()).statistic > 0.9
    assert spearmanr(sc["price_variance"], lat["price_index"].to_numpy()).statistic > 0.9
    assert sc["score"].between(0, 100).all() and sc["score"].is_monotonic_decreasing


def test_scorecard_scales_and_tiers():
    perfect = pd.DataFrame([{"on_time_rate": 1.0, "reject_rate": 0.0, "price_variance": -0.05, "avg_days_late": 0.0}])
    worst = pd.DataFrame([{"on_time_rate": 0.0, "reject_rate": 0.2, "price_variance": 0.3, "avg_days_late": 30.0}])
    assert scorecard(perfect)["score"][0] == pytest.approx(100)
    assert scorecard(worst)["score"][0] == pytest.approx(0)
    assert [tier(s) for s in (80, 79.9, 65, 50, 49.9)] == ["Preferred", "Approved", "Approved", "Watch", "At risk"]


def test_abc_and_pareto():
    spend = pd.Series({"a": 80.0, "b": 10.0, "c": 6.0, "d": 4.0})
    assert abc_classify(spend).to_dict() == {"a": "A", "b": "B", "c": "B", "d": "C"}
    assert pareto_count(spend) == 1 and pareto_count(pd.Series([50.0, 30.0, 20.0])) == 2


def test_savings_only_moves_volume_to_qualified_suppliers():
    prices = pd.DataFrame({"material_id": [1, 1, 1], "supplier_id": [1, 2, 3], "qty": [100, 100, 100],
                           "avg_price": [10.0, 9.0, 7.0]})
    scores = pd.Series({1: 90.0, 2: 70.0, 3: 40.0})  # the cheapest supplier is below the award threshold
    s = consolidation_savings(prices, scores).iloc[0]
    assert s["to_supplier"] == 2 and s["saving"] == pytest.approx(100.0)


def test_inventory_formulas():
    p = inventory_policy(daily_mean=10, daily_std=0, lead_mean=5, lead_std=0, unit_price=100)
    assert p["safety_stock"] == 0 and p["reorder_point"] == 50
    assert p["eoq"] == pytest.approx(np.sqrt(2 * 3650 * 2500 / 20))
    q = inventory_policy(10, 3, 4, 0, 100)
    assert q["safety_stock"] == pytest.approx(SERVICE_Z * 3 * 2)


def test_backtest_with_steady_demand_never_stocks_out():
    demand = np.full(200, 10)
    r = backtest(demand, np.array([5.0]), start=100, rng=np.random.default_rng(0), reorder_point=60, order_qty=100)
    assert r["fill_rate"] == 1.0 and r["orders"] > 0
    starved = backtest(demand, np.array([5.0]), start=0, rng=np.random.default_rng(0), order_qty=10, every=30)
    assert starved["fill_rate"] < 0.1
