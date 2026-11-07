-- Retain subscriptions and their publications when the user removes a feed.
ALTER TABLE feeds ADD COLUMN deleted boolean NOT NULL DEFAULT false;

-- Earlier releases removed membership before asynchronous file cleanup. Restore
-- the recorded owner when it still exists; never invent an owner for an orphan.
INSERT INTO feed_publications(feed_id, publication_id, is_primary, is_unviewed)
SELECT f.id, p.id,
       NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=p.id AND m.is_primary),
       p.viewed=0
FROM jobs j JOIN publications p ON p.id=j.item
JOIN feeds f ON f.id=j.payload::jsonb->>'cleanup_feed'
WHERE j.kind='delete' AND j.status<>'done' AND p.viewed=1
ON CONFLICT(feed_id,publication_id) DO NOTHING;

-- A previous attempt may already have removed files; keep the saved record but
-- require collection to establish completeness again.
UPDATE publications SET complete=0
WHERE viewed=1 AND id IN (SELECT item FROM jobs WHERE kind='delete' AND status<>'done');

UPDATE feeds SET deleted=true, connection='needs_connection' WHERE connection='deleting';
UPDATE jobs SET status='cancelled', generation=generation+1, error=NULL, problem_code=NULL, source_event_owned=false
WHERE kind IN ('delete','feed-delete') AND status <> 'done';
UPDATE reader_problems SET finished=extract(epoch FROM clock_timestamp()), outcome='irrelevant'
WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete');
