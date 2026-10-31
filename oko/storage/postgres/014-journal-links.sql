-- Index authoritative journal objects directly; do not retain a derived link table.
DO $body$
BEGIN
    BEGIN
        PERFORM body::jsonb FROM reader_action_results;
        PERFORM body::jsonb FROM reader_problems;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid journal body';
    END;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT body::jsonb AS body FROM reader_action_results
            UNION ALL SELECT body::jsonb FROM reader_problems
        ) records
        WHERE jsonb_typeof(body) <> 'object'
           OR (body ? 'objects' AND jsonb_typeof(body->'objects') <> 'array')
    ) THEN
        RAISE EXCEPTION 'invalid journal objects';
    END IF;
    IF EXISTS (
        WITH records AS (
            SELECT 'action'::text AS kind,id,body::jsonb AS body FROM reader_action_results
            UNION ALL SELECT 'problem'::text,id,body::jsonb FROM reader_problems
        ), objects AS (
            SELECT kind,id,obj FROM records CROSS JOIN LATERAL jsonb_array_elements(coalesce(body->'objects','[]'::jsonb)) AS obj
        )
        SELECT 1 FROM objects WHERE obj->>'kind'='material'
            AND (jsonb_typeof(obj->'id') IS DISTINCT FROM 'string' OR obj->>'id'='')
    ) THEN
        RAISE EXCEPTION 'invalid journal material reference';
    END IF;
    IF EXISTS (
        WITH expected AS (
            SELECT 'action'::text AS kind,r.id AS record,obj->>'id' AS item FROM reader_action_results r
              CROSS JOIN LATERAL jsonb_array_elements(coalesce(r.body::jsonb->'objects','[]'::jsonb)) AS obj
              WHERE obj->>'kind'='material'
            UNION
            SELECT 'problem'::text,r.id,obj->>'id' FROM reader_problems r
              CROSS JOIN LATERAL jsonb_array_elements(coalesce(r.body::jsonb->'objects','[]'::jsonb)) AS obj
              WHERE obj->>'kind'='material'
        ), differences AS (
            (SELECT kind,record,item FROM expected EXCEPT SELECT kind,record,item FROM reader_material_links)
            UNION ALL
            (SELECT kind,record,item FROM reader_material_links EXCEPT SELECT kind,record,item FROM expected)
        ) SELECT 1 FROM differences
    ) THEN
        RAISE EXCEPTION 'inconsistent journal links';
    END IF;
END
$body$;
CREATE INDEX reader_action_objects_gin ON reader_action_results USING gin ((body::jsonb->'objects') jsonb_path_ops);
CREATE INDEX reader_problem_objects_gin ON reader_problems USING gin ((body::jsonb->'objects') jsonb_path_ops);
DROP TRIGGER journal_links_action_delete ON reader_action_results;
DROP TRIGGER journal_links_action_insert ON reader_action_results;
DROP TRIGGER journal_links_action_update ON reader_action_results;
DROP TRIGGER journal_links_problem_delete ON reader_problems;
DROP TRIGGER journal_links_problem_insert ON reader_problems;
DROP TRIGGER journal_links_problem_update ON reader_problems;
DROP FUNCTION oko_trigger_journal_links_action_delete();
DROP FUNCTION oko_trigger_journal_links_action_insert();
DROP FUNCTION oko_trigger_journal_links_action_update();
DROP FUNCTION oko_trigger_journal_links_problem_delete();
DROP FUNCTION oko_trigger_journal_links_problem_insert();
DROP FUNCTION oko_trigger_journal_links_problem_update();
DROP TABLE reader_material_links;
