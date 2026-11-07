-- Retain surviving cleanup targets; already removed publications cannot be restored.
LOCK TABLE feeds,publications,jobs IN ACCESS EXCLUSIVE MODE;
ALTER TABLE feeds ADD COLUMN deleted boolean NOT NULL DEFAULT false;
ALTER TABLE publications ADD COLUMN viewed boolean NOT NULL DEFAULT false;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO conflicts
 FROM publications p JOIN jobs j ON j.kind='delete' AND j.item=p.id
 JOIN feeds f ON f.id=j.payload->>'cleanup_feed'
 WHERE j.status<>'done' AND p.feed_id IS NOT NULL AND p.feed_id<>f.id;
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Conflicting cleanup publication owners: %', conflicts; END IF;
END $$;
UPDATE publications p SET feed_id=f.id,viewed=true,status='pending_collection'
FROM jobs j JOIN feeds f ON f.id=j.payload->>'cleanup_feed'
WHERE j.kind='delete' AND j.item=p.id AND j.status<>'done';
UPDATE feeds SET deleted=true,connection='needs_connection' WHERE connection='deleting';
UPDATE jobs SET status='cancelled',generation=generation+1,reason_code=NULL,reason_message=NULL,source_event_owned=false
WHERE kind IN ('delete','feed-delete') AND status<>'done';
UPDATE reader_problems SET finished=extract(epoch FROM clock_timestamp()),outcome='irrelevant'
WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete');
