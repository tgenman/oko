-- Rename the existing relation in place; PostgreSQL preserves rows and view dependencies.
ALTER TABLE items RENAME TO publications;
