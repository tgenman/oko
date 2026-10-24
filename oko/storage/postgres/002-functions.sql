-- Helpers implement the existing repository JSON scalar contract explicitly.
CREATE FUNCTION oko_json_value(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE jsonb_typeof(value)
        WHEN 'null' THEN NULL
        WHEN 'boolean' THEN CASE WHEN value = 'true'::jsonb THEN '1' ELSE '0' END
        WHEN 'string' THEN value #>> '{}'
        ELSE value::text END
$$;

CREATE FUNCTION oko_json_path(document text, path text) RETURNS jsonb
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE WHEN path = '$' THEN document::jsonb
        ELSE jsonb_path_query_first(document::jsonb, path::jsonpath) END
$$;

CREATE FUNCTION oko_json_extract(document text, path text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT oko_json_value(oko_json_path(document, path))
$$;

CREATE FUNCTION oko_json_each(document text, path text DEFAULT '$')
RETURNS TABLE(key text, value text)
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    WITH selected AS (SELECT oko_json_path(document,path) AS body)
    SELECT (ordinal-1)::text, oko_json_value(element)
    FROM selected, LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(body)='array' THEN body ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS parts(element,ordinal)
    UNION ALL
    SELECT property, oko_json_value(element)
    FROM selected, LATERAL jsonb_each(
        CASE WHEN jsonb_typeof(body)='object' THEN body ELSE '{}'::jsonb END
    ) AS parts(property,element)
$$;

CREATE FUNCTION oko_max(bigint, bigint) RETURNS bigint
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$ SELECT greatest($1,$2) $$;

-- Reader membership invalidation is transaction-local work recorded durably in
-- the same database transaction; ReaderVersions consumes it before commit.
CREATE TABLE reader_membership_dirty (
    kind text NOT NULL, identity text NOT NULL, PRIMARY KEY(kind,identity)
);

CREATE FUNCTION oko_memberships_changed(identity text) RETURNS void
LANGUAGE sql VOLATILE STRICT AS $$
    INSERT INTO reader_membership_dirty VALUES ('feed',identity) ON CONFLICT DO NOTHING
$$;

CREATE FUNCTION oko_folder_memberships_changed(identity text) RETURNS void
LANGUAGE sql VOLATILE STRICT AS $$
    INSERT INTO reader_membership_dirty VALUES ('folder',identity) ON CONFLICT DO NOTHING
$$;

CREATE FUNCTION oko_reader_time() RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('oko.wall',true),'')::double precision, extract(epoch FROM clock_timestamp()))
$$;

CREATE FUNCTION oko_reader_clock() RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('oko.clock',true),'')::double precision, extract(epoch FROM clock_timestamp()))
$$;

CREATE FUNCTION oko_max(VARIADIC parts double precision[]) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE WHEN array_position(parts,NULL) IS NOT NULL THEN NULL
        ELSE (SELECT max(value) FROM unnest(parts) AS valueset(value)) END
$$;
