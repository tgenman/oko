-- Keep ambiguous historical jobs unchanged and stop the migration.
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM jobs WHERE kind IS NULL OR kind NOT IN
     ('discover','fetch','download_media','filter','theses','read','delete','feed-delete')
     OR generation IS NULL OR generation<0 OR attempt IS NULL OR attempt<0 OR retry_delay<0) THEN
  RAISE EXCEPTION 'invalid saved job operation or counters';
 END IF;
END $$;
ALTER TABLE jobs ALTER COLUMN kind SET NOT NULL;
ALTER TABLE jobs ALTER COLUMN generation SET NOT NULL;
ALTER TABLE jobs ALTER COLUMN attempt SET NOT NULL;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_allowed CHECK (kind IN
 ('discover','fetch','download_media','filter','theses','read','delete','feed-delete'));
ALTER TABLE jobs ADD CONSTRAINT jobs_generation_nonnegative CHECK (generation>=0);
ALTER TABLE jobs ADD CONSTRAINT jobs_attempt_nonnegative CHECK (attempt>=0);
ALTER TABLE jobs ADD CONSTRAINT jobs_retry_delay_nonnegative CHECK (retry_delay>=0);
