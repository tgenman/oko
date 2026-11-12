
CREATE TABLE feeds (
    id text NOT NULL,
    identity text,
    title text,
    kind text,
    folder_id text,
    original_urls text[] DEFAULT ARRAY[]::text[] NOT NULL,
    connection text,
    transport text,
    importance text DEFAULT 'normal'::text,
    initial_days bigint DEFAULT 1,
    public_ai bigint DEFAULT 0,
    read_history bigint DEFAULT 0,
    auto_read bigint,
    telegram_peer bigint,
    telegram_account text,
    telegram_username text,
    platform text,
    external_id text,
    url text DEFAULT ''::text NOT NULL,
    description text,
    created_at double precision,
    enabled bigint DEFAULT 1 NOT NULL,
    adapter text DEFAULT ''::text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    cursor text DEFAULT ''::text NOT NULL,
    interval_seconds bigint DEFAULT 1200 NOT NULL,
    sort_order bigint CONSTRAINT feeds_rowid_not_null NOT NULL,
    avatar_image bytea,
    avatar_mime text,
    avatar_digest text,
    last_collection_result jsonb DEFAULT '{}'::jsonb NOT NULL,
    modified_at double precision,
    last_discovery_at double precision,
    last_viewed_at double precision,
    deleted boolean DEFAULT false NOT NULL,
    initial_history_expected boolean DEFAULT false NOT NULL,
    initial_history_run text,
    history_replay_after bigint DEFAULT 0 NOT NULL,
    CONSTRAINT feeds_avatar_complete CHECK ((((avatar_image IS NULL) AND (avatar_mime IS NULL) AND (avatar_digest IS NULL)) OR ((avatar_image IS NOT NULL) AND (avatar_mime IS NOT NULL) AND (avatar_digest IS NOT NULL) AND (octet_length(avatar_image) <= 131072)))),
    CONSTRAINT feeds_config_object CHECK ((jsonb_typeof(config) = 'object'::text)),
    CONSTRAINT feeds_last_collection_result_check CHECK ((jsonb_typeof(last_collection_result) = 'object'::text)),
    CONSTRAINT feeds_original_urls_no_null CHECK ((array_position(original_urls, NULL::text) IS NULL))
);

ALTER TABLE feeds ALTER COLUMN sort_order ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME feeds_rowid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE folders (
    id text NOT NULL,
    title text,
    "position" bigint
);

CREATE TABLE history_deferred_events (
    id bigint NOT NULL,
    feed text NOT NULL,
    item text NOT NULL,
    run_id text,
    base_outcome text,
    transitions jsonb NOT NULL
);

ALTER TABLE history_deferred_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME history_deferred_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE history_members (
    feed text NOT NULL,
    item text NOT NULL,
    outcome text DEFAULT 'pending'::text
);

CREATE TABLE initial_history (
    feed text NOT NULL,
    selection text,
    frozen bigint DEFAULT 0,
    paused bigint DEFAULT 0,
    scan_complete bigint DEFAULT 1
);

CREATE TABLE jobs (
    id bigint NOT NULL,
    kind text NOT NULL,
    item text DEFAULT ''::text,
    status text DEFAULT 'pending'::text NOT NULL,
    attempt bigint DEFAULT 0 NOT NULL,
    next double precision DEFAULT 0,
    generation bigint DEFAULT 0 NOT NULL,
    reason_message text,
    manual_once boolean DEFAULT false,
    reason_code text,
    retry_at double precision,
    retry_delay double precision DEFAULT 0 NOT NULL,
    wait_scope text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    queued_wall double precision,
    feed_id text,
    collection_attempt jsonb,
    execution_settings jsonb,
    input_snapshot jsonb,
    processor_visible boolean DEFAULT false NOT NULL,
    processor_caption boolean DEFAULT false NOT NULL,
    source_event_owned boolean DEFAULT false NOT NULL,
    incomplete_youtube_sensitive boolean DEFAULT false NOT NULL,
    fetch_null_owned boolean DEFAULT false NOT NULL,
    fetch_null_ambiguous boolean DEFAULT false NOT NULL,
    youtube_admission_candidate boolean DEFAULT false NOT NULL,
    youtube_admission_objects jsonb DEFAULT '[]'::jsonb NOT NULL,
    youtube_admission_unknown boolean DEFAULT false NOT NULL,
    youtube_admission_forgotten_item text,
    subtitle_event_owned boolean DEFAULT false NOT NULL,
    identity_event_owned boolean DEFAULT false NOT NULL,
    fetch_youtube_sensitive boolean DEFAULT false NOT NULL,
    media_null_owned boolean DEFAULT false NOT NULL,
    deletion_extra_owned boolean DEFAULT false NOT NULL,
    CONSTRAINT jobs_attempt_nonnegative CHECK ((attempt >= 0)),
    CONSTRAINT jobs_collection_attempt_complete CHECK (((collection_attempt IS NULL) OR ((feed_id IS NOT NULL) AND (jsonb_typeof(collection_attempt) = 'object'::text)))),
    CONSTRAINT jobs_execution_settings_check CHECK (((execution_settings IS NULL) OR COALESCE(((jsonb_typeof(execution_settings) = 'object'::text) AND (jsonb_typeof((execution_settings -> 'generation'::text)) = 'number'::text) AND ((execution_settings ->> 'generation'::text) ~ '^[0-9]+$'::text) AND (jsonb_typeof((execution_settings -> 'parameters'::text)) = 'object'::text)), false))),
    CONSTRAINT jobs_generation_nonnegative CHECK ((generation >= 0)),
    CONSTRAINT jobs_input_snapshot_check CHECK (((input_snapshot IS NULL) OR COALESCE(((jsonb_typeof(input_snapshot) = 'object'::text) AND (jsonb_typeof((input_snapshot -> 'input_hash'::text)) = 'string'::text) AND ((input_snapshot ->> 'input_hash'::text) <> ''::text)), false))),
    CONSTRAINT jobs_kind_allowed CHECK ((kind = ANY (ARRAY['discover'::text, 'fetch'::text, 'rebuild'::text, 'download_media'::text, 'filter'::text, 'theses'::text, 'read'::text, 'delete'::text, 'feed-delete'::text]))),
    CONSTRAINT jobs_payload_object CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT jobs_retry_delay_nonnegative CHECK ((retry_delay >= (0)::double precision)),
    CONSTRAINT jobs_status_allowed CHECK ((status = ANY (ARRAY['blocked'::text, 'pending'::text, 'running'::text, 'done'::text, 'failed'::text, 'unavailable'::text, 'cancelled'::text])))
);

ALTER TABLE jobs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE media_files (
    id text NOT NULL,
    publication_id text NOT NULL,
    resource_key text NOT NULL,
    source_locator text NOT NULL,
    name text NOT NULL,
    mime_type text,
    size_bytes bigint,
    storage_key text,
    status text NOT NULL,
    resource_identity text,
    source_url text
);

CREATE TABLE publication_links (
    source_publication_id text NOT NULL,
    target_publication_id text NOT NULL,
    rowid bigint NOT NULL
);

ALTER TABLE publication_links ALTER COLUMN rowid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME publication_links_rowid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE publications (
    id text CONSTRAINT items_id_not_null NOT NULL,
    url text,
    platform text NOT NULL,
    external_id text NOT NULL,
    title text DEFAULT ''::text CONSTRAINT items_title_not_null NOT NULL,
    published_at double precision,
    discovered_at double precision DEFAULT EXTRACT(epoch FROM clock_timestamp()) NOT NULL,
    content jsonb DEFAULT '{}'::jsonb CONSTRAINT items_content_not_null NOT NULL,
    media_ids text DEFAULT '[]'::text CONSTRAINT items_media_ids_not_null NOT NULL,
    filtered_content text DEFAULT '{}'::text CONSTRAINT items_filtered_content_not_null NOT NULL,
    summarized_content text DEFAULT '{}'::text CONSTRAINT items_summarized_content_not_null NOT NULL,
    processing_metadata text DEFAULT '{}'::text CONSTRAINT items_processing_metadata_not_null NOT NULL,
    status text DEFAULT 'pending_collection'::text CONSTRAINT items_status_not_null NOT NULL,
    feed_id text,
    viewed boolean DEFAULT false NOT NULL,
    CONSTRAINT publications_status_allowed CHECK ((status = ANY (ARRAY['pending_collection'::text, 'pending_filtering'::text, 'filter_out'::text, 'pending_summary'::text, 'ready'::text])))
);

CREATE TABLE reader_action_results (
    sequence bigint NOT NULL,
    id text NOT NULL,
    identity text NOT NULL,
    body text NOT NULL,
    started double precision NOT NULL,
    completed double precision,
    result text NOT NULL
);

ALTER TABLE reader_action_results ALTER COLUMN sequence ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME reader_action_results_sequence_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE reader_problems (
    id text NOT NULL,
    identity text NOT NULL,
    origin text NOT NULL,
    body text NOT NULL,
    created double precision NOT NULL,
    finished double precision,
    outcome text,
    incomplete_due double precision,
    incomplete_future text,
    incomplete_closes boolean DEFAULT false NOT NULL,
    youtube_admission_due double precision,
    subtitle_due double precision,
    subtitle_reserved_owned boolean DEFAULT false NOT NULL,
    youtube_fetch_due double precision,
    youtube_fetch_reserved_owned boolean DEFAULT false NOT NULL
);

CREATE TABLE settings (
    folder_id text,
    feed_id text,
    field text CONSTRAINT settings_new_field_not_null NOT NULL,
    value jsonb CONSTRAINT settings_new_value_not_null NOT NULL,
    CONSTRAINT settings_new_check CHECK (((folder_id IS NULL) OR (feed_id IS NULL))),
    CONSTRAINT settings_new_field_check CHECK ((field <> ''::text)),
    CONSTRAINT settings_new_value_check CHECK ((value <> 'null'::jsonb))
);

CREATE TABLE state (
    key text NOT NULL,
    value text
);

CREATE TABLE telegram_bindings (
    feed text NOT NULL,
    binding jsonb,
    indexed_role text,
    binding_raw text,
    CONSTRAINT telegram_binding_one_representation CHECK (((binding IS NOT NULL) <> (binding_raw IS NOT NULL)))
);

CREATE TABLE telegram_claim_owners (
    kind text NOT NULL,
    value text NOT NULL,
    feed text NOT NULL,
    evidence text
);

CREATE TABLE telegram_profiles (
    role text NOT NULL,
    account text,
    user_id bigint,
    generation bigint NOT NULL,
    state text,
    checked double precision,
    message text
);

CREATE TABLE telegram_read_members (
    feed text NOT NULL,
    member bigint NOT NULL,
    missing bigint DEFAULT 0,
    saved boolean DEFAULT false NOT NULL,
    viewed boolean DEFAULT false NOT NULL
);

CREATE TABLE telegram_read_state (
    feed text NOT NULL,
    since double precision,
    initial bigint,
    confirmed bigint DEFAULT 0,
    signature text,
    waiting text,
    error text,
    needs_sync boolean DEFAULT false NOT NULL,
    saved_backfill_after text
);

CREATE TABLE youtube_access (
    id bigint NOT NULL,
    next_at double precision DEFAULT 0,
    blocked_until double precision DEFAULT 0,
    strikes bigint DEFAULT 0,
    processor_reservation_id text,
    youtube_admission_problem_id text,
    youtube_admission_recovered boolean DEFAULT false NOT NULL,
    CONSTRAINT youtube_access_id_check CHECK ((id = 1))
);

ALTER TABLE ONLY feeds
    ADD CONSTRAINT feeds_identity_key UNIQUE (identity);

ALTER TABLE ONLY feeds
    ADD CONSTRAINT feeds_pkey PRIMARY KEY (id);

ALTER TABLE ONLY feeds
    ADD CONSTRAINT feeds_rowid_key UNIQUE (sort_order);

ALTER TABLE ONLY folders
    ADD CONSTRAINT folders_pkey PRIMARY KEY (id);

ALTER TABLE ONLY folders
    ADD CONSTRAINT folders_title_key UNIQUE (title);

ALTER TABLE ONLY history_deferred_events
    ADD CONSTRAINT history_deferred_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY history_members
    ADD CONSTRAINT history_members_pkey PRIMARY KEY (feed, item);

ALTER TABLE ONLY initial_history
    ADD CONSTRAINT initial_history_pkey PRIMARY KEY (feed);

ALTER TABLE ONLY publications
    ADD CONSTRAINT items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY jobs
    ADD CONSTRAINT jobs_kind_item_key UNIQUE (kind, item);

ALTER TABLE ONLY jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY media_files
    ADD CONSTRAINT media_files_pkey PRIMARY KEY (id);

ALTER TABLE ONLY media_files
    ADD CONSTRAINT media_files_publication_id_resource_key_key UNIQUE (publication_id, resource_key);

ALTER TABLE ONLY publication_links
    ADD CONSTRAINT publication_links_pkey PRIMARY KEY (source_publication_id, target_publication_id);

ALTER TABLE ONLY publication_links
    ADD CONSTRAINT publication_links_rowid_key UNIQUE (rowid);

ALTER TABLE ONLY reader_action_results
    ADD CONSTRAINT reader_action_results_id_key UNIQUE (id);

ALTER TABLE ONLY reader_action_results
    ADD CONSTRAINT reader_action_results_pkey PRIMARY KEY (sequence);

ALTER TABLE ONLY reader_problems
    ADD CONSTRAINT reader_problems_pkey PRIMARY KEY (id);

ALTER TABLE ONLY settings
    ADD CONSTRAINT settings_one_value UNIQUE NULLS NOT DISTINCT (folder_id, feed_id, field);

ALTER TABLE ONLY state
    ADD CONSTRAINT state_pkey PRIMARY KEY (key);

ALTER TABLE ONLY telegram_bindings
    ADD CONSTRAINT telegram_bindings_pkey PRIMARY KEY (feed);

ALTER TABLE ONLY telegram_claim_owners
    ADD CONSTRAINT telegram_claim_owners_pkey PRIMARY KEY (kind, value, feed);

ALTER TABLE ONLY telegram_profiles
    ADD CONSTRAINT telegram_profiles_pkey PRIMARY KEY (role);

ALTER TABLE ONLY telegram_read_members
    ADD CONSTRAINT telegram_read_members_pkey PRIMARY KEY (feed, member);

ALTER TABLE ONLY telegram_read_state
    ADD CONSTRAINT telegram_read_state_pkey PRIMARY KEY (feed);

ALTER TABLE ONLY youtube_access
    ADD CONSTRAINT youtube_access_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX feed_external_key ON feeds USING btree (platform, external_id) WHERE (platform IS NOT NULL);

CREATE INDEX feeds_folder ON feeds USING btree (folder_id);

CREATE INDEX feeds_incomplete_youtube ON feeds USING btree (id) WHERE (kind = 'youtube'::text);

CREATE INDEX feeds_youtube_recovery ON feeds USING btree (id) WHERE (kind = 'youtube'::text);

CREATE INDEX history_deferred_events_feed ON history_deferred_events USING btree (feed, id);

CREATE INDEX history_deferred_events_item ON history_deferred_events USING btree (feed, item, id);

CREATE INDEX history_members_invalid_outcome ON history_members USING btree (feed, item) WHERE ((outcome IS NULL) OR (outcome <> ALL (ARRAY['pending'::text, 'retrying'::text, 'unavailable'::text, 'loaded'::text, 'incomplete'::text, 'viewed'::text])));

CREATE INDEX history_members_item ON history_members USING btree (item);

CREATE INDEX jobs_active ON jobs USING btree (status, kind, item) WHERE ((status = ANY (ARRAY['pending'::text, 'running'::text])) OR (reason_message IS NOT NULL));

CREATE INDEX jobs_cleanup_feed ON jobs USING btree (((payload ->> 'cleanup_feed'::text)), item) WHERE (kind = 'delete'::text);

CREATE INDEX jobs_collection_attempts_by_feed ON jobs USING btree (feed_id) WHERE (collection_attempt IS NOT NULL);

CREATE INDEX jobs_delete_problem_recovery ON jobs USING btree (id) WHERE ((kind = ANY (ARRAY['delete'::text, 'feed-delete'::text])) AND ((reason_message IS NOT NULL) OR (reason_code IS NOT NULL)) AND (COALESCE(NULLIF(reason_code, ''::text), ('work-'::text || kind)) = ANY (ARRAY['delete'::text, 'feed-delete'::text, 'work-delete'::text, 'work-feed-delete'::text])));

CREATE INDEX jobs_deletion_extra_recovery ON jobs USING btree (id) WHERE ((kind = ANY (ARRAY['delete'::text, 'feed-delete'::text])) AND ((reason_message IS NOT NULL) OR (reason_code IS NOT NULL)));

CREATE INDEX jobs_discover_problem_feed ON jobs USING btree (feed_id, item, id) WHERE ((kind = 'discover'::text) AND ((reason_message IS NOT NULL) OR (reason_code IS NOT NULL)));

CREATE INDEX jobs_discover_problem_recovery ON jobs USING btree (id) WHERE ((kind = 'discover'::text) AND ((reason_message IS NOT NULL) OR (reason_code IS NOT NULL)));

CREATE INDEX jobs_fetch_feed_wait ON jobs USING btree (feed_id, item) WHERE ((kind = 'fetch'::text) AND (status = ANY (ARRAY['pending'::text, 'running'::text])) AND (reason_code = ANY (ARRAY[''::text, 'work-fetch'::text])));

CREATE INDEX jobs_fetch_identity_feed ON jobs USING btree (md5(feed_id), id) WHERE ((kind = 'fetch'::text) AND (feed_id IS NOT NULL) AND (reason_code = 'identity-unconfirmed'::text));

CREATE INDEX jobs_fetch_identity_recovery ON jobs USING btree (id) WHERE ((kind = 'fetch'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code = 'identity-unconfirmed'::text));

CREATE INDEX jobs_fetch_null_recovery ON jobs USING btree (id) WHERE ((kind = 'fetch'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NULL) AND (reason_message IS NOT NULL));

CREATE INDEX jobs_fetch_problem_recovery ON jobs USING btree (id) WHERE ((kind = 'fetch'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NOT NULL) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])));

CREATE INDEX jobs_fetch_without_reason ON jobs USING btree (id) WHERE ((kind = 'fetch'::text) AND (status = ANY (ARRAY['failed'::text, 'unavailable'::text])) AND (reason_code IS NULL) AND (reason_message IS NULL));

CREATE INDEX jobs_incomplete_caption_pending ON jobs USING btree (item) WHERE ((kind = 'download_media'::text) AND (status = 'pending'::text) AND (reason_code IS NULL));

CREATE INDEX jobs_incomplete_profile_event ON jobs USING btree (feed_id, kind, item) WHERE (reason_code = ANY (ARRAY['identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text]));

CREATE INDEX jobs_incomplete_youtube_fetch_error ON jobs USING btree (item) WHERE ((kind = 'fetch'::text) AND (reason_code IS NULL) AND (reason_message IS NOT NULL) AND (status = ANY (ARRAY['pending'::text, 'running'::text, 'failed'::text, 'unavailable'::text, 'blocked'::text])));

CREATE INDEX jobs_incomplete_youtube_media_error ON jobs USING btree (item) WHERE ((kind = 'download_media'::text) AND (reason_code IS NULL) AND (reason_message IS NOT NULL) AND (status = ANY (ARRAY['pending'::text, 'running'::text, 'failed'::text, 'unavailable'::text, 'blocked'::text])));

CREATE INDEX jobs_incomplete_youtube_sensitive ON jobs USING btree (item) WHERE ((kind = 'fetch'::text) AND incomplete_youtube_sensitive);

CREATE INDEX jobs_item ON jobs USING btree (item);

CREATE INDEX jobs_media_null_recovery ON jobs USING btree (id) WHERE ((kind = 'download_media'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NULL) AND (reason_message IS NOT NULL));

CREATE INDEX jobs_media_problem_recovery ON jobs USING btree (id) WHERE ((kind = 'download_media'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NOT NULL) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])));

CREATE INDEX jobs_media_without_reason ON jobs USING btree (id) WHERE ((kind = 'download_media'::text) AND (status = ANY (ARRAY['failed'::text, 'unavailable'::text])) AND (reason_code IS NULL) AND (reason_message IS NULL));

CREATE INDEX jobs_pending_kind_item ON jobs USING btree (kind, item, next, id) WHERE (status = 'pending'::text);

CREATE INDEX jobs_pending_next ON jobs USING btree (next, kind, item) WHERE (status = 'pending'::text);

CREATE INDEX jobs_problems ON jobs USING btree (kind, item) WHERE ((reason_code IS NOT NULL) OR (reason_message IS NOT NULL));

CREATE INDEX jobs_processing_input_backfill ON jobs USING btree (kind, id) WHERE ((input_snapshot IS NOT NULL) AND ((payload ->> 'input_revision'::text) IS NULL));

CREATE INDEX jobs_processing_problem_recovery ON jobs USING btree (id) WHERE ((kind = ANY (ARRAY['filter'::text, 'theses'::text])) AND (item IS NOT NULL) AND (item <> ''::text) AND ((reason_code = ''::text) OR ((reason_code IS NULL) AND (reason_message IS NOT NULL)) OR ((reason_code IS NOT NULL) AND (reason_code <> ''::text) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])))));

CREATE INDEX jobs_processing_without_reason ON jobs USING btree (kind, id) WHERE ((kind = ANY (ARRAY['filter'::text, 'theses'::text])) AND (status = ANY (ARRAY['failed'::text, 'unavailable'::text])) AND (reason_code IS NULL) AND (reason_message IS NULL));

CREATE INDEX jobs_processor_code ON jobs USING btree (id) WHERE (reason_code = 'processor-config'::text);

CREATE INDEX jobs_processor_visible ON jobs USING btree (processor_caption, kind, item) WHERE processor_visible;

CREATE INDEX jobs_read_problem_recovery ON jobs USING btree (id) WHERE ((kind = 'read'::text) AND ((reason_message IS NOT NULL) OR (reason_code IS NOT NULL)));

CREATE INDEX jobs_reader_deadlines ON jobs USING btree (next) WHERE ((status = 'pending'::text) AND (kind = ANY (ARRAY['fetch'::text, 'filter'::text, 'theses'::text])));

CREATE INDEX jobs_retired_media_cleanup ON jobs USING btree (id) WHERE ((kind = 'download_media'::text) AND (payload ? 'retired_media_key'::text));

CREATE INDEX jobs_running ON jobs USING btree (kind, item) WHERE (status = 'running'::text);

CREATE INDEX jobs_running_delete_problem ON jobs USING btree (id) WHERE ((kind = ANY (ARRAY['delete'::text, 'feed-delete'::text])) AND (status = 'running'::text) AND ((reason_message IS NOT NULL) OR (reason_code IS NOT NULL)) AND (COALESCE(NULLIF(reason_code, ''::text), ('work-'::text || kind)) = ANY (ARRAY['delete'::text, 'feed-delete'::text, 'work-delete'::text, 'work-feed-delete'::text])));

CREATE INDEX jobs_subtitle_event_owned ON jobs USING btree (id) WHERE ((kind = 'download_media'::text) AND subtitle_event_owned);

CREATE INDEX jobs_subtitle_profile_recovery ON jobs USING btree (id) WHERE ((kind = 'download_media'::text) AND (status = ANY (ARRAY['pending'::text, 'running'::text, 'failed'::text, 'unavailable'::text, 'blocked'::text])) AND (reason_code = ANY (ARRAY['identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])));

CREATE INDEX jobs_subtitle_unowned ON jobs USING btree (item) WHERE ((kind = 'download_media'::text) AND (NOT subtitle_event_owned) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NOT NULL) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])));

CREATE INDEX jobs_unmanaged_problems ON jobs USING btree (kind, item) WHERE ((kind IS DISTINCT FROM 'read'::text) AND (kind IS DISTINCT FROM 'discover'::text) AND (reason_code IS DISTINCT FROM 'processor-config'::text) AND (NOT ((kind = ANY (ARRAY['delete'::text, 'feed-delete'::text])) AND (COALESCE(NULLIF(reason_code, ''::text), ('work-'::text || kind)) = ANY (ARRAY['delete'::text, 'feed-delete'::text, 'work-delete'::text, 'work-feed-delete'::text])))) AND (NOT ((NOT (kind IS DISTINCT FROM 'fetch'::text)) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NOT NULL) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])) AND (NOT fetch_null_ambiguous))) AND ((reason_code IS NOT NULL) OR (reason_message IS NOT NULL)) AND (NOT ((NOT (kind IS DISTINCT FROM 'download_media'::text)) AND (item IS NOT NULL) AND (item <> ''::text) AND (reason_code IS NOT NULL) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])))) AND (NOT (((NOT (kind IS DISTINCT FROM 'filter'::text)) OR (NOT (kind IS DISTINCT FROM 'theses'::text))) AND (item IS NOT NULL) AND (item <> ''::text) AND ((reason_code = ''::text) OR ((reason_code IS NULL) AND (reason_message IS NOT NULL)) OR ((reason_code IS NOT NULL) AND (reason_code <> ''::text) AND (reason_code !~~ 'source-%'::text) AND (reason_code <> ALL (ARRAY['codex-choice'::text, 'ai-permission'::text, 'youtube-admission'::text, 'processor-config'::text, 'identity-unconfirmed'::text, 'profile_not_configured'::text, 'profile_unavailable'::text, 'account_identity_mismatch'::text])))))) AND (NOT ((kind = ANY (ARRAY['fetch'::text, 'download_media'::text, 'filter'::text, 'theses'::text, 'delete'::text, 'feed-delete'::text])) AND (reason_code IS NOT NULL) AND (reason_code ~~ 'source-%'::text) AND source_event_owned)) AND (NOT ((NOT (kind IS DISTINCT FROM 'fetch'::text)) AND (reason_code IS NULL) AND (reason_message IS NOT NULL) AND fetch_null_owned)) AND (reason_code IS DISTINCT FROM 'youtube-admission'::text) AND (NOT ((NOT (kind IS DISTINCT FROM 'download_media'::text)) AND subtitle_event_owned)) AND (NOT ((NOT (kind IS DISTINCT FROM 'fetch'::text)) AND (reason_code = 'identity-unconfirmed'::text) AND identity_event_owned)));

CREATE INDEX jobs_youtube_admission_candidate ON jobs USING btree (id) WHERE youtube_admission_candidate;

CREATE INDEX jobs_youtube_admission_feed ON jobs USING btree (md5(feed_id), id) WHERE ((feed_id IS NOT NULL) AND (kind = ANY (ARRAY['fetch'::text, 'download_media'::text])) AND ((status = ANY (ARRAY['pending'::text, 'running'::text])) OR youtube_admission_candidate));

CREATE INDEX jobs_youtube_admission_objects ON jobs USING gin (youtube_admission_objects jsonb_path_ops) WHERE youtube_admission_candidate;

CREATE INDEX jobs_youtube_admission_recovery ON jobs USING btree (id) WHERE ((kind = ANY (ARRAY['fetch'::text, 'download_media'::text])) AND (status = ANY (ARRAY['pending'::text, 'running'::text])));

CREATE INDEX jobs_youtube_admission_unknown ON jobs USING btree (id) WHERE youtube_admission_unknown;

CREATE INDEX jobs_youtube_fetch_sensitive ON jobs USING btree (id) WHERE ((kind = 'fetch'::text) AND fetch_youtube_sensitive);

CREATE INDEX jobs_youtube_fetch_sensitive_item ON jobs USING btree (md5(item), id) WHERE ((kind = 'fetch'::text) AND fetch_youtube_sensitive);

CREATE INDEX jobs_youtube_null_feed ON jobs USING btree (md5(feed_id), id) WHERE ((kind = 'fetch'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (feed_id IS NOT NULL) AND (status = ANY (ARRAY['pending'::text, 'running'::text, 'failed'::text, 'unavailable'::text, 'blocked'::text])) AND (reason_code IS NULL) AND (reason_message IS NOT NULL));

CREATE INDEX jobs_youtube_null_item ON jobs USING btree (md5(item), id) WHERE ((kind = 'fetch'::text) AND (item IS NOT NULL) AND (item <> ''::text) AND (status = ANY (ARRAY['pending'::text, 'running'::text, 'failed'::text, 'unavailable'::text, 'blocked'::text])) AND (reason_code IS NULL) AND (reason_message IS NOT NULL));

CREATE INDEX media_subtitle_problem_lookup ON media_files USING btree (id) WHERE (resource_key = 'youtube:subtitles'::text);

CREATE UNIQUE INDEX publication_external_key ON publications USING btree (platform, external_id) WHERE (platform IS NOT NULL);

CREATE INDEX publication_links_target ON publication_links USING btree (target_publication_id, source_publication_id);

CREATE INDEX publication_stage ON publications USING btree (status, id) WHERE (platform IS NOT NULL);

CREATE INDEX publications_feed ON publications USING btree (feed_id, id) WHERE (feed_id IS NOT NULL);

CREATE INDEX publications_order ON publications USING btree (published_at DESC, id);

CREATE INDEX reader_action_expiry ON reader_action_results USING btree (completed, sequence) WHERE (completed IS NOT NULL);

CREATE INDEX reader_action_identity_sequence ON reader_action_results USING btree (identity, sequence);

CREATE INDEX reader_action_objects_gin ON reader_action_results USING gin ((((body)::jsonb -> 'objects'::text)) jsonb_path_ops);

CREATE INDEX reader_action_unresolved ON reader_action_results USING btree (result, identity, sequence) WHERE (result = ANY (ARRAY['failed'::text, 'unknown'::text, 'pending'::text]));

CREATE INDEX reader_delete_problem_identity ON reader_problems USING btree ((((body)::jsonb ->> 'deletion_job_identity'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = ANY (ARRAY['producer:delete'::text, 'producer:feed-delete'::text])));


CREATE INDEX reader_discover_problem_objects_gin ON reader_problems USING gin ((((body)::jsonb -> 'objects'::text)) jsonb_path_ops) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:discover'::text));


CREATE INDEX reader_fetch_problem_identity ON reader_problems USING btree ((((body)::jsonb ->> 'fetch_job_identity'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:fetch'::text));


CREATE INDEX reader_fetch_status_identity ON reader_problems USING btree ((((body)::jsonb ->> 'status_job_identity'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:fetch'::text));

CREATE INDEX reader_fetch_work_job_owner ON reader_problems USING btree ((((body)::jsonb ->> 'fetch_job_id'::text)) COLLATE "C", id COLLATE "C") WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:fetch'::text) AND (((body)::jsonb ->> 'code'::text) = 'work-fetch'::text) AND (((body)::jsonb ->> 'fetch_job_identity'::text) IS NOT NULL) AND (((body)::jsonb ->> 'fetch_job_id'::text) IS NOT NULL));




CREATE INDEX reader_incomplete_current_due ON reader_problems USING btree (incomplete_due) WHERE ((finished IS NULL) AND (incomplete_due IS NOT NULL));

CREATE INDEX reader_incomplete_due ON reader_problems USING btree (incomplete_due, id COLLATE "C") WHERE (incomplete_due IS NOT NULL);

CREATE INDEX reader_incomplete_group_isolated ON reader_problems USING btree ((((body)::jsonb ->> 'incomplete_group_identity'::text)), created, id COLLATE "C") WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:incomplete'::text));


CREATE INDEX reader_incomplete_objects_gin ON reader_problems USING gin ((((body)::jsonb -> 'objects'::text)) jsonb_path_ops) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'code'::text) = 'material-incomplete'::text));

CREATE INDEX reader_incomplete_reservations ON reader_problems USING btree ((((body)::jsonb ->> 'incomplete_group_identity'::text)), id COLLATE "C") WHERE ((finished IS NOT NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:incomplete-reservation'::text));

CREATE INDEX reader_incomplete_reservations_due ON reader_problems USING btree (finished, id COLLATE "C") WHERE ((finished IS NOT NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:incomplete-reservation'::text));

CREATE INDEX reader_incomplete_virtual_expiry ON reader_problems USING btree (incomplete_due) WHERE ((finished IS NULL) AND incomplete_closes);


CREATE INDEX reader_media_problem_identity ON reader_problems USING btree ((((body)::jsonb ->> 'media_job_identity'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:download_media'::text));

CREATE INDEX reader_media_problem_job ON reader_problems USING btree ((((body)::jsonb ->> 'media_job_id'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:download_media'::text));


CREATE INDEX reader_media_status_identity ON reader_problems USING btree ((((body)::jsonb ->> 'status_job_identity'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:download_media'::text));


CREATE INDEX reader_problem_expiry ON reader_problems USING btree (finished, id) WHERE (finished IS NOT NULL);

CREATE INDEX reader_problem_nonreserve_expiry ON reader_problems USING btree (finished, id) WHERE ((finished IS NOT NULL) AND (incomplete_due IS NULL));

CREATE INDEX reader_problem_objects_gin ON reader_problems USING gin ((((body)::jsonb -> 'objects'::text)) jsonb_path_ops);

CREATE UNIQUE INDEX reader_problems_current ON reader_problems USING btree (identity) WHERE (finished IS NULL);

CREATE INDEX reader_processing_problem_identity ON reader_problems USING btree ((((body)::jsonb ->> 'processing_job_identity'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = ANY (ARRAY['producer:filter'::text, 'producer:theses'::text])));

CREATE INDEX reader_processing_problem_job ON reader_problems USING btree ((((body)::jsonb ->> 'processing_job_id'::text))) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = ANY (ARRAY['producer:filter'::text, 'producer:theses'::text])));


CREATE INDEX reader_processor_group_isolated ON reader_problems USING btree ((((body)::jsonb ->> 'processor_group_identity'::text)), created, id COLLATE "C") WHERE ((created >= ('-62135596800'::bigint)::double precision) AND (created < ('253402300800'::bigint)::double precision) AND (finished IS NULL) AND (((body)::jsonb ->> 'code'::text) = 'processor-config'::text) AND (jsonb_typeof(((body)::jsonb -> 'title'::text)) = 'string'::text) AND (jsonb_typeof(((body)::jsonb -> 'consequence'::text)) = 'string'::text) AND (jsonb_typeof(((body)::jsonb -> 'next_step'::text)) = 'string'::text) AND (jsonb_typeof(((body)::jsonb -> 'operation'::text)) = 'string'::text) AND (((body)::jsonb ->> 'state'::text) = ANY (ARRAY['current'::text, 'retrying'::text])) AND ((((body)::jsonb -> 'next_at'::text) IS NULL) OR (jsonb_typeof(((body)::jsonb -> 'next_at'::text)) = 'null'::text)) AND ((NOT ((body)::jsonb ? 'reason'::text)) OR (jsonb_typeof(((body)::jsonb -> 'reason'::text)) = 'string'::text)) AND ((NOT ((body)::jsonb ? 'request_id'::text)) OR ((jsonb_typeof(((body)::jsonb -> 'request_id'::text)) = 'string'::text) AND ((((body)::jsonb ->> 'request_id'::text) COLLATE "C") ~ '^[A-Za-z0-9_-]{1,100}$'::text))) AND (jsonb_typeof(((body)::jsonb -> 'objects'::text)) = 'array'::text) AND (NOT ((body)::jsonb @? '$."objects"[*]?((((((@.type() != "object" || !(exists (@."kind"))) || @."kind".type() != "string") || !(exists (@."id"))) || @."id".type() != "string") || @."id" == "") || ((@."kind" != "settings" && @."kind" != "feed") && @."kind" != "folder") && @."kind" != "material")'::jsonpath)) AND (NOT ((body)::jsonb @? '$."objects"[*].keyvalue()?((@."key" != "kind" && @."key" != "id") && @."key" != "label")'::jsonpath)));

CREATE INDEX reader_read_problem_objects_gin ON reader_problems USING gin ((((body)::jsonb -> 'objects'::text)) jsonb_path_ops) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:read'::text));


CREATE INDEX reader_source_group_identity ON reader_problems USING btree ((((body)::jsonb ->> 'source_group_identity'::text))) WHERE ((finished IS NULL) AND (octet_length(((body)::jsonb ->> 'source_group_identity'::text)) <= 1000));

CREATE INDEX reader_subtitle_due ON reader_problems USING btree (subtitle_due, id COLLATE "C") WHERE ((subtitle_due IS NOT NULL) AND subtitle_reserved_owned);


CREATE INDEX reader_subtitle_media_isolated_paged ON reader_problems USING btree (md5(((body)::jsonb ->> 'media_job_identity'::text)), md5(id)) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:download_media'::text) AND (((body)::jsonb ->> 'media_job_identity'::text) IS NOT NULL));


CREATE INDEX reader_subtitle_reservation_job ON reader_problems USING btree (md5(((body)::jsonb ->> 'media_job_id'::text)), md5(((body)::jsonb ->> 'subtitle_canonical'::text))) WHERE ((subtitle_due IS NOT NULL) AND subtitle_reserved_owned AND (identity ~~ 'subtitle:reservation:%'::text));

CREATE INDEX reader_subtitle_status_isolated_paged ON reader_problems USING btree (md5(((body)::jsonb ->> 'status_job_identity'::text)), md5(id)) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:download_media'::text) AND (((body)::jsonb ->> 'status_job_identity'::text) IS NOT NULL));

CREATE INDEX reader_youtube_admission_due ON reader_problems USING btree (youtube_admission_due, id COLLATE "C") WHERE ((finished IS NULL) AND (youtube_admission_due IS NOT NULL));

CREATE INDEX reader_youtube_fetch_due ON reader_problems USING btree (youtube_fetch_due, id COLLATE "C") WHERE ((youtube_fetch_due IS NOT NULL) AND youtube_fetch_reserved_owned);

CREATE INDEX reader_youtube_fetch_isolated_paged ON reader_problems USING btree (md5(((body)::jsonb ->> 'fetch_job_identity'::text)), md5(id)) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:fetch'::text) AND (((body)::jsonb ->> 'fetch_job_identity'::text) IS NOT NULL));

CREATE INDEX reader_youtube_fetch_reservation_job ON reader_problems USING btree (md5(((body)::jsonb ->> 'fetch_job_id'::text)), md5(((body)::jsonb ->> 'fetch_youtube_canonical'::text))) WHERE ((youtube_fetch_due IS NOT NULL) AND youtube_fetch_reserved_owned AND (identity ~~ 'youtube-fetch:reservation:%'::text));

CREATE INDEX reader_youtube_fetch_status_paged ON reader_problems USING btree (md5(((body)::jsonb ->> 'status_job_identity'::text)), md5(id)) WHERE ((finished IS NULL) AND (((body)::jsonb ->> 'operation'::text) = 'producer:fetch'::text) AND (((body)::jsonb ->> 'status_job_identity'::text) IS NOT NULL));

CREATE INDEX source_group_jobs_feed ON jobs USING btree (kind, feed_id, reason_code) WHERE ((reason_code ~~ 'source-%'::text) AND (octet_length(reason_code) <= 1000) AND (COALESCE(octet_length(feed_id), 0) <= 1000));

CREATE INDEX source_recovery_markers ON state USING btree (key text_pattern_ops) WHERE (key ~~ 'source-recovery-seen:%'::text);

CREATE INDEX source_unowned_recovery ON jobs USING btree (id) WHERE ((kind = ANY (ARRAY['fetch'::text, 'download_media'::text, 'filter'::text, 'theses'::text, 'delete'::text, 'feed-delete'::text])) AND (reason_code ~~ 'source-%'::text) AND (source_event_owned = false));

CREATE INDEX source_youtube_timed ON state USING btree (key COLLATE "C") WHERE (key ~~ 'source-youtube-timed:%'::text);

CREATE INDEX state_fetch_null_protected ON state USING btree (key COLLATE "C") WHERE (((key COLLATE "C") >= 'fetch-null-protected:'::text) AND ((key COLLATE "C") < 'fetch-null-protected;'::text));

CREATE INDEX state_subtitle_reservation ON state USING btree (key COLLATE "C") WHERE (((key COLLATE "C") >= 'subtitle:reservation:'::text) AND ((key COLLATE "C") < 'subtitle:reservation;'::text));

CREATE INDEX state_youtube_fetch_reservation ON state USING btree (key COLLATE "C") WHERE (((key COLLATE "C") >= 'youtube-fetch:reservation:'::text) AND ((key COLLATE "C") < 'youtube-fetch:reservation;'::text));

CREATE INDEX telegram_bindings_role_feed ON telegram_bindings USING btree (indexed_role, feed);

CREATE INDEX telegram_read_candidates ON telegram_read_members USING btree (feed, member) WHERE ((missing = 0) AND (saved OR viewed));

CREATE INDEX telegram_read_needs_sync ON telegram_read_state USING btree (feed) WHERE needs_sync;

CREATE INDEX telegram_saved_backfill ON telegram_read_state USING btree (feed) WHERE (saved_backfill_after IS NOT NULL);

ALTER TABLE ONLY media_files
    ADD CONSTRAINT media_files_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES publications(id);

ALTER TABLE ONLY publication_links
    ADD CONSTRAINT publication_links_source_publication_id_fkey FOREIGN KEY (source_publication_id) REFERENCES publications(id);

ALTER TABLE ONLY publication_links
    ADD CONSTRAINT publication_links_target_publication_id_fkey FOREIGN KEY (target_publication_id) REFERENCES publications(id);

ALTER TABLE ONLY publications
    ADD CONSTRAINT publications_feed_id_fkey FOREIGN KEY (feed_id) REFERENCES feeds(id);

ALTER TABLE ONLY settings
    ADD CONSTRAINT settings_new_feed_id_fkey FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE RESTRICT;

ALTER TABLE ONLY settings
    ADD CONSTRAINT settings_new_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE RESTRICT;


INSERT INTO state(key,value) VALUES
 ('publication-pipeline','complete'),('work-enabled','false'),('journal-index-version','1'),
 ('reader-status-revision',jsonb_build_object('version',1,'changed',extract(epoch FROM clock_timestamp()))::text);
INSERT INTO youtube_access(id) VALUES (1);
