-- Content existence and Publication.status carry the facts formerly duplicated
-- by complete. Dropping the column also removes its dependent partial indexes.
ALTER TABLE publications DROP COLUMN complete;
CREATE INDEX publications_incomplete_recovery ON publications(id)
  WHERE viewed=0 AND content <> '{}'::jsonb AND NOT source_deleted AND incomplete_class IS NULL;
CREATE INDEX publications_incomplete_candidates ON publications(id)
  WHERE viewed=0 AND content <> '{}'::jsonb AND NOT source_deleted AND incomplete_class IS DISTINCT FROM 1;
CREATE INDEX publications_incomplete_group_recovery ON publications(id)
  WHERE viewed=0 AND content <> '{}'::jsonb AND NOT source_deleted AND incomplete_class IS DISTINCT FROM 1 AND NOT incomplete_group_ready;
-- The old grouping was derived from reader flags. Keep its visible history,
-- but rebuild current groups from Publication status and Job/MediaFile facts.
DO $$
DECLARE ambiguous text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO ambiguous FROM publications
 WHERE incomplete_group_unknown OR incomplete_group_conflict;
 IF ambiguous IS NOT NULL THEN
  RAISE EXCEPTION 'Ambiguous incomplete diagnostics require reconciliation: %', ambiguous;
 END IF;
END $$;
UPDATE reader_problems SET finished=extract(epoch FROM clock_timestamp()),outcome='irrelevant',
  incomplete_due=NULL,incomplete_future=NULL,incomplete_closes=false
WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete';
DELETE FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete-reservation'
  AND body::jsonb->>'reservation'='true';
DELETE FROM state WHERE key LIKE 'incomplete:reservation:%' OR key LIKE 'incomplete:reservation-group:%';
UPDATE publications SET incomplete_class=NULL,incomplete_group_key=NULL,incomplete_group_owner=NULL,
  incomplete_group_ready=false,incomplete_group_unknown=false,incomplete_group_conflict=false;
DELETE FROM state WHERE key IN ('incomplete-class-recovered-v1','experimental-incomplete-groups-v1','experimental-incomplete-problems-v1');
