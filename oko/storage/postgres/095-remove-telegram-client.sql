-- Never discard a feed whose old account-backed identity cannot be migrated.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM telegram_bindings
        WHERE binding_raw IS NOT NULL OR binding IS NULL
           OR binding->>'kind' IS DISTINCT FROM 'web'
           OR indexed_role IS NOT NULL
    ) OR EXISTS (
        SELECT 1 FROM feeds f LEFT JOIN telegram_bindings b ON b.feed = f.id
        WHERE f.adapter = 'telegram_client_adapter'
           OR (f.kind = 'telegram' AND b.feed IS NULL)
    ) THEN
        RAISE EXCEPTION 'Unsupported or unreadable Telegram binding; remove the account-backed feed before upgrading';
    END IF;
END $$;

-- These records only support external Telegram read acknowledgement and account access.
DELETE FROM jobs WHERE kind = 'read';
DELETE FROM telegram_read_members;
DELETE FROM telegram_read_state;
DELETE FROM telegram_profiles;
DELETE FROM state WHERE key LIKE 'telegram-retry:%';

-- Keep historical diagnostics, but retire problems that cannot be recovered any more.
UPDATE reader_problems
SET finished = extract(epoch FROM clock_timestamp()), outcome = 'irrelevant'
WHERE finished IS NULL
  AND ((identity LIKE 'profile:%' AND body::jsonb->>'code' IN
        ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))
       OR (identity LIKE 'job:%' AND body::jsonb->>'operation' = 'producer:read'));
