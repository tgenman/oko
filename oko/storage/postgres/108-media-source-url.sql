-- Adapter locators can be opaque; only a direct web address is a user link.
LOCK TABLE publications, media_files, publication_links IN ACCESS EXCLUSIVE MODE;
ALTER TABLE media_files ADD COLUMN source_url text;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO conflicts FROM (
   SELECT m.id FROM media_files m JOIN publications p ON p.id=m.publication_id
   CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
   WHERE b.block->>'type'='media' AND b.block->>'media_id'=m.id
   GROUP BY m.id HAVING count(DISTINCT NULLIF(b.block->>'source_url',''))>1
     OR bool_or((b.block ? 'source_url' AND jsonb_typeof(b.block->'source_url') NOT IN ('string','null'))
       OR (b.block ? 'publication_url' AND jsonb_typeof(b.block->'publication_url') NOT IN ('string','null')))
 ) q;
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Conflicting media source URLs: %', conflicts; END IF;
END $$;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO conflicts FROM (
   SELECT p.id FROM publications p
   CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
   WHERE b.block->>'type'='publication'
   GROUP BY p.id HAVING count(*)<>count(DISTINCT b.block->>'publication_id')
     OR bool_or(NOT EXISTS (SELECT 1 FROM publication_links l JOIN publications target ON target.id=l.target_publication_id
       WHERE l.source_publication_id=p.id AND target.id=b.block->>'publication_id'
         AND (b.block->>'source_url' IS NULL OR b.block->>'source_url'=target.url)))
 ) q;
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Conflicting publication blocks: %', conflicts; END IF;
END $$;
UPDATE media_files m SET source_url=(
 SELECT NULLIF(b.block->>'source_url','') FROM publications p
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
 WHERE p.id=m.publication_id AND b.block->>'type'='media' AND b.block->>'media_id'=m.id
   AND NULLIF(b.block->>'source_url','') IS NOT NULL LIMIT 1
);
UPDATE media_files SET source_url=source_locator
WHERE source_url IS NULL AND length(source_locator)<=4000
  AND source_locator ~ '^https?://[^/@[:space:]]+([/?#][^[:space:]]*)?$';
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',(
 SELECT jsonb_agg(CASE WHEN b.block->>'type'='media' AND m.id IS NOT NULL THEN
   b.block || jsonb_build_object('source_url',m.source_url,'publication_url',
     CASE WHEN b.block->>'publication_url' IS NOT NULL THEN b.block->>'publication_url'
       WHEN origin.value ~ '^telegram:[A-Za-z0-9_]+/[0-9]+$' AND m.resource_key ~ '^telegram-web:[1-9][0-9]*:[0-9]+:'
         THEN 'https://t.me/'||split_part(split_part(origin.value,':',2),'/',1)||'/'||split_part(m.resource_key,':',2)
       WHEN origin.value ~ '^telegram:[A-Za-z0-9_]+/[0-9]+$' THEN 'https://t.me/'||substring(origin.value from 10)
       WHEN p.platform='telegram' AND jsonb_array_length(p.members::jsonb)>1 THEN NULL
       ELSE NULLIF(p.url,'') END)
   ELSE b.block END ORDER BY b.ordinal)
 FROM jsonb_array_elements(p.content->'blocks') WITH ORDINALITY b(block,ordinal)
 LEFT JOIN media_files m ON m.publication_id=p.id AND m.id=b.block->>'media_id'
 LEFT JOIN LATERAL (SELECT r.value->>'origin' AS value
   FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
     THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) r(value)
   WHERE r.value->>'key'=m.resource_key LIMIT 1) origin ON TRUE
)) WHERE jsonb_typeof(p.content->'blocks')='array' AND jsonb_array_length(p.content->'blocks')>0
  AND EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id);
-- Keep direct links in the document as well as in the relationship table.
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',(
 SELECT COALESCE(jsonb_agg(CASE WHEN b.block->>'type'='publication' THEN
   b.block || jsonb_build_object('source_url',target.url) ELSE b.block END ORDER BY b.ordinal),'[]'::jsonb)
 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) WITH ORDINALITY b(block,ordinal)
 LEFT JOIN publications target ON target.id=b.block->>'publication_id'
) || (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('type','publication','publication_id',target.id,'source_url',target.url) ORDER BY l.rowid),'[]'::jsonb)
 FROM publication_links l JOIN publications target ON target.id=l.target_publication_id
 WHERE l.source_publication_id=p.id AND NOT EXISTS (
   SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
   WHERE b.block->>'type'='publication' AND b.block->>'publication_id'=target.id)
),true)
WHERE p.content<>'{}'::jsonb AND EXISTS (SELECT 1 FROM publication_links l WHERE l.source_publication_id=p.id);
-- External resources must remain readable without the adapter's manifest.
DO $$
DECLARE conflicts text;
BEGIN
 WITH resources AS (
  SELECT p.id,r.value->'external' AS external
  FROM publications p CROSS JOIN LATERAL jsonb_array_elements(
   CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
    THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) r(value)
  WHERE r.value->>'status'='external'
 ), bad AS (
  SELECT id FROM resources WHERE jsonb_typeof(external) IS DISTINCT FROM 'object'
    OR COALESCE(external->>'kind','')='' OR COALESCE(external->>'source_url','')=''
  UNION
  SELECT id FROM resources GROUP BY id,external->>'kind',external->>'source_url' HAVING count(*)>1
  UNION
  SELECT r.id FROM resources r JOIN publications p ON p.id=r.id
  CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
  WHERE b.block->>'type'='external_media' AND b.block->>'kind'=r.external->>'kind' AND b.block->>'url'=r.external->>'source_url'
  GROUP BY r.id,r.external->>'kind',r.external->>'source_url' HAVING count(*)>1
  UNION
  SELECT r.id FROM resources r JOIN publications p ON p.id=r.id
  CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
  CROSS JOIN LATERAL unnest(ARRAY['reason','restriction','title','size_label','author','duration']) field(name)
  WHERE b.block->>'type'='external_media' AND b.block->>'kind'=r.external->>'kind' AND b.block->>'url'=r.external->>'source_url'
    AND b.block->field.name IS NOT NULL AND b.block->field.name<>'null'::jsonb
    AND r.external->field.name IS NOT NULL AND r.external->field.name<>'null'::jsonb
    AND b.block->field.name IS DISTINCT FROM r.external->field.name
 )
 SELECT string_agg(DISTINCT id, ', ' ORDER BY id) INTO conflicts FROM bad;
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Conflicting external media: %', conflicts; END IF;
END $$;
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',(
 SELECT COALESCE(jsonb_agg(CASE WHEN ext.value IS NOT NULL THEN
   (ext.value-'source_url'-'poster_key') || b.block ||
   CASE WHEN ext.value->>'poster_key' IS NOT NULL THEN jsonb_build_object('poster_media_id',poster.id) ELSE '{}'::jsonb END
   ELSE b.block END ORDER BY b.ordinal),'[]'::jsonb)
 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) WITH ORDINALITY b(block,ordinal)
 LEFT JOIN LATERAL (SELECT r.value->'external' AS value FROM jsonb_array_elements(
   CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
    THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) r(value)
   WHERE r.value->>'status'='external' AND r.value #>> '{external,kind}'=b.block->>'kind'
    AND r.value #>> '{external,source_url}'=b.block->>'url' LIMIT 1) ext ON b.block->>'type'='external_media'
 LEFT JOIN media_files poster ON poster.publication_id=p.id AND poster.resource_key=ext.value->>'poster_key'
) || (
 SELECT COALESCE(jsonb_agg(((r.value->'external')-'source_url'-'poster_key') ||
   jsonb_build_object('type','external_media','url',r.value #>> '{external,source_url}') ||
   CASE WHEN r.value #>> '{external,poster_key}' IS NOT NULL THEN jsonb_build_object('poster_media_id',poster.id) ELSE '{}'::jsonb END
   ORDER BY r.ordinal),'[]'::jsonb)
 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
   THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) WITH ORDINALITY r(value,ordinal)
 LEFT JOIN media_files poster ON poster.publication_id=p.id AND poster.resource_key=r.value #>> '{external,poster_key}'
 WHERE r.value->>'status'='external' AND NOT EXISTS (
   SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
   WHERE b.block->>'type'='external_media' AND b.block->>'kind'=r.value #>> '{external,kind}'
     AND b.block->>'url'=r.value #>> '{external,source_url}')
),true)
WHERE p.content<>'{}'::jsonb AND EXISTS (SELECT 1 FROM jsonb_array_elements(
 CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
  THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) r(value) WHERE r.value->>'status'='external');
-- Preserve album membership in the source-specific document. Legacy columns
-- remain until their consumers are switched, but disagreements stop migration.
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO conflicts FROM publications
 WHERE platform='telegram' AND content<>'{}'::jsonb AND (
   (content #> '{extra,telegram,members}' IS NOT NULL AND content #> '{extra,_reader,members}' IS NOT NULL
    AND content #> '{extra,telegram,members}' <> content #> '{extra,_reader,members}')
   OR (content #> '{extra,telegram,members}' IS NOT NULL AND members::jsonb<>'[]'::jsonb
    AND content #> '{extra,telegram,members}' <> members::jsonb)
   OR (content #> '{extra,_reader,members}' IS NOT NULL AND members::jsonb<>'[]'::jsonb
    AND content #> '{extra,_reader,members}' <> members::jsonb)
 );
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Conflicting Telegram members: %', conflicts; END IF;
END $$;
UPDATE publications p SET content=jsonb_set(p.content,'{extra}',
 COALESCE(p.content->'extra','{}'::jsonb) || jsonb_build_object('telegram',
 COALESCE(p.content #> '{extra,telegram}','{}'::jsonb) || jsonb_build_object('members',
 COALESCE(p.content #> '{extra,telegram,members}',NULLIF(p.members::jsonb,'[]'::jsonb),p.content #> '{extra,_reader,members}','[]'::jsonb))),true)
WHERE p.platform='telegram' AND p.content<>'{}'::jsonb;
-- A video has the same display type regardless of the source platform.
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',(
 SELECT jsonb_agg(CASE WHEN b.block->>'type'='external_media' AND b.block->>'kind'='youtube'
   THEN b.block || '{"kind":"video"}'::jsonb ELSE b.block END ORDER BY b.ordinal)
 FROM jsonb_array_elements(p.content->'blocks') WITH ORDINALITY b(block,ordinal)))
WHERE jsonb_typeof(p.content->'blocks')='array' AND jsonb_array_length(p.content->'blocks')>0
 AND EXISTS (SELECT 1 FROM jsonb_array_elements(p.content->'blocks') b(block)
   WHERE b.block->>'type'='external_media' AND b.block->>'kind'='youtube');
