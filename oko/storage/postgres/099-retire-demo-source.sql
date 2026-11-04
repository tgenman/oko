-- Keep the old feed identity and every publication.  A retired synthetic
-- source cannot be refreshed, but its contents remain readable.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM feeds WHERE id='demo' AND kind IS DISTINCT FROM 'demo')
       AND EXISTS (SELECT 1 FROM jobs WHERE kind='discover' AND (item='' OR item IS NULL)) THEN
        RAISE EXCEPTION 'ambiguous legacy discovery owner';
    END IF;
    IF EXISTS (SELECT 1 FROM jobs WHERE kind='discover' AND (item='' OR item IS NULL)
               AND feed_id IS NOT NULL AND feed_id <> 'demo') THEN
        RAISE EXCEPTION 'legacy discovery owner conflicts with saved attempt';
    END IF;
    IF (SELECT count(*) FROM jobs WHERE kind='discover' AND (item='' OR item IS NULL OR item='demo')) > 1 THEN
        RAISE EXCEPTION 'ambiguous legacy discovery jobs';
    END IF;
    IF EXISTS (SELECT 1 FROM feeds WHERE kind='demo' AND id <> 'demo') THEN
        RAISE EXCEPTION 'unexpected legacy demo feed identity';
    END IF;
    -- Old deletion could leave its discovery attempt without a feed. Preserve
    -- the attempt and diagnostics under the old opaque identity in that case.
    UPDATE jobs SET item='demo' WHERE kind='discover' AND (item='' OR item IS NULL);
    UPDATE feeds SET kind='unresolved', transport=NULL,
        connection=CASE WHEN connection='deleting' THEN 'deleting' ELSE 'needs_connection' END
      WHERE id='demo' AND kind='demo';
END $$;
