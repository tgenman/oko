-- Unknown historical states are left untouched; migration stops for reconciliation.
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM publications WHERE status NOT IN ('pending_collection','pending_filtering','filter_out','pending_summary','ready'))
 OR EXISTS (SELECT 1 FROM jobs WHERE status IS NULL OR status NOT IN ('blocked','pending','running','done','failed','unavailable','cancelled')) THEN
  RAISE EXCEPTION 'invalid saved status';
 END IF;
END $$;
ALTER TABLE publications ADD CONSTRAINT publications_status_allowed
 CHECK (status IN ('pending_collection','pending_filtering','filter_out','pending_summary','ready'));
ALTER TABLE jobs ALTER COLUMN status SET NOT NULL;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_allowed
 CHECK (status IN ('blocked','pending','running','done','failed','unavailable','cancelled'));
