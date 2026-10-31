ALTER TABLE jobs ADD COLUMN deletion_extra_owned boolean NOT NULL DEFAULT false;
CREATE INDEX jobs_deletion_extra_recovery ON jobs(id) WHERE kind IN ('delete','feed-delete') AND (error IS NOT NULL OR problem_code IS NOT NULL);
