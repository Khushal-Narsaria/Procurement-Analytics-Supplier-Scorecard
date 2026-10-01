-- Average paid price per material and supplier: the basis for the savings analysis.
SELECT p.material_id, p.supplier_id, SUM(p.qty_received) AS qty,
       SUM(p.qty_received * p.unit_price) / SUM(p.qty_received) AS avg_price
FROM po_line p WHERE p.received_date IS NOT NULL AND p.qty_received > 0
GROUP BY p.material_id, p.supplier_id;
