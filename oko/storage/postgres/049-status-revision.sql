DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM state WHERE key='reader-status-revision') THEN
  RAISE EXCEPTION 'status revision key already exists';
 END IF;
END $$;
INSERT INTO state(key,value)
SELECT 'reader-status-revision',jsonb_build_object('version',version,'changed',changed)::text
FROM reader_entities WHERE kind='status' AND identity='runtime';
INSERT INTO state(key,value)
SELECT 'reader-status-revision',jsonb_build_object('version',1,'changed',extract(epoch FROM clock_timestamp()))::text
WHERE NOT EXISTS (SELECT 1 FROM state WHERE key='reader-status-revision');
DROP TABLE reader_entities;
