-- Matching copies carry no independent data. Malformed content is left intact.
UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,_reader,title}')::text
WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,title}') = 'string'
  AND (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,_reader,title}' = title;

-- For a differing copy the reader previously displayed the header title. Move
-- that value to the publication, invalidating parent processing that used the
-- previous source title. Leave rows with unreadable parent metadata untouched.
DO $migration$
DECLARE candidate record;
BEGIN
  FOR candidate IN
    SELECT id, (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,_reader,title}' AS displayed
    FROM publications
    WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,title}') = 'string'
      AND (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,_reader,title}' IS DISTINCT FROM title
      AND NOT EXISTS (
        SELECT 1 FROM publication_links l JOIN publications parent ON parent.id=l.source_publication_id
        WHERE l.target_publication_id=publications.id
          AND (NOT pg_input_is_valid(parent.processing_metadata, 'jsonb') OR
               jsonb_typeof(CASE WHEN pg_input_is_valid(parent.processing_metadata, 'jsonb') THEN parent.processing_metadata::jsonb END) IS DISTINCT FROM 'object')
      )
  LOOP
    UPDATE publications parent SET processing_metadata = (
      CASE WHEN jsonb_typeof(parent.processing_metadata::jsonb->'summary')='object'
                  AND parent.processing_metadata::jsonb #>> '{summary,reason}' IS DISTINCT FROM 'no_input'
        THEN jsonb_set(
          CASE WHEN jsonb_typeof(parent.processing_metadata::jsonb->'filter')='object'
                    AND parent.processing_metadata::jsonb #>> '{filter,reason}' IS DISTINCT FROM 'no_input'
               THEN jsonb_set(parent.processing_metadata::jsonb, '{filter,source_title_stale}', 'true'::jsonb)
               ELSE parent.processing_metadata::jsonb END,
          '{summary,source_title_stale}', 'true'::jsonb)
        WHEN jsonb_typeof(parent.processing_metadata::jsonb->'filter')='object'
                  AND parent.processing_metadata::jsonb #>> '{filter,reason}' IS DISTINCT FROM 'no_input'
        THEN jsonb_set(parent.processing_metadata::jsonb, '{filter,source_title_stale}', 'true'::jsonb)
        ELSE parent.processing_metadata::jsonb END)::text
    WHERE parent.id IN (SELECT source_publication_id FROM publication_links WHERE target_publication_id=candidate.id);

    UPDATE jobs SET input_snapshot=NULL
    WHERE item IN (SELECT source_publication_id FROM publication_links WHERE target_publication_id=candidate.id)
      AND kind IN ('filter','theses') AND status<>'cancelled' AND input_snapshot IS NOT NULL;

    UPDATE publications SET title=candidate.displayed, content=(content::jsonb #- '{extra,_reader,title}')::text
    WHERE id=candidate.id;
  END LOOP;
END
$migration$;
