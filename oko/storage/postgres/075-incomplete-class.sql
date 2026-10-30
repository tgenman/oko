ALTER TABLE publications ADD COLUMN incomplete_class smallint;
CREATE INDEX publications_incomplete_recovery ON publications(id) WHERE viewed=0 AND complete=1 AND NOT source_deleted AND incomplete_class IS NULL;
CREATE INDEX publications_incomplete_candidates ON publications(id) WHERE viewed=0 AND complete=1 AND NOT source_deleted AND incomplete_class IS DISTINCT FROM 1;
