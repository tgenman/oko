-- Membership owners record reader and processing effects in their transaction.
DROP TRIGGER membership_owner_changed ON feed_publications;
DROP TRIGGER reader_v_shared_insert ON feed_publications;
DROP TRIGGER reader_v_shared_delete ON feed_publications;
DROP FUNCTION oko_membership_owner_changed();
DROP FUNCTION oko_trigger_reader_v_shared_insert();
DROP FUNCTION oko_trigger_reader_v_shared_delete();
