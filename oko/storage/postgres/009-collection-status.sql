-- Move collection diagnostics to their owning job and feed without loss.
DO $body$
BEGIN
    BEGIN
        PERFORM data::jsonb FROM collection_attempts;
        PERFORM data::jsonb FROM collection_results;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid collection status JSON';
    END;
    IF EXISTS (
        SELECT 1 FROM collection_attempts a
        LEFT JOIN jobs j ON j.id = a.job LEFT JOIN feeds f ON f.id = a.feed
        WHERE j.id IS NULL OR f.id IS NULL OR j.kind NOT IN ('discover','fetch','download_media')
           OR jsonb_typeof(a.data::jsonb) <> 'object'
    ) OR EXISTS (
        SELECT 1 FROM collection_results r LEFT JOIN feeds f ON f.id = r.feed
        WHERE f.id IS NULL OR jsonb_typeof(r.data::jsonb) <> 'object'
    ) THEN
        RAISE EXCEPTION 'invalid collection status';
    END IF;
END
$body$;
ALTER TABLE jobs ADD COLUMN feed_id text;
ALTER TABLE jobs ADD COLUMN collection_attempt jsonb;
ALTER TABLE jobs ADD CONSTRAINT jobs_collection_attempt_complete CHECK (
    collection_attempt IS NULL OR
    (feed_id IS NOT NULL AND jsonb_typeof(collection_attempt) = 'object')
);
CREATE INDEX jobs_collection_attempts_by_feed ON jobs(feed_id) WHERE collection_attempt IS NOT NULL;
ALTER TABLE feeds ADD COLUMN last_collection_result jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(last_collection_result) = 'object');
UPDATE jobs j SET feed_id = a.feed, collection_attempt = a.data::jsonb
FROM collection_attempts a WHERE a.job = j.id;
UPDATE feeds f SET last_collection_result = r.data::jsonb
FROM collection_results r WHERE r.feed = f.id;
DROP TRIGGER collection_feed_deleted ON feeds;
DROP TRIGGER collection_job_deleted ON jobs;
DROP FUNCTION oko_trigger_collection_feed_deleted();
DROP FUNCTION oko_trigger_collection_job_deleted();
DROP TABLE collection_attempts;
DROP TABLE collection_results;
