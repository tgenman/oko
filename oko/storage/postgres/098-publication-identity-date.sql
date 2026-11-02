-- Refuse ambiguous legacy rows instead of silently changing their meaning.
DO $$
DECLARE
    target_schema pg_catalog.text := pg_catalog.current_schema();
    original_path pg_catalog.text := pg_catalog.current_setting('search_path');
BEGIN
    IF target_schema IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=target_schema AND c.relname='publications' AND c.relkind IN ('r','p')
    ) THEN
        RAISE EXCEPTION 'Publication migration requires publications in current schema';
    END IF;
    EXECUTE pg_catalog.format('LOCK TABLE %I.publications IN ACCESS EXCLUSIVE MODE',target_schema);
    PERFORM pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,pg_temp',target_schema),true);
    IF EXISTS (SELECT 1 FROM publications WHERE platform IS NULL OR external_id IS NULL OR discovered_at IS NULL) THEN
        RAISE EXCEPTION 'Publication migration: missing external identity or discovery time';
    END IF;
    IF EXISTS (SELECT 1 FROM publications WHERE platform NOT IN ('telegram','youtube','telegraph','instagram','twitter','other_web','rss') OR external_id = '') THEN
        RAISE EXCEPTION 'Publication migration: invalid external identity';
    END IF;
    IF EXISTS (SELECT 1 FROM publications WHERE published_known IS NULL OR published_known NOT IN (0,1)) THEN
        RAISE EXCEPTION 'Publication migration: invalid publication date flag';
    END IF;
    IF EXISTS (SELECT 1 FROM publications WHERE (published IS NOT NULL AND (published < -62135596800 OR published >= 253402300800 OR published IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8))) OR (published_at IS NOT NULL AND (published_at < -62135596800 OR published_at >= 253402300800 OR published_at IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8))) OR discovered_at < -62135596800 OR discovered_at >= 253402300800 OR discovered_at IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)) THEN
        RAISE EXCEPTION 'Publication migration: invalid timestamp';
    END IF;
    IF EXISTS (SELECT 1 FROM publications WHERE (published_known <> 0 AND (published IS NULL OR (published_at IS NOT NULL AND published_at IS DISTINCT FROM published))) OR (published_known = 0 AND published_at IS NOT NULL)) THEN
        RAISE EXCEPTION 'Publication migration: conflicting publication dates';
    END IF;
    IF EXISTS (SELECT 1 FROM publications GROUP BY platform, external_id HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'Publication migration: duplicate external identity';
    END IF;
UPDATE publications SET published_at=published WHERE published_known <> 0 AND published_at IS NULL;
ALTER TABLE publications ALTER COLUMN discovered_at SET DEFAULT extract(epoch FROM clock_timestamp());
ALTER TABLE publications ALTER COLUMN platform SET NOT NULL, ALTER COLUMN external_id SET NOT NULL, ALTER COLUMN discovered_at SET NOT NULL;
ALTER TABLE publications DROP COLUMN source_id, DROP COLUMN published, DROP COLUMN published_known;
CREATE INDEX publications_unviewed_order ON publications(published_at DESC, id) WHERE viewed=0;
    PERFORM pg_catalog.set_config('search_path',original_path,true);
END $$;
