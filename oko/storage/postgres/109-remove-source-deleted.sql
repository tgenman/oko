-- A historical source-deletion marker becomes an unavailable fetch outcome.
-- Conflicting active/successful attempts require manual reconciliation.
LOCK TABLE publications, jobs IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO conflicts
 FROM publications p JOIN jobs j ON j.kind='fetch' AND j.item=p.id
 WHERE p.source_deleted AND p.viewed=0 AND (j.status NOT IN ('unavailable','failed')
   OR j.reason_code IS NOT NULL AND j.reason_code NOT IN ('source-unavailable','source_unavailable')
   OR j.reason_code IS NULL AND j.reason_message IS NOT NULL AND j.reason_message NOT IN
     ('Публикация удалена или недоступна в источнике','Публикация недоступна в источнике'));
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Conflicting source deletion and fetch Job: %', conflicts; END IF;
END $$;
INSERT INTO jobs(kind,item,status,reason_code,reason_message)
SELECT 'fetch',id,'unavailable','source-unavailable','Публикация недоступна в источнике'
FROM publications WHERE source_deleted AND viewed=0
ON CONFLICT(kind,item) DO UPDATE SET status='unavailable',
 reason_code=COALESCE(jobs.reason_code,excluded.reason_code),
 reason_message=COALESCE(jobs.reason_message,excluded.reason_message);
ALTER TABLE publications DROP COLUMN source_deleted;
UPDATE publications SET content=content #- '{extra,_reader,changed}'
WHERE content #> '{extra,_reader,changed}' IS NOT NULL;
