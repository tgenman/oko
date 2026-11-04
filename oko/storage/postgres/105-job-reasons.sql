-- Introduce the Job outcome fields before moving the readers and writers.
-- Hold source rows stable from the first preflight through removal of the old
-- columns, including writers that do not participate in application leases.
LOCK TABLE jobs, media_files, publications IN ACCESS EXCLUSIVE MODE;
ALTER TABLE jobs RENAME COLUMN problem_code TO reason_code;
ALTER TABLE jobs RENAME COLUMN error TO reason_message;
-- A single outcome cannot preserve two different historical explanations.
-- Stop atomically and identify every affected media row for reconciliation.
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(m.id, ', ' ORDER BY m.id) INTO conflicts
 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
 WHERE m.reason<>'' AND m.reason<>'Субтитры не запрошены'
   AND j.reason_message IS NOT NULL AND j.reason_message<>'' AND j.reason_message<>m.reason;
 IF conflicts IS NOT NULL THEN
  RAISE EXCEPTION 'Conflicting media and job reasons: %', conflicts;
 END IF;
END $$;
-- Older fetch jobs could be marked done while retaining a failure.
UPDATE jobs SET status='failed' WHERE kind='fetch' AND status='done'
  AND (reason_message IS NOT NULL OR reason_code IS NOT NULL);
UPDATE jobs j SET reason_code = 'subtitles_absent', reason_message = COALESCE(j.reason_message,NULLIF(m.reason,''))
FROM media_files m JOIN publications p ON p.id=m.publication_id
WHERE j.kind = 'download_media' AND j.item = m.id AND j.status = 'done'
  AND m.resource_key = 'youtube:subtitles' AND m.status = 'unavailable'
  AND p.content #>> '{extra,youtube,captions}'='unavailable'
  AND (j.reason_code='subtitles_absent' OR (j.reason_code IS NULL AND j.reason_message IS NULL));
-- Some older cleanups removed completed download jobs. The saved caption state
-- distinguishes confirmed absence from an unrequested or failed download.
INSERT INTO jobs(kind,item,status,reason_code,reason_message)
SELECT 'download_media',m.id,'done','subtitles_absent',NULLIF(m.reason,'')
FROM media_files m JOIN publications p ON p.id=m.publication_id
WHERE m.resource_key='youtube:subtitles' AND m.status='unavailable'
  AND p.content #>> '{extra,youtube,captions}'='unavailable'
ON CONFLICT (kind,item) DO NOTHING;
-- Previously an unrequested caption was stored as unavailable. Only change
-- rows without a job: a saved attempt must be reconciled rather than erased.
UPDATE media_files m SET status='pending_download',reason=''
FROM publications p
WHERE p.id=m.publication_id AND m.resource_key='youtube:subtitles'
  AND m.status='unavailable' AND m.reason='Субтитры не запрошены'
  AND p.content #>> '{extra,youtube,captions}'='not_requested'
  AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind='download_media' AND j.item=m.id);
-- An older retry could leave the previous caption outcome in content after
-- the file and its Job had already moved back to the download queue.
UPDATE publications p SET content=jsonb_set(p.content,'{extra,youtube,captions}','"pending"'::jsonb)
WHERE p.content #>> '{extra,youtube,captions}' IN ('unavailable','error','not_requested')
  AND EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
    WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles'
      AND m.status='pending_download' AND j.status IN ('pending','running','blocked'));
UPDATE publications p SET content=jsonb_set(p.content,'{extra,youtube,captions}','"unavailable"'::jsonb)
WHERE p.content #>> '{extra,youtube,captions}' IN ('error','pending')
  AND EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
    WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles'
      AND m.status='unavailable' AND j.status='done' AND j.reason_code='subtitles_absent');
-- Older media rows can carry the only surviving explanation of an unavailable
-- file. Preserve it in a durable Job without guessing a more specific code.
INSERT INTO jobs(kind,item,status,reason_message)
SELECT 'download_media',m.id,'unavailable',m.reason
FROM media_files m
WHERE m.status='unavailable' AND m.reason<>''
  AND m.reason<>'Субтитры не запрошены'
ON CONFLICT (kind,item) DO NOTHING;
UPDATE jobs j SET reason_message=m.reason
FROM media_files m
WHERE j.kind='download_media' AND j.item=m.id AND j.status<>'done'
  AND j.reason_message IS NULL AND m.reason<>''
  AND m.reason<>'Субтитры не запрошены';
DO $$
DECLARE ambiguous text;
BEGIN
 SELECT string_agg(m.id, ', ' ORDER BY m.id) INTO ambiguous FROM media_files m
 WHERE m.status='ready' AND m.reason<>'' AND NOT EXISTS
   (SELECT 1 FROM jobs j WHERE j.kind='download_media' AND j.item=m.id);
 IF ambiguous IS NOT NULL THEN
  RAISE EXCEPTION 'Ready media with unknown prior outcome: %', ambiguous;
 END IF;
END $$;
-- A ready file without a saved attempt predates durable download Jobs.
INSERT INTO jobs(kind,item,status)
SELECT 'download_media',m.id,'done' FROM media_files m WHERE m.status='ready'
ON CONFLICT (kind,item) DO NOTHING;
-- Preserve a pending file's last attempt even when its old Job was removed.
INSERT INTO jobs(kind,item,status,reason_message)
SELECT 'download_media',m.id,'pending',m.reason
FROM media_files m WHERE m.status='pending_download' AND m.reason<>''
  AND m.reason<>'Субтитры не запрошены'
ON CONFLICT (kind,item) DO NOTHING;
DO $$
DECLARE lost text;
BEGIN
 SELECT string_agg(m.id, ', ' ORDER BY m.id) INTO lost
 FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id
 WHERE m.reason<>'' AND (
   (m.reason='Субтитры не запрошены' AND (m.status<>'pending_download' OR j.id IS NOT NULL)) OR
   (m.reason<>'Субтитры не запрошены' AND j.reason_message IS DISTINCT FROM m.reason));
 IF lost IS NOT NULL THEN
  RAISE EXCEPTION 'Cannot preserve media reasons: %', lost;
 END IF;
 SELECT string_agg(m.id, ', ' ORDER BY m.id) INTO lost
 FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id
 WHERE (m.status='unavailable' AND (j.id IS NULL OR j.status NOT IN ('unavailable','done')
        OR (j.status='unavailable' AND NULLIF(j.reason_code,'') IS NULL AND NULLIF(j.reason_message,'') IS NULL)
        OR (j.status='done' AND (j.reason_code IS DISTINCT FROM 'subtitles_absent' OR m.resource_key<>'youtube:subtitles'))))
    OR (j.status='done' AND j.reason_code='subtitles_absent' AND (m.status<>'unavailable' OR m.resource_key<>'youtube:subtitles'))
    OR (m.status='pending_download' AND j.status IN ('done','unavailable'));
 IF lost IS NOT NULL THEN
  RAISE EXCEPTION 'Ambiguous media and job states: %', lost;
 END IF;
END $$;
ALTER TABLE media_files DROP COLUMN reason;
CREATE INDEX jobs_fetch_without_reason ON jobs(id)
  WHERE kind='fetch' AND status IN ('failed','unavailable') AND reason_code IS NULL AND reason_message IS NULL;
CREATE INDEX jobs_media_without_reason ON jobs(id)
  WHERE kind='download_media' AND status IN ('failed','unavailable') AND reason_code IS NULL AND reason_message IS NULL;
CREATE INDEX jobs_processing_without_reason ON jobs(kind,id)
  WHERE kind IN ('filter','theses') AND status IN ('failed','unavailable') AND reason_code IS NULL AND reason_message IS NULL;
DO $$
DECLARE ambiguous text;
BEGIN
 SELECT string_agg(kind||':'||item, ', ' ORDER BY kind,item) INTO ambiguous FROM jobs
 WHERE status='done' AND (reason_code IS NOT NULL OR reason_message IS NOT NULL)
   AND NOT (kind='download_media' AND reason_code IS NOT DISTINCT FROM 'subtitles_absent');
 IF ambiguous IS NOT NULL THEN
  RAISE EXCEPTION 'Completed jobs with ambiguous errors: %', ambiguous;
 END IF;
END $$;
