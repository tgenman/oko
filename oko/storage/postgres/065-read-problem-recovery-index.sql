CREATE INDEX jobs_read_problem_recovery ON jobs(id) WHERE kind='read' AND (error IS NOT NULL OR problem_code IS NOT NULL);
CREATE INDEX reader_read_problem_recovery ON reader_problems(id)
WHERE finished IS NULL AND identity LIKE 'job:%' AND coalesce(body::jsonb->>'code','')!='processor-config'
  AND (body::jsonb->>'operation'='producer:read' OR ((body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status')
    AND (identity LIKE '%:read' OR identity LIKE '%:read:%')));
ALTER TABLE telegram_bindings ADD COLUMN indexed_role TEXT;
UPDATE telegram_bindings SET indexed_role=CASE WHEN pg_input_is_valid(binding,'jsonb') THEN binding::jsonb->>'role' END;
CREATE INDEX telegram_bindings_role_feed ON telegram_bindings (indexed_role,feed);
CREATE INDEX reader_read_problem_objects_gin ON reader_problems USING gin ((body::jsonb->'objects') jsonb_path_ops) WHERE finished IS NULL AND body::jsonb->>'operation'='producer:read';
CREATE INDEX jobs_unmanaged_problems ON jobs(kind,item)
WHERE (kind IS DISTINCT FROM 'read' OR problem_code='processor-config') AND (problem_code IS NOT NULL OR error IS NOT NULL);
