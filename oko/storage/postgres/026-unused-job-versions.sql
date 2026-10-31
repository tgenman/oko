-- The old resource versions are no longer read by the HTTP reader.
DO $$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY[
  'reader_v_job_filter_delete','reader_v_job_filter_insert','reader_v_job_filter_update',
  'reader_v_job_theses_delete','reader_v_job_theses_insert','reader_v_job_theses_update',
  'reader_v_job_history_delete','reader_v_job_history_insert','reader_v_job_history_update',
  'reader_v_filter_eligibility_delete','reader_v_filter_eligibility_insert','reader_v_filter_eligibility_update'
 ] LOOP
  EXECUTE format('DROP TRIGGER %I ON jobs',name);
  EXECUTE format('DROP FUNCTION %I()', 'oko_trigger_' || name);
 END LOOP;
END $$;
DROP TRIGGER reader_v_feed_material ON feeds;
DROP FUNCTION oko_trigger_reader_v_feed_material();
DROP TRIGGER reader_v_item_inputs ON items;
DROP FUNCTION oko_trigger_reader_v_item_inputs();
DROP TRIGGER reader_v_item_media_available ON items;
DROP FUNCTION oko_trigger_reader_v_item_media_available();
