-- Historical filtering copied the source extra verbatim. Remove that copy
-- while it can still be compared, before immutable migrations alter content.
-- Nonmatching copies remain untouched and the final audit rejects them.
DO $$
BEGIN
 IF to_regclass(format('%I.publications',current_schema())) IS NULL THEN
  RAISE EXCEPTION 'Publication table is missing from current schema';
 END IF;
END $$;
LOCK TABLE publications IN ACCESS EXCLUSIVE MODE;
UPDATE publications p SET filtered_content=(p.filtered_content::jsonb-'extra')::text
WHERE (CASE WHEN pg_input_is_valid(p.filtered_content,'jsonb') THEN p.filtered_content::jsonb END) ? 'extra'
 AND ((CASE WHEN pg_input_is_valid(p.filtered_content,'jsonb') THEN p.filtered_content::jsonb END)->'extra' IN ('{}'::jsonb,'null'::jsonb)
      OR (CASE WHEN pg_input_is_valid(p.filtered_content,'jsonb') THEN p.filtered_content::jsonb END)->'extra' =
         (CASE WHEN pg_input_is_valid(p.content::text,'jsonb') THEN p.content::jsonb END)->'extra');
