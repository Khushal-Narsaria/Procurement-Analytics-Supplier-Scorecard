-- One row per supplier, from received purchase-order lines only.
SELECT
    s.supplier_id,
    s.name,
    s.city,
    COUNT(*)                                                             AS lines,
    SUM(p.qty_received * p.unit_price)                                   AS spend,
    AVG(CASE WHEN p.received_date <= p.promised_date THEN 1.0 ELSE 0 END) AS on_time_rate,
    AVG(MAX(julianday(p.received_date) - julianday(p.promised_date), 0)) AS avg_days_late,
    1.0 * SUM(p.qty_rejected) / SUM(p.qty_received)                      AS reject_rate,
    SUM(p.qty_received * (p.unit_price - m.contract_price))
        / SUM(p.qty_received * m.contract_price)                         AS price_variance,
    AVG(julianday(p.received_date) - julianday(p.order_date))            AS lead_time_days
FROM po_line p
JOIN supplier s ON s.supplier_id = p.supplier_id
JOIN material m ON m.material_id = p.material_id
WHERE p.received_date IS NOT NULL
GROUP BY s.supplier_id
ORDER BY spend DESC;
