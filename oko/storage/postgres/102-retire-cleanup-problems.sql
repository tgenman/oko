-- Close a legacy cleanup problem only when every canonical or legacy identity
-- resolves to exactly one saved job. Colons in identifiers can make identities
-- collide, so include publication owners and linked-publication owners.
WITH matches AS (
    SELECT p.id, count(DISTINCT j.id) AS owners,
           bool_and(j.kind IN ('delete','feed-delete')) AS cleanup_only
    FROM reader_problems p JOIN jobs j ON EXISTS (
        SELECT 1 FROM (SELECT 1) seed
        LEFT JOIN publications item ON item.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(item.id,media_owner.id)
        LEFT JOIN publications root ON root.id=l.source_publication_id
        WHERE p.identity = 'job:' || (p.body::jsonb->>'code') || ':' ||
            coalesce(item.feed_id,media_owner.feed_id,root.feed_id,j.feed_id,j.item,'') || ':' || j.kind || ':' || coalesce(j.item,'')
    ) OR p.identity = 'job:' || (p.body::jsonb->>'code') || ':' || coalesce(j.feed_id,j.item,'') || ':' || j.kind || ':' || coalesce(j.item,'')
    WHERE p.finished IS NULL
      AND p.body::jsonb->>'code' IN ('delete','feed-delete','work-delete','work-feed-delete')
      AND (p.body::jsonb->>'operation' IS NULL OR p.body::jsonb->>'operation'='producer:status')
    GROUP BY p.id
)
UPDATE reader_problems p SET finished=extract(epoch FROM clock_timestamp()), outcome='irrelevant'
FROM matches m WHERE p.id=m.id AND m.owners=1 AND m.cleanup_only;
