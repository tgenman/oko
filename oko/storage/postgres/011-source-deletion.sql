-- Move source-deletion markers to their publication without losing orphans.
DO $body$
BEGIN
    IF EXISTS (SELECT 1 FROM source_deleted d LEFT JOIN items i ON i.id=d.item WHERE i.id IS NULL) THEN
        RAISE EXCEPTION 'orphan source deletion';
    END IF;
END
$body$;
ALTER TABLE items ADD COLUMN source_deleted boolean NOT NULL DEFAULT false;
UPDATE items i SET source_deleted=true FROM source_deleted d WHERE d.item=i.id;
CREATE OR REPLACE VIEW items_by_feed AS
 SELECT i.id,i.source_id,i.url,i.published,i.viewed,i.complete,i.members,i.published_known,i.source_order,i.platform,i.external_id,i.title,i.published_at,i.discovered_at,i.content,i.media_ids,i.filtered_content,i.summarized_content,i.processing_metadata,i.status,m.feed_id,i.source_deleted FROM feed_publications m JOIN items i ON i.id=m.publication_id WHERE m.is_primary
 UNION ALL
 SELECT i.id,i.source_id,i.url,i.published,i.viewed,i.complete,i.members,i.published_known,i.source_order,i.platform,i.external_id,i.title,i.published_at,i.discovered_at,i.content,i.media_ids,i.filtered_content,i.summarized_content,i.processing_metadata,i.status,j.payload::jsonb->>'cleanup_feed' AS feed_id,i.source_deleted
 FROM items i LEFT JOIN jobs j ON j.kind='delete' AND j.item=i.id AND i.viewed=1
 WHERE NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary);
CREATE OR REPLACE VIEW unviewed_items_by_feed AS
 SELECT i.id,i.source_id,i.url,i.published,i.viewed,i.complete,i.members,i.published_known,i.source_order,i.platform,i.external_id,i.title,i.published_at,i.discovered_at,i.content,i.media_ids,i.filtered_content,i.summarized_content,i.processing_metadata,i.status,m.feed_id,i.source_deleted FROM feed_publications m JOIN items i ON i.id=m.publication_id
 WHERE m.is_primary AND m.is_unviewed AND i.viewed=0;
CREATE OR REPLACE VIEW items_read AS SELECT i.id,i.source_id,i.url,i.published,i.viewed,i.complete,i.members,i.published_known,i.source_order,i.platform,i.external_id,i.title,i.published_at,i.discovered_at,i.content,i.media_ids,i.filtered_content,i.summarized_content,i.processing_metadata,i.status,coalesce(
 (SELECT m.feed_id FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary),
 (SELECT j.payload::jsonb->>'cleanup_feed' FROM jobs j WHERE j.kind='delete' AND j.item=i.id AND i.viewed=1)
) AS feed_id,i.source_deleted FROM items i;
DROP TABLE source_deleted;
DROP FUNCTION oko_trigger_reader_v_deleted_delete();
DROP FUNCTION oko_trigger_reader_v_deleted_insert();
DROP FUNCTION oko_trigger_reader_v_deleted_update();
