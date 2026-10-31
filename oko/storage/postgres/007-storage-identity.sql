-- Move the existing reader identity without changing the library timeline.
DO $body$
BEGIN
    BEGIN
        PERFORM value::uuid FROM reader_identity;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid reader identity';
    END;
    IF EXISTS (SELECT 1 FROM state WHERE key = 'storage-id') AND NOT EXISTS (
        SELECT 1 FROM state s JOIN reader_identity r ON r.id = 1
        WHERE s.key = 'storage-id' AND s.value = to_json(r.value)::text
    ) THEN
        RAISE EXCEPTION 'existing storage-id conflicts with reader identity';
    END IF;
END
$body$;
INSERT INTO state(key, value)
    SELECT 'storage-id', to_json(value)::text FROM reader_identity WHERE id = 1
    ON CONFLICT (key) DO NOTHING;
DROP TABLE reader_identity;
