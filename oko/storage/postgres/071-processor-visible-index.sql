ALTER TABLE jobs ADD COLUMN processor_visible boolean NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN processor_caption boolean NOT NULL DEFAULT false;
ALTER TABLE youtube_access ADD COLUMN processor_reservation_id text;
CREATE INDEX jobs_processor_code ON jobs(id) WHERE problem_code='processor-config';
UPDATE jobs j SET processor_visible=(
    j.status IN ('pending','running','failed','unavailable','blocked')
    OR (j.kind='fetch' AND j.status='done' AND j.error IS NOT NULL))
  AND (j.kind IN ('delete','feed-delete','discover','read') OR CASE WHEN j.kind='download_media' THEN coalesce(
    (SELECT p.viewed FROM media_files m JOIN publications p ON p.id=m.publication_id WHERE m.id=j.item),0)=0 ELSE coalesce(
    (SELECT p.viewed FROM publications p WHERE p.id=j.item),0)=0 END)
WHERE j.problem_code='processor-config';
UPDATE jobs j SET processor_caption=EXISTS (SELECT 1 FROM media_files m WHERE j.kind='download_media' AND m.id=j.item AND m.resource_key='youtube:subtitles') WHERE j.problem_code='processor-config';
CREATE INDEX jobs_processor_visible ON jobs(processor_caption,kind,item) WHERE processor_visible;
DROP INDEX jobs_unmanaged_problems;
CREATE INDEX jobs_unmanaged_problems ON jobs(kind,item)
WHERE kind IS DISTINCT FROM 'read' AND kind IS DISTINCT FROM 'discover' AND problem_code IS DISTINCT FROM 'processor-config'
  AND NOT (kind IN ('delete','feed-delete') AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete'))
  AND NOT (kind IS NOT DISTINCT FROM 'fetch' AND item IS NOT NULL AND item<>'' AND problem_code IS NOT NULL AND problem_code<>'' AND problem_code NOT LIKE 'source-%' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-fetch'))
  AND (problem_code IS NOT NULL OR error IS NOT NULL)
  AND NOT (kind IS NOT DISTINCT FROM 'download_media' AND item IS NOT NULL AND item<>'' AND problem_code IS NOT NULL AND problem_code<>'' AND problem_code NOT LIKE 'source-%' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-download_media'));
CREATE INDEX reader_processor_group_isolated ON reader_problems ((body::jsonb->>'processor_group_identity'),created,id COLLATE "C") WHERE created >= -62135596800 AND created < 253402300800 AND finished IS NULL AND body::jsonb->>'code'='processor-config' AND jsonb_typeof(body::jsonb->'title')='string' AND jsonb_typeof(body::jsonb->'consequence')='string' AND jsonb_typeof(body::jsonb->'next_step')='string' AND jsonb_typeof(body::jsonb->'operation')='string' AND body::jsonb->>'state' IN ('current','retrying') AND (body::jsonb->'next_at' IS NULL OR jsonb_typeof(body::jsonb->'next_at')='null') AND (NOT (body::jsonb ? 'reason') OR jsonb_typeof(body::jsonb->'reason')='string') AND (NOT (body::jsonb ? 'request_id') OR (jsonb_typeof(body::jsonb->'request_id')='string' AND (body::jsonb->>'request_id') COLLATE "C" ~ '^[A-Za-z0-9_-]{1,100}$')) AND jsonb_typeof(body::jsonb->'objects')='array' AND NOT (body::jsonb @? '$.objects[*] ? (@.type() != "object" || !exists(@.kind) || @.kind.type() != "string" || !exists(@.id) || @.id.type() != "string" || @.id == "" || (@.kind != "settings" && @.kind != "feed" && @.kind != "folder" && @.kind != "material"))') AND NOT (body::jsonb @? '$.objects[*].keyvalue() ? (@.key != "kind" && @.key != "id" && @.key != "label")');
