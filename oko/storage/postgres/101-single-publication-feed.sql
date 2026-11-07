-- Do not silently choose among conflicting publication owners.
DO $$
DECLARE conflict record;
BEGIN
    SELECT publication_id, array_agg(feed_id ORDER BY feed_id) AS feeds INTO conflict
    FROM feed_publications GROUP BY publication_id HAVING count(*) > 1
    ORDER BY publication_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'publication % has multiple feeds: %', conflict.publication_id, conflict.feeds;
    END IF;
END $$;

ALTER TABLE publications ADD COLUMN feed_id text REFERENCES feeds(id) ON DELETE RESTRICT;
UPDATE publications p SET feed_id=m.feed_id FROM feed_publications m WHERE m.publication_id=p.id;
CREATE INDEX publications_feed ON publications(feed_id, published_at DESC, id) WHERE feed_id IS NOT NULL;
DROP TABLE feed_publications;
