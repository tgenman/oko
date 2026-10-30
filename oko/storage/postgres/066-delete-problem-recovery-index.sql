CREATE INDEX jobs_delete_problem_recovery ON jobs(id)
WHERE kind IN ('delete','feed-delete') AND (error IS NOT NULL OR problem_code IS NOT NULL)
  AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete');
DROP INDEX jobs_unmanaged_problems;
CREATE INDEX jobs_unmanaged_problems ON jobs(kind,item)
WHERE (kind IS DISTINCT FROM 'read' OR problem_code='processor-config')
  AND NOT (kind IN ('delete','feed-delete') AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete'))
  AND (problem_code IS NOT NULL OR error IS NOT NULL);
CREATE INDEX reader_delete_problem_identity ON reader_problems ((body::jsonb->>'deletion_job_identity'))
WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete');
CREATE INDEX reader_delete_problem_recovery ON reader_problems(id)
WHERE finished IS NULL AND identity LIKE 'job:%'
  AND body::jsonb->>'code' IN ('delete','feed-delete','work-delete','work-feed-delete')
  AND (body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete') OR body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status');
CREATE INDEX jobs_running_delete_problem ON jobs(id)
WHERE kind IN ('delete','feed-delete') AND status='running' AND (error IS NOT NULL OR problem_code IS NOT NULL)
  AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete');
