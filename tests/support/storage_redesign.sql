-- Synthetic target-schema fixture. This is not a release migration.

CREATE TABLE publications (
    id                  text PRIMARY KEY CHECK (id <> ''),
    platform            text NOT NULL CHECK (platform IN (
        'telegram', 'youtube', 'telegraph', 'instagram',
        'twitter', 'other_web', 'rss'
    )),
    external_id         text NOT NULL CHECK (external_id <> ''),
    url                 text NOT NULL CHECK (url <> ''),
    title               text NOT NULL DEFAULT '',
    published_at        timestamptz,
    discovered_at       timestamptz NOT NULL,
    source_order        bigint,
    status              text NOT NULL DEFAULT 'pending_collection'
                        CHECK (status IN (
                            'pending_collection', 'pending_filtering',
                            'filter_out', 'pending_summary', 'ready'
                        )),
    lifecycle           text NOT NULL DEFAULT 'active'
                        CHECK (lifecycle IN ('active', 'deleting')),
    source_deleted      boolean NOT NULL DEFAULT false,
    has_observed_update boolean NOT NULL DEFAULT false,
    content             jsonb NOT NULL DEFAULT '{}'
                        CHECK (jsonb_typeof(content) = 'object'),
    media_ids           text[] NOT NULL DEFAULT ARRAY[]::text[]
                        CHECK (array_position(media_ids, NULL::text) IS NULL),
    filtered_content    jsonb NOT NULL DEFAULT '{}'
                        CHECK (jsonb_typeof(filtered_content) = 'object'),
    summarized_content  jsonb NOT NULL DEFAULT '{}'
                        CHECK (jsonb_typeof(summarized_content) = 'object'),
    processing_metadata jsonb NOT NULL DEFAULT '{}'
                        CHECK (jsonb_typeof(processing_metadata) = 'object'),
    UNIQUE (platform, external_id),
    CHECK (
        lifecycle <> 'deleting'
        OR (
            title = '' AND content = '{}'::jsonb
            AND filtered_content = '{}'::jsonb
            AND summarized_content = '{}'::jsonb
            AND processing_metadata = '{}'::jsonb
            AND cardinality(media_ids) = 0
        )
    )
);

CREATE TABLE jobs (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation          text NOT NULL CHECK (operation IN (
        'discover', 'fetch', 'download_media', 'filter',
        'theses', 'read', 'delete', 'feed-delete'
    )),
    target_id          text NOT NULL CHECK (target_id <> ''),
    feed_id            text,
    status             text NOT NULL DEFAULT 'pending'
                       CHECK (status IN (
                           'blocked', 'pending', 'running', 'done',
                           'failed', 'unavailable', 'cancelled'
                       )),
    generation         bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    attempt_count      bigint NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    manual_once        boolean NOT NULL DEFAULT false,
    next_run_at        timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retry_delay_seconds integer NOT NULL DEFAULT 0
                       CHECK (retry_delay_seconds >= 0),
    wait_scope         text,
    payload            jsonb NOT NULL DEFAULT '{}'
                       CHECK (jsonb_typeof(payload) = 'object'),
    input_snapshot     jsonb CHECK (
                           input_snapshot IS NULL
                           OR jsonb_typeof(input_snapshot) = 'object'
                       ),
    execution_settings jsonb CHECK (
                           execution_settings IS NULL
                           OR jsonb_typeof(execution_settings) = 'object'
                       ),
    queued_at          timestamptz DEFAULT CURRENT_TIMESTAMP,
    started_at         timestamptz,
    finished_at        timestamptz,
    elapsed_seconds    double precision
                       CHECK (elapsed_seconds >= 0
                              AND elapsed_seconds < 'Infinity'::double precision),
    error              jsonb CHECK (
                           error IS NULL OR jsonb_typeof(error) = 'object'
                       ),
    UNIQUE (operation, target_id),
    CHECK (
        status <> 'running'
        OR (generation > 0 AND started_at IS NOT NULL AND finished_at IS NULL)
    )
);

CREATE INDEX publications_processing_candidates
    ON publications (status, discovered_at, id)
    WHERE lifecycle = 'active'
      AND status IN ('pending_collection', 'pending_filtering', 'pending_summary');

CREATE INDEX publications_reading_order
    ON publications (published_at DESC NULLS LAST, id DESC)
    WHERE lifecycle = 'active';

CREATE INDEX jobs_due
    ON jobs (operation, next_run_at, id)
    WHERE status = 'pending';

CREATE INDEX jobs_running
    ON jobs (id)
    WHERE status = 'running';

CREATE INDEX jobs_blocked
    ON jobs (operation, target_id, id)
    WHERE status = 'blocked';

CREATE INDEX jobs_target
    ON jobs (target_id, operation);

CREATE INDEX jobs_feed_status
    ON jobs (feed_id, status, operation, id)
    WHERE feed_id IS NOT NULL;

CREATE TABLE folders (
    id       text PRIMARY KEY CHECK (id <> ''),
    title    text NOT NULL UNIQUE CHECK (btrim(title) <> ''),
    position bigint NOT NULL DEFAULT 0
);

CREATE TABLE feeds (
    id                     text PRIMARY KEY CHECK (id <> ''),
    platform               text NOT NULL CHECK (platform IN (
        'telegram', 'youtube', 'telegraph', 'instagram',
        'twitter', 'other_web', 'rss'
    )),
    external_id            text NOT NULL CHECK (external_id <> ''),
    url                    text NOT NULL CHECK (url <> ''),
    original_urls          text[] NOT NULL DEFAULT ARRAY[]::text[]
                           CHECK (array_position(original_urls, NULL::text) IS NULL),
    title                  text NOT NULL CHECK (btrim(title) <> ''),
    description            text,
    folder_id              text NOT NULL
                           REFERENCES folders(id) ON DELETE RESTRICT,
    sort_order             bigint GENERATED BY DEFAULT AS IDENTITY UNIQUE,
    importance             text NOT NULL DEFAULT 'normal'
                           CHECK (importance IN ('normal', 'elevated', 'high')),
    enabled                boolean NOT NULL DEFAULT true,
    lifecycle              text NOT NULL DEFAULT 'active'
                           CHECK (lifecycle IN ('active', 'deleting')),
    connection_state       text DEFAULT 'needs_connection'
                           CHECK (connection_state IN (
                               'needs_connection', 'connected', 'unavailable', 'ambiguous'
                           )),
    public_ai              boolean NOT NULL DEFAULT false,
    adapter                text NOT NULL CHECK (adapter <> ''),
    config                 jsonb NOT NULL DEFAULT '{}'
                           CHECK (jsonb_typeof(config) = 'object'),
    cursor                 text NOT NULL DEFAULT '',
    interval_seconds       integer CHECK (interval_seconds > 0),
    created_at             timestamptz DEFAULT CURRENT_TIMESTAMP,
    modified_at            timestamptz DEFAULT CURRENT_TIMESTAMP,
    last_discovery_at      timestamptz,
    last_viewed_at         timestamptz,
    last_collection_result jsonb NOT NULL DEFAULT '{}'
                           CHECK (jsonb_typeof(last_collection_result) = 'object'),
    avatar_image           bytea CHECK (octet_length(avatar_image) <= 131072),
    avatar_mime            text,
    avatar_digest          text,
    UNIQUE (platform, external_id),
    CHECK (lifecycle = 'deleting' OR connection_state IS NOT NULL),
    CHECK (
        (avatar_image IS NULL AND avatar_mime IS NULL AND avatar_digest IS NULL)
        OR
        (avatar_image IS NOT NULL AND avatar_mime IS NOT NULL AND avatar_digest IS NOT NULL)
    )
);

CREATE INDEX feeds_folder_order ON feeds (folder_id, sort_order, id);

CREATE TABLE settings (
    folder_id text REFERENCES folders(id) ON DELETE RESTRICT,
    feed_id   text REFERENCES feeds(id) ON DELETE RESTRICT,
    field     text NOT NULL CHECK (field <> ''),
    value     jsonb NOT NULL CHECK (value <> 'null'::jsonb),
    CHECK (folder_id IS NULL OR feed_id IS NULL),
    CONSTRAINT settings_one_value
        UNIQUE NULLS NOT DISTINCT (folder_id, feed_id, field)
);

CREATE INDEX jobs_discovery_schedule
    ON jobs (next_run_at, target_id)
    WHERE operation = 'discover' AND status = 'done';

CREATE TABLE feed_publications (
    feed_id        text NOT NULL REFERENCES feeds(id) ON DELETE RESTRICT,
    publication_id text NOT NULL REFERENCES publications(id) ON DELETE RESTRICT,
    is_primary     boolean NOT NULL DEFAULT false,
    PRIMARY KEY (feed_id, publication_id)
);

CREATE UNIQUE INDEX feed_publications_one_primary
    ON feed_publications (publication_id) WHERE is_primary;

CREATE INDEX feed_publications_by_publication
    ON feed_publications (publication_id, feed_id);

CREATE TABLE publication_links (
    source_publication_id text NOT NULL
        REFERENCES publications(id) ON DELETE RESTRICT,
    target_publication_id text NOT NULL
        REFERENCES publications(id) ON DELETE RESTRICT,
    PRIMARY KEY (source_publication_id, target_publication_id),
    CHECK (source_publication_id <> target_publication_id)
);

CREATE INDEX publication_links_parents
    ON publication_links (target_publication_id, source_publication_id);

CREATE TABLE media_files (
    id                text PRIMARY KEY CHECK (id <> ''),
    publication_id    text NOT NULL
        REFERENCES publications(id) ON DELETE RESTRICT,
    resource_key      text NOT NULL CHECK (resource_key <> ''),
    resource_identity text,
    source_adapter    text NOT NULL CHECK (source_adapter <> ''),
    source_context    jsonb NOT NULL DEFAULT '{}'
                      CHECK (jsonb_typeof(source_context) = 'object'),
    source_locator    text NOT NULL,
    name              text NOT NULL DEFAULT '',
    mime_type         text,
    size_bytes        bigint CHECK (size_bytes >= 0),
    storage_key       text UNIQUE CHECK (storage_key <> ''),
    status            text NOT NULL DEFAULT 'pending_download'
                      CHECK (status IN ('pending_download', 'ready', 'unavailable')),
    reason            text NOT NULL DEFAULT '',
    UNIQUE (publication_id, resource_key),
    CHECK ((status = 'ready') = (storage_key IS NOT NULL)),
    CHECK (status <> 'unavailable' OR reason <> '')
);

CREATE INDEX media_pending_download
    ON media_files (publication_id, id)
    WHERE status = 'pending_download';

CREATE TABLE telegram_profiles (
    role        text PRIMARY KEY CHECK (role IN ('personal', 'collection')),
    session_ref text NOT NULL CHECK (session_ref <> ''),
    user_id     bigint UNIQUE CHECK (user_id > 0),
    generation  bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    state       text NOT NULL DEFAULT 'unavailable'
                CHECK (state IN ('ready', 'unavailable', 'identity_mismatch')),
    checked_at  timestamptz,
    message     text,
    CHECK (state <> 'ready' OR user_id IS NOT NULL)
);

CREATE TABLE telegram_bindings (
    feed_id       text PRIMARY KEY REFERENCES feeds(id) ON DELETE RESTRICT,
    profile_role  text REFERENCES telegram_profiles(role) ON DELETE RESTRICT,
    generation    bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    read_history  boolean NOT NULL DEFAULT false,
    auto_read     boolean,
    CHECK (profile_role IS NOT NULL OR (auto_read IS NULL AND NOT read_history))
);

CREATE TABLE telegram_claim_owners (
    kind        text NOT NULL CHECK (kind IN ('peer', 'username')),
    value       text NOT NULL CHECK (value <> ''),
    feed_id     text NOT NULL REFERENCES feeds(id) ON DELETE RESTRICT,
    evidence    jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    observed_at timestamptz,
    PRIMARY KEY (kind, value)
);

CREATE INDEX telegram_claims_feed ON telegram_claim_owners (feed_id);

CREATE TABLE telegram_read_state (
    feed_id          text PRIMARY KEY
                     REFERENCES telegram_bindings(feed_id) ON DELETE RESTRICT,
    account_user_id  bigint NOT NULL CHECK (account_user_id > 0),
    peer_id          bigint NOT NULL CHECK (peer_id <> 0),
    initial_boundary bigint CHECK (initial_boundary >= 0),
    confirmed        bigint NOT NULL DEFAULT 0 CHECK (confirmed >= 0),
    needs_sync       boolean NOT NULL DEFAULT true,
    waiting_reason   text NOT NULL DEFAULT ''
);

CREATE INDEX telegram_read_pending
    ON telegram_read_state (feed_id) WHERE needs_sync;

CREATE TABLE telegram_read_members (
    feed_id    text NOT NULL
               REFERENCES telegram_read_state(feed_id) ON DELETE RESTRICT,
    message_id bigint NOT NULL CHECK (message_id > 0),
    saved      boolean NOT NULL DEFAULT false,
    viewed     boolean NOT NULL DEFAULT false,
    missing    boolean NOT NULL DEFAULT false,
    PRIMARY KEY (feed_id, message_id)
);

CREATE INDEX telegram_read_candidates
    ON telegram_read_members (feed_id, message_id)
    WHERE NOT missing AND (saved OR viewed);

CREATE TABLE initial_history (
    feed_id            text PRIMARY KEY REFERENCES feeds(id) ON DELETE RESTRICT,
    run_id             uuid NOT NULL UNIQUE,
    requested_count    bigint NOT NULL CHECK (requested_count > 0),
    selection_started  boolean NOT NULL DEFAULT false,
    selection_complete boolean NOT NULL DEFAULT false,
    control_state      text NOT NULL DEFAULT 'active'
                       CHECK (control_state IN ('active', 'paused', 'cancelled')),
    adapter_state      jsonb NOT NULL DEFAULT '{}'
                       CHECK (jsonb_typeof(adapter_state) = 'object'),
    CHECK (NOT selection_complete OR selection_started)
);

CREATE TABLE history_members (
    run_id         uuid NOT NULL REFERENCES initial_history(run_id) ON DELETE RESTRICT,
    publication_id text NOT NULL CHECK (publication_id <> ''),
    outcome        text NOT NULL DEFAULT 'pending'
                   CHECK (outcome IN (
                       'pending', 'retrying', 'loaded',
                       'incomplete', 'unavailable', 'viewed'
                   )),
    PRIMARY KEY (run_id, publication_id)
);

CREATE INDEX history_members_publication ON history_members (publication_id, run_id);

CREATE TABLE state (
    key   text PRIMARY KEY CHECK (key <> ''),
    value jsonb NOT NULL CHECK (value <> 'null'::jsonb)
);

CREATE TABLE youtube_access (
    id            smallint PRIMARY KEY CHECK (id = 1),
    next_at       timestamptz,
    blocked_until timestamptz,
    strikes       integer NOT NULL DEFAULT 0 CHECK (strikes >= 0)
);

CREATE TABLE reader_action_results (
    sequence     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id           text NOT NULL UNIQUE CHECK (id <> ''),
    identity     text NOT NULL CHECK (identity <> ''),
    body         jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    started_at   timestamptz NOT NULL,
    completed_at timestamptz,
    result       text NOT NULL CHECK (result IN (
                     'pending', 'unknown', 'succeeded', 'failed', 'abandoned'
                 ))
);

CREATE INDEX reader_action_latest
    ON reader_action_results (identity, sequence DESC);

CREATE INDEX reader_action_cleanup
    ON reader_action_results (completed_at, sequence)
    WHERE completed_at IS NOT NULL;

CREATE INDEX reader_action_objects
    ON reader_action_results USING gin ((body -> 'objects') jsonb_path_ops);

CREATE TABLE reader_problems (
    id          text PRIMARY KEY CHECK (id <> ''),
    identity    text NOT NULL CHECK (identity <> ''),
    origin      text NOT NULL CHECK (origin IN ('action', 'background')),
    body        jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    created_at  timestamptz NOT NULL,
    finished_at timestamptz,
    outcome     text CHECK (outcome IN ('resolved', 'irrelevant')),
    CHECK ((finished_at IS NULL) = (outcome IS NULL))
);

CREATE UNIQUE INDEX reader_problem_current
    ON reader_problems (identity) WHERE finished_at IS NULL;

CREATE INDEX reader_problem_cleanup
    ON reader_problems (finished_at, id) WHERE finished_at IS NOT NULL;

CREATE INDEX reader_problem_objects
    ON reader_problems USING gin ((body -> 'objects') jsonb_path_ops);

CREATE TABLE oko_schema_migrations (
    version    integer PRIMARY KEY CHECK (version > 0),
    name       text NOT NULL CHECK (name <> ''),
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
