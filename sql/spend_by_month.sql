SELECT substr(order_date, 1, 7) AS month, SUM(qty_received * unit_price) AS spend, COUNT(*) AS lines
FROM po_line WHERE received_date IS NOT NULL
GROUP BY month ORDER BY month;
