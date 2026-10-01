SELECT material_id, julianday(received_date) - julianday(order_date) AS lead_time
FROM po_line WHERE received_date IS NOT NULL;
