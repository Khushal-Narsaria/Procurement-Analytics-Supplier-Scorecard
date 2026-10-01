"""Scorecard, spend classification, savings and inventory policy. Pure functions over DataFrames."""
import math

import numpy as np
import pandas as pd

# Scorecard weights and scales are project assumptions; change them here to match a sourcing policy.
WEIGHTS = {"delivery": 0.40, "quality": 0.30, "price": 0.20, "consistency": 0.10}
REJECT_RATE_ZERO = 0.10          # a 10% reject rate scores 0 on quality
PRICE_BEST, PRICE_WORST = -0.05, 0.10  # paid vs contract: 5% under scores 100, 10% over scores 0
DAYS_LATE_ZERO = 10.0            # averaging 10 days late scores 0 on consistency
TIERS = [("Preferred", 80), ("Approved", 65), ("Watch", 50)]
MIN_SCORE_FOR_AWARD = 65         # only Approved or better suppliers can receive consolidated volume

SERVICE_Z = 1.645                # 95% cycle service level
ORDER_COST = 2500.0              # INR per purchase order (assumption)
HOLDING_RATE = 0.20              # annual holding cost as a share of unit price (assumption)


def _scale(x: pd.Series, zero_at: float, full_at: float) -> pd.Series:
    return (100 * (x - zero_at) / (full_at - zero_at)).clip(0, 100)


def tier(score: float) -> str:
    for name, floor in TIERS:
        if score >= floor:
            return name
    return "At risk"


def scorecard(metrics: pd.DataFrame) -> pd.DataFrame:
    out = metrics.copy()
    out["delivery"] = 100 * out["on_time_rate"]
    out["quality"] = _scale(out["reject_rate"], REJECT_RATE_ZERO, 0.0)
    out["price"] = _scale(out["price_variance"], PRICE_WORST, PRICE_BEST)
    out["consistency"] = _scale(out["avg_days_late"], DAYS_LATE_ZERO, 0.0)
    out["score"] = sum(out[k] * w for k, w in WEIGHTS.items())
    out["tier"] = out["score"].map(tier)
    return out.sort_values("score", ascending=False).reset_index(drop=True)


def abc_classify(spend: pd.Series, a: float = 0.80, b: float = 0.95) -> pd.Series:
    """A items make up the first 80% of spend, B the next 15%, C the rest."""
    ordered = spend.sort_values(ascending=False)
    before = (ordered.cumsum() - ordered) / ordered.sum()  # share of spend ranked ahead of each item
    return before.map(lambda c: "A" if c < a else "B" if c < b else "C").reindex(spend.index)


def pareto_count(spend: pd.Series, share: float = 0.80) -> int:
    """Smallest number of suppliers that together account for `share` of spend."""
    cum = spend.sort_values(ascending=False).cumsum() / spend.sum()
    return int((cum < share).sum() + 1)


def consolidation_savings(prices: pd.DataFrame, scores: pd.Series) -> pd.DataFrame:
    """Saving if each material's volume moved to its cheapest supplier scoring at least MIN_SCORE_FOR_AWARD."""
    rows = []
    for material_id, g in prices.groupby("material_id"):
        eligible = g[g["supplier_id"].map(scores) >= MIN_SCORE_FOR_AWARD]
        if eligible.empty:
            continue
        best = eligible.loc[eligible["avg_price"].idxmin()]
        saving = float(((g["avg_price"] - best["avg_price"]).clip(lower=0) * g["qty"]).sum())
        if saving > 0:
            rows.append({"material_id": int(material_id), "to_supplier": int(best["supplier_id"]), "saving": saving,
                         "spend": float((g["avg_price"] * g["qty"]).sum())})
    return pd.DataFrame(rows).sort_values("saving", ascending=False).reset_index(drop=True)


def inventory_policy(daily_mean: float, daily_std: float, lead_mean: float, lead_std: float, unit_price: float) -> dict:
    """Reorder point with safety stock for demand and lead-time uncertainty, plus the economic order quantity."""
    safety = SERVICE_Z * math.sqrt(lead_mean * daily_std**2 + daily_mean**2 * lead_std**2)
    eoq = math.sqrt(2 * daily_mean * 365 * ORDER_COST / (HOLDING_RATE * unit_price))
    return {"safety_stock": safety, "reorder_point": daily_mean * lead_mean + safety, "eoq": max(eoq, 1.0)}


def backtest(demand: np.ndarray, lead_times: np.ndarray, start: float, rng: np.random.Generator,
             reorder_point: float | None = None, order_qty: float = 0.0, every: int | None = None) -> dict:
    """Day-by-day stock simulation with lost sales. Either a reorder-point policy or a fixed calendar order."""
    on_hand, pipeline, served, held = float(start), [], 0.0, 0.0
    orders = 0
    for day, need in enumerate(demand):
        on_hand += sum(q for due, q in pipeline if due == day)
        pipeline = [(due, q) for due, q in pipeline if due > day]
        sold = min(on_hand, need)
        served += sold
        on_hand -= sold
        position = on_hand + sum(q for _, q in pipeline)
        place = position <= reorder_point if reorder_point is not None else day % every == 0
        if place:
            pipeline.append((day + max(1, int(round(rng.choice(lead_times)))), order_qty))
            orders += 1
        held += on_hand
    return {"fill_rate": served / max(float(demand.sum()), 1.0), "avg_stock": held / len(demand), "orders": orders}
