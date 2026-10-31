-- A publication reconciliation prepares both stage Jobs in the same commit.
INSERT INTO processing_dirty(item)
SELECT DISTINCT item FROM processing_stage_dirty WHERE item IS NOT NULL
ON CONFLICT(item) DO NOTHING;
DROP TABLE processing_stage_dirty;
