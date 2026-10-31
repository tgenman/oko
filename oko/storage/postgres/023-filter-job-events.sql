-- The Job owner records dependent processing changes in the same transaction.
DROP TRIGGER processing_filter_changed ON jobs;
DROP FUNCTION oko_trigger_processing_filter_changed();
