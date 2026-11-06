-- Restore the original URLs after each resource has been matched by order.
DO $$
BEGIN
 IF to_regclass('pg_temp.oko_migration_external_urls') IS NOT NULL THEN
  UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',(
   SELECT jsonb_agg(CASE WHEN u.temporary_url IS NOT NULL THEN jsonb_set(b.value,'{url}',to_jsonb(u.original_url)) ELSE b.value END ORDER BY b.ordinal)
   FROM jsonb_array_elements(p.content->'blocks') WITH ORDINALITY b(value,ordinal)
   LEFT JOIN oko_migration_external_urls u ON u.publication_id=p.id AND u.temporary_url=b.value->>'url'
  )) WHERE EXISTS (SELECT 1 FROM oko_migration_external_urls u WHERE u.publication_id=p.id);
 END IF;
END $$;
