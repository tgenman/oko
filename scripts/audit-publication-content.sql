-- Read-only aggregate inventory; no publication contents are returned.
-- Categories may overlap: quote_header_only includes unconvertible_quotes.
WITH parsed AS MATERIALIZED (
 SELECT platform, viewed, title, CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END AS doc
 FROM publications
), quoted AS (
 SELECT platform, viewed, title, doc, doc #> '{extra,_reader,reply_preview}' AS old_quote,
   (SELECT b FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc->'blocks')='array' THEN doc->'blocks' ELSE '[]'::jsonb END) WITH ORDINALITY AS q(b,n) WHERE b->>'type'='quote' ORDER BY n LIMIT 1) AS block_quote
 FROM parsed
)
SELECT count(*) AS publications,
 count(*) FILTER (WHERE viewed<>0) AS viewed_awaiting_cleanup,
 count(*) FILTER (WHERE doc IS NULL) AS unreadable_content,
 count(*) FILTER (WHERE doc IS NOT NULL AND (jsonb_typeof(doc) IS DISTINCT FROM 'object' OR (doc <> '{}'::jsonb AND jsonb_typeof(doc->'blocks') IS DISTINCT FROM 'array'))) AS malformed_structure,
 count(*) FILTER (WHERE jsonb_typeof(doc #> '{extra,_reader,title}')='string' AND doc #>> '{extra,_reader,title}' IS DISTINCT FROM title) AS title_conflicts,
 count(*) FILTER (WHERE jsonb_typeof(doc->'blocks')='array' AND jsonb_typeof(old_quote)='object' AND block_quote IS NULL) AS quote_header_only,
 count(*) FILTER (WHERE jsonb_typeof(old_quote)='object' AND block_quote IS NOT NULL AND jsonb_build_object('text', old_quote->'text','author',old_quote->'author','source_url',old_quote->'source_url') IS DISTINCT FROM jsonb_build_object('text', block_quote->'text','author',block_quote->'author','source_url',block_quote->'source_url')) AS quote_conflicts,
 count(*) FILTER (WHERE jsonb_typeof(old_quote)='object' AND (jsonb_typeof(old_quote->'text') IS DISTINCT FROM 'string' OR COALESCE(jsonb_typeof(old_quote->'author'),'null') NOT IN ('string','null') OR COALESCE(jsonb_typeof(old_quote->'source_url'),'null') NOT IN ('string','null'))) AS unconvertible_quotes,
 count(*) FILTER (WHERE platform='youtube' AND doc #> '{extra,_reader,description}' IS NOT NULL) AS youtube_old_descriptions,
 count(*) FILTER (WHERE platform='youtube' AND doc #> '{extra,_reader,captions}' IS NOT NULL) AS youtube_old_caption_states,
 count(*) FILTER (WHERE doc #> '{extra,telegram,manifest}' IS NOT NULL) AS telegram_old_adapter_manifests
FROM quoted;
