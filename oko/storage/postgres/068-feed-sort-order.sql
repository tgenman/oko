-- Preserve the existing identity values, sequence, uniqueness and order.
ALTER TABLE feeds RENAME COLUMN rowid TO sort_order;
