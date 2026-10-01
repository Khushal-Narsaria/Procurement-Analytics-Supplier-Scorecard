SELECT m.category, SUM(p.qty_received * p.unit_price) AS spend, COUNT(DISTINCT p.supplier_id) AS suppliers,
       COUNT(*) AS lines
FROM po_line p JOIN material m ON m.material_id = p.material_id
WHERE p.received_date IS NOT NULL
GROUP BY m.category ORDER BY spend DESC;
