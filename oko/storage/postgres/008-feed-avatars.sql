-- Move bounded avatar bytes into their owning feed; reject ambiguous old data.
DO $body$
BEGIN
    IF EXISTS (
        SELECT 1 FROM telegram_avatars a LEFT JOIN feeds f ON f.id = a.feed
        WHERE f.id IS NULL OR octet_length(a.image) > 131072
           OR a.digest <> encode(sha256(a.image), 'hex')
           OR NOT (
               (a.mime = 'image/jpeg' AND substring(a.image FROM 1 FOR 3) = decode('ffd8ff','hex')) OR
               (a.mime = 'image/png' AND substring(a.image FROM 1 FOR 8) = decode('89504e470d0a1a0a','hex')) OR
               (a.mime = 'image/webp' AND substring(a.image FROM 1 FOR 4) = decode('52494646','hex')
                                      AND substring(a.image FROM 9 FOR 4) = decode('57454250','hex'))
           )
    ) THEN
        RAISE EXCEPTION 'invalid stored avatar';
    END IF;
END
$body$;
ALTER TABLE feeds ADD COLUMN avatar_image bytea;
ALTER TABLE feeds ADD COLUMN avatar_mime text;
ALTER TABLE feeds ADD COLUMN avatar_digest text;
ALTER TABLE feeds ADD CONSTRAINT feeds_avatar_complete CHECK (
    (avatar_image IS NULL AND avatar_mime IS NULL AND avatar_digest IS NULL) OR
    (avatar_image IS NOT NULL AND avatar_mime IS NOT NULL AND avatar_digest IS NOT NULL AND octet_length(avatar_image) <= 131072)
);
UPDATE feeds f SET avatar_image = a.image, avatar_mime = a.mime, avatar_digest = a.digest
FROM telegram_avatars a WHERE a.feed = f.id;
DROP TABLE telegram_avatars;
DROP FUNCTION oko_trigger_reader_v_telegram_avatars_delete();
DROP FUNCTION oko_trigger_reader_v_telegram_avatars_insert();
DROP FUNCTION oko_trigger_reader_v_telegram_avatars_update();
