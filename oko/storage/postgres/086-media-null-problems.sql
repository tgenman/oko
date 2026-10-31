ALTER TABLE jobs ADD COLUMN media_null_owned boolean NOT NULL DEFAULT false;
CREATE INDEX jobs_media_null_recovery ON jobs(id) WHERE kind='download_media' AND item IS NOT NULL AND item<>'' AND problem_code IS NULL AND error IS NOT NULL;
CREATE INDEX reader_media_null_legacy_recovery ON reader_problems(id COLLATE "C") WHERE finished IS NULL AND identity LIKE 'job:%' AND body::jsonb->>'code'='work-download_media' AND body::jsonb->>'operation' IN ('producer:status','producer:download_media');
