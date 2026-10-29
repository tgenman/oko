-- A resumable cursor is transitional state for existing client subscriptions.
ALTER TABLE telegram_read_state ADD COLUMN saved_backfill_after text;
INSERT INTO telegram_read_state(feed,needs_sync,saved_backfill_after)
 SELECT f.id,true,'' FROM feeds f JOIN telegram_bindings b ON b.feed=f.id
 WHERE f.transport='telegram' AND f.connection IS DISTINCT FROM 'deleting'
   AND b.binding::jsonb->>'kind'='client'
 ON CONFLICT(feed) DO UPDATE SET needs_sync=true,saved_backfill_after='';
CREATE INDEX telegram_saved_backfill ON telegram_read_state(feed) WHERE saved_backfill_after IS NOT NULL;
