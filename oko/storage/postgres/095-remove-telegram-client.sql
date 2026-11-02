-- Check and lock only relations owned by the selected schema. Keep the
-- restricted path through preflight and mutation; restore it for the ledger.
DO $$
DECLARE
    target_schema pg_catalog.text := pg_catalog.current_schema();
    original_path pg_catalog.text := pg_catalog.current_setting('search_path');
    relation pg_catalog.text;
BEGIN
    IF target_schema IS NULL THEN
        RAISE EXCEPTION 'Telegram removal requires a current schema';
    END IF;
    FOREACH relation IN ARRAY ARRAY['feeds','jobs','reader_problems','state',
        'telegram_bindings','telegram_profiles','telegram_read_members','telegram_read_state'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = target_schema AND c.relname = relation AND c.relkind IN ('r','p')
        ) THEN
            RAISE EXCEPTION 'Telegram removal requires % in current schema', relation;
        END IF;
        EXECUTE pg_catalog.format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE', target_schema, relation);
    END LOOP;
    PERFORM pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,pg_temp',target_schema),true);

    -- Refuse unknown, malformed or unsupported identities before removing state.
    IF EXISTS (
        SELECT 1 FROM telegram_bindings
        WHERE binding_raw IS NOT NULL OR binding IS NULL OR indexed_role IS NOT NULL
           OR jsonb_typeof(binding) IS DISTINCT FROM 'object'
           OR binding->>'kind' IS DISTINCT FROM 'web'
           OR jsonb_typeof(binding->'username') IS DISTINCT FROM 'string'
           OR coalesce(binding->>'username','') !~ '^[A-Za-z][A-Za-z0-9_]{3,31}$'
           OR jsonb_typeof(binding->'preview_url') IS DISTINCT FROM 'string'
           OR coalesce(binding->>'preview_url','') !~* '^https://(t[.]me|telegram[.]me)/s/[A-Za-z][A-Za-z0-9_]{3,31}([?#][^[:space:]]*)?$'
           OR lower(split_part(split_part(binding->>'preview_url','?',1),'#',1)) NOT IN
                ('https://t.me/s/' || lower(binding->>'username'), 'https://telegram.me/s/' || lower(binding->>'username'))
           OR length(coalesce(binding->>'preview_url','')) > 4000
           OR (binding - ARRAY['kind','username','preview_url']) <> '{}'::jsonb
    ) OR EXISTS (
        SELECT 1 FROM feeds f LEFT JOIN telegram_bindings b ON b.feed = f.id
        WHERE f.adapter = 'telegram_client_adapter'
           OR (f.kind = 'telegram' AND b.feed IS NULL)
    ) THEN
        RAISE EXCEPTION 'Unsupported or unreadable Telegram binding; remove the account-backed feed before upgrading';
    END IF;

    DELETE FROM jobs WHERE kind = 'read';
    -- A shared publication can retain a job that failed only because its old
    -- account profile was unavailable. Requeue it without the obsolete error.
    UPDATE jobs SET status='pending', error=NULL, problem_code=NULL,
        generation=generation+1, next=0, retry_at=NULL, retry_delay=0,
        wait_scope=NULL, identity_event_owned=false
    WHERE kind IN ('discover','fetch','download_media')
      AND status NOT IN ('done','cancelled')
      AND problem_code IN ('profile_not_configured','profile_unavailable','account_identity_mismatch');
    DELETE FROM telegram_read_members;
    DELETE FROM telegram_read_state;
    DELETE FROM telegram_profiles;
    DELETE FROM state WHERE key LIKE 'telegram-retry:%';

    -- Retain historical diagnostics, but finish identifiable account problems.
    UPDATE reader_problems
    SET finished = extract(epoch FROM clock_timestamp()), outcome = 'irrelevant'
    WHERE finished IS NULL AND (
        (identity LIKE 'profile:%' AND body::jsonb->>'code' IN
            ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))
        OR (identity LIKE 'job:%' AND (
            body::jsonb->>'operation' = 'producer:read'
            OR body::jsonb->>'code' IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))
        OR (identity LIKE 'action:%' AND CASE
            WHEN pg_input_is_valid(body::jsonb->>'operation','jsonb')
            THEN coalesce((body::jsonb->>'operation')::jsonb #>> '{context,operation}',
                          (body::jsonb->>'operation')::jsonb ->> 'operation') = 'telegram_profile_save'
            ELSE false END)
    );
    PERFORM pg_catalog.set_config('search_path',original_path,true);
END $$;
