-- Empty article lists and a false partial-download flag carry no source facts.
-- Nonempty or true values remain for migration 113 to reject explicitly.
LOCK TABLE publications IN ACCESS EXCLUSIVE MODE;
UPDATE publications SET content=content #- '{extra,_reader,source_manifest,articles}'
 WHERE content #> '{extra,_reader,source_manifest,articles}'='[]'::jsonb;
UPDATE publications SET content=content #- '{extra,_reader,source_manifest,download_partial}'
 WHERE content #> '{extra,_reader,source_manifest,download_partial}'='false'::jsonb;
