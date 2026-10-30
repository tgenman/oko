-- Card validators read Job and linked-material facts directly.
DO $$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY[
  'reader_v_job_card_filter_delete','reader_v_job_card_filter_insert','reader_v_job_card_filter_update',
  'reader_v_job_card_theses_delete','reader_v_job_card_theses_insert','reader_v_job_card_theses_update',
  'reader_v_job_fetch_delete','reader_v_job_fetch_insert','reader_v_job_fetch_update'
 ] LOOP
  EXECUTE format('DROP TRIGGER %I ON jobs',name);
  EXECUTE format('DROP FUNCTION %I()', 'oko_trigger_' || name);
 END LOOP;
END $$;
