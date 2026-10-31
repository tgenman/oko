-- Preserve missing and explicitly empty descriptions, and reject conflicting copies.
DO $body$
BEGIN
    IF EXISTS (SELECT 1 FROM feed_details d LEFT JOIN feeds f ON f.id=d.feed WHERE f.id IS NULL)
       OR EXISTS (
           SELECT 1 FROM feeds f LEFT JOIN feed_details d ON d.feed=f.id
           WHERE (f.description <> '' AND d.description IS DISTINCT FROM f.description)
              OR (f.created_at IS NOT NULL AND d.created IS DISTINCT FROM f.created_at)
       )
       OR EXISTS (
           SELECT 1 FROM feed_details d WHERE
               NOT (d.created IS NULL OR (d.created > '-Infinity'::float8 AND d.created < 'Infinity'::float8)) OR
               NOT (d.modified IS NULL OR (d.modified > '-Infinity'::float8 AND d.modified < 'Infinity'::float8)) OR
               NOT (d.updated IS NULL OR (d.updated > '-Infinity'::float8 AND d.updated < 'Infinity'::float8)) OR
               NOT (d.marked_read IS NULL OR (d.marked_read > '-Infinity'::float8 AND d.marked_read < 'Infinity'::float8))
       ) THEN
        RAISE EXCEPTION 'conflicting feed details';
    END IF;
END
$body$;
ALTER TABLE feeds ALTER COLUMN description DROP NOT NULL;
ALTER TABLE feeds ALTER COLUMN description DROP DEFAULT;
ALTER TABLE feeds ADD COLUMN modified_at double precision;
ALTER TABLE feeds ADD COLUMN last_discovery_at double precision;
ALTER TABLE feeds ADD COLUMN last_viewed_at double precision;
UPDATE feeds f SET description=d.description, created_at=coalesce(d.created,f.created_at),
    modified_at=d.modified, last_discovery_at=d.updated, last_viewed_at=d.marked_read
FROM feed_details d WHERE d.feed=f.id;
UPDATE feeds f SET description=NULL WHERE f.description='' AND NOT EXISTS (SELECT 1 FROM feed_details d WHERE d.feed=f.id);
DROP TABLE feed_details;
