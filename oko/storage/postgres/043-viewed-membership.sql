-- Repair the derived flag before replacing its trigger with the storage owner.
UPDATE feed_publications m SET is_unviewed=coalesce(i.viewed=0,false)
FROM items i WHERE i.id=m.publication_id AND m.is_unviewed IS DISTINCT FROM coalesce(i.viewed=0,false);
DROP TRIGGER membership_viewed_changed ON items;
DROP FUNCTION oko_membership_viewed_changed();
