-- Old source diagnostic history is disposable. Current event-owned records,
-- Jobs, publications and command receipts retain their identities and contents.
WITH discarded AS (
DELETE FROM reader_problems
WHERE origin='background'
  AND body::jsonb->>'code' LIKE 'source-%'
  AND jsonb_typeof(body::jsonb->'source_group_identity') IS DISTINCT FROM 'string'
  AND (body::jsonb->>'operation' IN ('producer:fetch','producer:download_media','producer:filter','producer:theses','producer:delete','producer:feed-delete')
       OR (coalesce(body::jsonb->>'operation','producer:status')='producer:status'
           AND identity ~ '^job:source-.*:(fetch|download_media|filter|theses|delete|feed-delete)$'))
RETURNING id
)
INSERT INTO state(key,value)
SELECT 'reader-status-revision',jsonb_build_object('version',1,'changed',extract(epoch FROM clock_timestamp()))::text
WHERE EXISTS (SELECT 1 FROM discarded)
ON CONFLICT(key) DO UPDATE SET value=jsonb_build_object(
    'version',(state.value::jsonb->>'version')::bigint+1,
    'changed',extract(epoch FROM clock_timestamp()))::text;

DELETE FROM state WHERE key LIKE 'source-legacy-protected:%'
    OR key LIKE 'source-recovery-seen:%' OR key='source-problems-recovered-v1';
DROP INDEX source_legacy_protected;
-- Existing resumable recovery reprojects current failures on first startup.
UPDATE jobs SET source_event_owned=false
WHERE source_event_owned AND problem_code LIKE 'source-%'
  AND kind IN ('fetch','download_media','filter','theses','delete','feed-delete');
