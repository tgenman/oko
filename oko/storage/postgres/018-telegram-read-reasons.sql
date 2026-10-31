DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM telegram_read_members WHERE eligible IS NULL OR eligible NOT IN (0,1)
     OR (eligible=1 AND reason IS DISTINCT FROM 'saved' AND reason IS DISTINCT FROM 'viewed')) THEN
  RAISE EXCEPTION 'invalid Telegram read eligibility reason';
 END IF;
END $$;
ALTER TABLE telegram_read_members ADD COLUMN saved boolean NOT NULL DEFAULT false;
ALTER TABLE telegram_read_members ADD COLUMN viewed boolean NOT NULL DEFAULT false;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_telegram_read_members_status_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.saved IS DISTINCT FROM OLD.saved OR NEW.viewed IS DISTINCT FROM OLD.viewed OR NEW.missing IS DISTINCT FROM OLD.missing THEN
  INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 END IF;
 RETURN NULL;
END $$;
UPDATE telegram_read_members SET saved=(eligible=1 AND reason='saved'),viewed=(eligible=1 AND reason='viewed')
 WHERE eligible=1;
ALTER TABLE telegram_read_members DROP COLUMN eligible, DROP COLUMN reason;
CREATE INDEX telegram_read_candidates ON telegram_read_members(feed,member) WHERE missing=0 AND (saved OR viewed);
