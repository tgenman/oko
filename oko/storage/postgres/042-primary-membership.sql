-- Python owns primary membership under a transaction-scoped graph lock.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM feed_publications GROUP BY publication_id
               HAVING count(*) FILTER (WHERE is_primary) <> 1) THEN
        RAISE EXCEPTION 'invalid primary membership';
    END IF;
END $$;
DROP TRIGGER membership_primary_insert ON feed_publications;
DROP TRIGGER membership_primary_delete ON feed_publications;
DROP FUNCTION oko_membership_primary_insert();
DROP FUNCTION oko_membership_primary_delete();
