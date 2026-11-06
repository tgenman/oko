-- Old Telegram adapters emitted one block per resource, in resource order.
-- Distinguish equal source URLs only within the migration transaction, so the
-- old migration can merge each resource's metadata into its matching block.
CREATE TEMP TABLE oko_migration_external_urls (
 publication_id text NOT NULL, temporary_url text NOT NULL, original_url text NOT NULL,
 PRIMARY KEY(publication_id,temporary_url)
) ON COMMIT DROP;
DO $$
DECLARE p record; grp record; r record; b record; doc jsonb; marker text; n integer; field text;
BEGIN
 FOR p IN SELECT id,content FROM publications WHERE jsonb_typeof(content #> '{extra,_reader,source_manifest,resources}')='array' LOOP
  doc:=p.content;
  FOR grp IN SELECT rr.value #>> '{external,kind}' kind,rr.value #>> '{external,source_url}' url,count(*) amount
   FROM jsonb_array_elements(p.content #> '{extra,_reader,source_manifest,resources}') rr(value)
   WHERE rr.value->>'status'='external' GROUP BY 1,2 HAVING count(*)>1 LOOP
   IF NULLIF(grp.kind,'') IS NULL OR NULLIF(grp.url,'') IS NULL OR
      (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) x
       WHERE x->>'type'='external_media' AND x->>'kind'=grp.kind AND x->>'url'=grp.url)<>grp.amount THEN
    RAISE EXCEPTION 'Ambiguous repeated external media: %',p.id;
   END IF;
   n:=0;
   FOR r IN SELECT value,ordinal FROM jsonb_array_elements(p.content #> '{extra,_reader,source_manifest,resources}') WITH ORDINALITY x(value,ordinal)
     WHERE value->>'status'='external' AND value #>> '{external,kind}'=grp.kind AND value #>> '{external,source_url}'=grp.url ORDER BY ordinal LOOP
    SELECT value,ordinal INTO b FROM jsonb_array_elements(p.content->'blocks') WITH ORDINALITY x(value,ordinal)
     WHERE value->>'type'='external_media' AND value->>'kind'=grp.kind AND value->>'url'=grp.url ORDER BY ordinal OFFSET n LIMIT 1;
    FOREACH field IN ARRAY ARRAY['reason','restriction','title','size_label','author','duration'] LOOP
     IF b.value->field IS NOT NULL AND b.value->field<>'null'::jsonb AND r.value->'external'->field IS NOT NULL AND r.value->'external'->field<>'null'::jsonb AND b.value->field IS DISTINCT FROM r.value->'external'->field THEN
      RAISE EXCEPTION 'Conflicting repeated external media: %',p.id;
     END IF;
    END LOOP;
    IF r.value #>> '{external,poster_key}' IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.resource_key=r.value #>> '{external,poster_key}'
       AND (b.value->'poster_media_id' IS NULL OR b.value->'poster_media_id'='null'::jsonb OR m.id=b.value->>'poster_media_id')) THEN
     RAISE EXCEPTION 'Conflicting repeated external media poster: %',p.id;
    END IF;
    marker:='https://migration.invalid/'||md5(p.id||':'||grp.kind||':'||grp.url||':'||n::text);
    IF position(marker in p.content::text)>0 THEN RAISE EXCEPTION 'External media migration marker collision: %',p.id; END IF;
    INSERT INTO oko_migration_external_urls VALUES (p.id,marker,grp.url);
    doc:=jsonb_set(doc,ARRAY['blocks',(b.ordinal-1)::text,'url'],to_jsonb(marker));
    doc:=jsonb_set(doc,ARRAY['extra','_reader','source_manifest','resources',(r.ordinal-1)::text,'external','source_url'],to_jsonb(marker));
    n:=n+1;
   END LOOP;
  END LOOP;
  IF doc IS DISTINCT FROM p.content THEN UPDATE publications SET content=doc WHERE id=p.id; END IF;
 END LOOP;
END $$;
