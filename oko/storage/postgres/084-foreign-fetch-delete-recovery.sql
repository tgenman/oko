CREATE INDEX reader_foreign_fetch_delete_recovery ON reader_problems(created,id COLLATE "C")
WHERE finished IS NULL AND identity LIKE 'job:%' AND identity LIKE '%:fetch:%' AND identity LIKE '%:delete:%'
AND body::jsonb->>'operation'='producer:status'
AND body::jsonb->>'code' IN ('delete','feed-delete','work-delete','work-feed-delete');
