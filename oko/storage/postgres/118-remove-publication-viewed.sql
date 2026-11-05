-- Read completion is represented by feed ownership and reachability.
-- Migration 117 already transferred unfinished file cleanup to Jobs.
ALTER TABLE publications DROP COLUMN viewed;
CREATE INDEX publication_stage ON publications(status,id) WHERE platform IS NOT NULL;
CREATE INDEX publications_order ON publications(published_at DESC,id);
