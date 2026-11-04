-- Reject ambiguous ownership before changing any data. Migrations execute in a
-- single release transaction, so an exception leaves the existing schema intact.
LOCK TABLE publications, feed_publications, feeds IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
    SELECT string_agg(format('publication=%s platform=%s external_id=%s feeds=%s',
                             p.id,p.platform,p.external_id,owners.feeds), E'\n' ORDER BY p.id)
      INTO conflicts
      FROM publications p
      JOIN (SELECT publication_id,string_agg(feed_id, ', ' ORDER BY feed_id) AS feeds
            FROM feed_publications GROUP BY publication_id HAVING count(*) > 1) owners
        ON owners.publication_id=p.id;
    IF conflicts IS NOT NULL THEN
        RAISE EXCEPTION 'Publication feed ownership conflicts:%', E'\n' || conflicts;
    END IF;
END $$;
ALTER TABLE publications ADD COLUMN feed_id text REFERENCES feeds(id);
UPDATE publications p SET feed_id=m.feed_id FROM feed_publications m WHERE m.publication_id=p.id;
CREATE INDEX publications_feed ON publications(feed_id,id) WHERE feed_id IS NOT NULL;
DROP TABLE feed_publications;
