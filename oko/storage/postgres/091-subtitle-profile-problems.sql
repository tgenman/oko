CREATE INDEX jobs_subtitle_profile_recovery ON jobs(id)
WHERE kind='download_media' AND status IN ('pending','running','failed','unavailable','blocked')
AND problem_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch');
CREATE INDEX reader_subtitle_profile_legacy ON reader_problems(id COLLATE "C")
WHERE finished IS NULL AND identity LIKE 'job:%'
AND body::jsonb->>'code' IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')
AND coalesce(body::jsonb->>'status_job_identity',body::jsonb->>'media_job_identity',identity) LIKE '%:download_media:%';
