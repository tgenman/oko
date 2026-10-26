-- Reverse dependency lookup; the primary key already covers the source direction.
CREATE INDEX publication_links_target ON publication_links(target_publication_id,source_publication_id);
