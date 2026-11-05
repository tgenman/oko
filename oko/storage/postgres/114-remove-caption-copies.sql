-- Caption state belongs to the MediaFile and its download Job. Reject any
-- legacy copy which those rows cannot represent before removing it.
LOCK TABLE publications, media_files, jobs IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO conflicts
 FROM publications p
 WHERE (p.content #> '{extra,youtube,captions}' IS NOT NULL OR p.content #> '{extra,youtube,captions_digest}' IS NOT NULL)
   AND (
     p.platform <> 'youtube'
     OR (p.content #> '{extra,youtube,captions}' IS NOT NULL AND
         (jsonb_typeof(p.content #> '{extra,youtube,captions}') <> 'string' OR
          p.content #>> '{extra,youtube,captions}' NOT IN ('available','unavailable','not_requested','pending','running','error')))
     OR (p.content #> '{extra,youtube,captions_digest}' IS NOT NULL AND
         (jsonb_typeof(p.content #> '{extra,youtube,captions_digest}') <> 'string'
          OR p.content #>> '{extra,youtube,captions_digest}' !~ '^[0-9a-f]{64}$'
          OR p.content #>> '{extra,youtube,captions}' IS DISTINCT FROM 'available'))
     OR NOT EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles')
     OR (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array'
       THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='media' AND b->>'role'='subtitles')<>1
     OR (SELECT count(*) FROM media_files m
       JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array'
         THEN p.content->'blocks' ELSE '[]'::jsonb END) b
         ON b->>'type'='media' AND b->>'role'='subtitles' AND b->>'media_id'=m.id
       WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles')<>1
     OR ((p.content #>> '{extra,youtube,captions}')='available' AND NOT EXISTS (
       SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles' AND m.status='ready' AND m.storage_key IS NOT NULL))
     OR ((p.content #>> '{extra,youtube,captions}')='unavailable' AND NOT EXISTS (
       SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
       WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles' AND m.status='unavailable'
         AND j.status='done' AND j.reason_code='subtitles_absent'))
     OR ((p.content #>> '{extra,youtube,captions}')='not_requested' AND NOT EXISTS (
       SELECT 1 FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id
       WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles' AND m.status='pending_download'
         AND (j.id IS NULL OR j.status='cancelled')))
     OR ((p.content #>> '{extra,youtube,captions}') IN ('pending','running') AND NOT EXISTS (
       SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
       WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles' AND m.status='pending_download'
         AND j.status IN ('pending','running','blocked')))
     OR ((p.content #>> '{extra,youtube,captions}')='error' AND NOT EXISTS (
       SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
       WHERE m.publication_id=p.id AND m.resource_key='youtube:subtitles' AND m.status IN ('pending_download','unavailable')
         AND j.status IN ('failed','unavailable')))
   );
 IF conflicts IS NOT NULL THEN
  RAISE EXCEPTION 'Caption state conflicts with MediaFile or Job: %', conflicts;
 END IF;
END $$;
UPDATE publications
 SET content=jsonb_set(content,'{extra,youtube}',(content #> '{extra,youtube}') - 'captions' - 'captions_digest')
 WHERE content #> '{extra,youtube,captions}' IS NOT NULL OR content #> '{extra,youtube,captions_digest}' IS NOT NULL;
