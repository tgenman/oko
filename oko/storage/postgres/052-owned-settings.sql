DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM settings s WHERE
   s.scope NOT IN ('oko','folder','feed') OR
   (s.scope='oko' AND s.owner<>'global') OR
   (s.scope='folder' AND NOT EXISTS (SELECT 1 FROM folders f WHERE f.id=s.owner)) OR
   (s.scope='feed' AND NOT EXISTS (SELECT 1 FROM feeds f WHERE f.id=s.owner)) OR
   s.field='' OR s.value::jsonb='null'::jsonb) THEN
  RAISE EXCEPTION 'invalid settings owner or value';
 END IF;
END $$;
CREATE TABLE settings_new (
 folder_id text REFERENCES folders(id) ON DELETE RESTRICT,
 feed_id text REFERENCES feeds(id) ON DELETE RESTRICT,
 field text NOT NULL CHECK (field<>''),
 value jsonb NOT NULL CHECK (value<>'null'::jsonb),
 CHECK (folder_id IS NULL OR feed_id IS NULL),
 CONSTRAINT settings_one_value UNIQUE NULLS NOT DISTINCT (folder_id,feed_id,field)
);
INSERT INTO settings_new(folder_id,feed_id,field,value)
SELECT CASE WHEN scope='folder' THEN owner END,CASE WHEN scope='feed' THEN owner END,field,value::jsonb FROM settings;
DROP TABLE settings;
ALTER TABLE settings_new RENAME TO settings;
