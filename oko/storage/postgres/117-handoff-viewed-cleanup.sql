-- Old releases kept forgotten Publication rows until their files were removed.
-- Transfer that work to independent Jobs before removing the rows.
LOCK TABLE publications, publication_links, media_files, jobs IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE ambiguous text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO ambiguous
 FROM publications p WHERE p.viewed IS NULL OR p.viewed NOT IN (0,1);
 IF ambiguous IS NOT NULL THEN
   RAISE EXCEPTION 'Ambiguous viewed state: %', ambiguous;
 END IF;
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO ambiguous
 FROM publications p WHERE p.viewed=1 AND (
   EXISTS (SELECT 1 FROM publication_links l WHERE l.source_publication_id=p.id OR l.target_publication_id=p.id)
   OR EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id));
 IF ambiguous IS NOT NULL THEN
   RAISE EXCEPTION 'Viewed publications still have links or media: %', ambiguous;
 END IF;
END $$;
INSERT INTO jobs(kind,item,status,payload)
 SELECT 'delete',p.id,'pending',CASE WHEN p.feed_id IS NULL THEN '{}'::jsonb
   ELSE jsonb_build_object('cleanup_feed',p.feed_id) END
 FROM publications p WHERE p.viewed=1
ON CONFLICT(kind,item) DO UPDATE SET
 status=CASE WHEN jobs.status IN ('done','cancelled') THEN 'pending' ELSE jobs.status END,
 generation=CASE WHEN jobs.status IN ('done','cancelled') THEN jobs.generation+1 ELSE jobs.generation END,
 payload=CASE WHEN jobs.payload ? 'cleanup_feed' THEN jobs.payload ELSE jobs.payload || excluded.payload END;
UPDATE jobs SET status='cancelled',generation=generation+1
 WHERE kind NOT IN ('delete','discover','read','feed-delete','download_media')
 AND status<>'cancelled' AND item IN (SELECT id FROM publications WHERE viewed=1);
DELETE FROM publications WHERE viewed=1;
