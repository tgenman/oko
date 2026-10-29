-- Collection and membership owners reconcile affected publications in Python.
DROP TRIGGER processing_item_added ON items;
DROP TRIGGER processing_item_changed ON items;
DROP FUNCTION oko_trigger_processing_item_added();
DROP FUNCTION oko_trigger_processing_item_changed();
