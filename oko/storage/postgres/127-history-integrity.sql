-- Retain the distinction between an absent legacy history and a missing row.
ALTER TABLE feeds ADD COLUMN initial_history_expected boolean NOT NULL DEFAULT false;
UPDATE feeds f SET initial_history_expected=true
    WHERE EXISTS (SELECT 1 FROM initial_history h WHERE h.feed=f.id);

-- A run identity survives malformed selection JSON and dump/restore. Never
-- infer an identity for a selection whose stored run cannot be read.
ALTER TABLE feeds ADD COLUMN initial_history_run text;
ALTER TABLE feeds ADD COLUMN history_replay_after bigint NOT NULL DEFAULT 0;
UPDATE feeds f SET initial_history_run=CASE
    WHEN pg_input_is_valid(h.selection,'jsonb') THEN
        CASE WHEN jsonb_typeof(h.selection::jsonb->'run')='string'
             THEN NULLIF(h.selection::jsonb->>'run','') END
    END
FROM initial_history h WHERE h.feed=f.id;

CREATE TABLE history_deferred_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed text NOT NULL,
    item text NOT NULL,
    run_id text,
    base_outcome text,
    transitions jsonb NOT NULL
);
CREATE INDEX history_deferred_events_feed ON history_deferred_events(feed,id);
CREATE INDEX history_deferred_events_item ON history_deferred_events(feed,item,id);

-- Keep admission checks independent of the size of a healthy history.
CREATE INDEX history_members_invalid_outcome ON history_members(feed,item)
    WHERE outcome IS NULL OR outcome NOT IN ('pending','retrying','unavailable','loaded','incomplete','viewed');
