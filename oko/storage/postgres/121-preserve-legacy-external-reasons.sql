-- The historical explanation is retained in Job payload, not presented as an
-- error of the successful fetch. Refuse inconsistent or pre-existing markers.
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM jobs WHERE kind='fetch' AND payload ? 'historical_external_media_reason'
    AND (status<>'done' OR reason_message IS DISTINCT FROM payload->>'historical_external_media_reason' OR reason_code IS NOT NULL)) THEN
  RAISE EXCEPTION 'Conflicting historical external-media reasons';
 END IF;
END $$;
UPDATE jobs SET reason_message=NULL WHERE kind='fetch' AND payload ? 'historical_external_media_reason';
