// Test-owned synthetic scenario inputs, including historical persisted facts.
// These are not HTTP contracts; actual responses validate against canonical v3.


/**
 * AI
 */
export type Ai = {
    /**
     * Error
     */
    error: string | null;
    /**
     * Status
     */
    status: 'pending' | 'running' | 'done' | 'cancelled';
};

/**
 * Attachment
 */
export type Attachment = {
    /**
     * Kind
     */
    kind: 'image' | 'video' | 'audio' | 'circle' | 'file';
    /**
     * Mime
     */
    mime: string;
    /**
     * Url
     */
    url: string;
};

/**
 * Card
 */
export type Card = {
    ai?: Ai | null;
    /**
     * Attachments
     */
    attachments?: Array<Attachment>;
    /**
     * Captions
     */
    captions?: 'available' | 'not_requested' | 'unavailable' | 'error' | null;
    /**
     * Changed
     */
    changed?: boolean;
    /**
     * Complete
     */
    complete: boolean;
    /**
     * Feed Id
     */
    feed_id: string;
    /**
     * Feed Title
     */
    feed_title: string;
    filter?: Filter;
    /**
     * Folder Id
     */
    folder_id: string;
    /**
     * Full
     */
    full?: boolean;
    /**
     * Id
     */
    id: string;
    /**
     * Images
     */
    images?: Array<string>;
    /**
     * Importance
     */
    importance: 'normal' | 'elevated' | 'high';
    /**
     * Loading
     */
    loading?: boolean;
    /**
     * Members
     */
    members?: Array<number>;
    /**
     * Originals
     */
    originals?: boolean;
    /**
     * Previous
     */
    previous?: Array<Previous>;
    /**
     * Provenance
     */
    provenance?: UnknownProvenance | CodexProvenance | LocalProvenance | null;
    /**
     * Published
     */
    published: number;
    /**
     * Published Known
     */
    published_known: boolean;
    /**
     * Reason
     */
    reason?: string;
    /**
     * Snippet
     */
    snippet: string;
    /**
     * Source Deleted
     */
    source_deleted?: boolean;
    /**
     * Source Order
     */
    source_order?: number | null;
    theses?: Summary | null;
    /**
     * Title
     */
    title: string;
    /**
     * Url
     */
    url: string;
    /**
     * Version
     */
    version?: string;
};

/**
 * CodexProvenance
 */
export type CodexProvenance = {
    /**
     * Fast
     */
    fast: boolean;
    /**
     * Model
     */
    model: string;
    /**
     * Processor
     */
    processor: 'codex';
    /**
     * Reasoning Effort
     */
    reasoning_effort: string;
};

export type CompletionConflict = {
    message: string;
    preview: CompletionPreview;
};

export type CompletionPreview = {
    cards: number;
    originals: number;
    token: string;
};

export type EffectiveSettings = {
    effective: EffectiveValues;
    feed?: SubscriptionFeed;
    overrides: SettingOverrides;
};

export type EffectiveValues = {
    originals: {
        owner: string | null;
        scope: 'default' | 'oko' | 'folder' | 'feed';
        value: boolean;
    };
    processor: {
        owner: string | null;
        scope: 'default' | 'oko' | 'folder' | 'feed';
        value: string;
    };
    prompt_filters: {
        owner: string | null;
        scope: 'default' | 'oko' | 'folder' | 'feed';
        value: string;
    };
    prompt_theses: {
        owner: string | null;
        scope: 'default' | 'oko' | 'folder' | 'feed';
        value: string;
    };
    theses: {
        owner: string | null;
        scope: 'default' | 'oko' | 'folder' | 'feed';
        value: boolean;
    };
};

/**
 * Feed
 */
export type Feed = {
    has_unviewed?: boolean;
    /**
     * Auto Read
     */
    auto_read?: boolean | null;
    /**
     * Connection
     */
    connection?: string;
    /**
     * Count
     */
    count: number;
    /**
     * Folder Id
     */
    folder_id: string;
    /**
     * Id
     */
    id: string;
    /**
     * Identity
     */
    identity?: string;
    /**
     * Importance
     */
    importance?: 'normal' | 'elevated' | 'high';
    /**
     * Initial Days
     */
    initial_days?: number;
    initial_history?: History | null;
    /**
     * Kind
     */
    kind: 'demo' | 'rss' | 'telegram' | 'youtube' | 'unresolved';
    /**
     * Legacy Since
     */
    legacy_since?: number | null;
    /**
     * Original Urls
     */
    original_urls?: Array<string>;
    /**
     * Public Ai
     */
    public_ai?: boolean | null;
    /**
     * Read History
     */
    read_history?: boolean | null;
    /**
     * Telegram Account
     */
    telegram_account?: string | null;
    /**
     * Telegram Peer
     */
    telegram_peer?: number | null;
    /**
     * Telegram Topic
     */
    telegram_topic?: number | null;
    /**
     * Telegram Username
     */
    telegram_username?: string | null;
    /**
     * Title
     */
    title: string;
    /**
     * Transport
     */
    transport?: 'demo' | 'rss' | 'telegram' | 'youtube' | null;
};

export type FeedVerification = {
    existing: SubscriptionFeed | null;
    kind: 'rss' | 'youtube';
    title: string;
    url: string;
    verification: string;
};

/**
 * Filter
 */
export type Filter = {
    /**
     * Error
     */
    error?: string | null;
    /**
     * Hidden
     */
    hidden: boolean;
    /**
     * Reason
     */
    reason: string;
    /**
     * Removed
     */
    removed: Array<string>;
    /**
     * Restored
     */
    restored: boolean;
    /**
     * Status
     */
    status: 'off' | 'pending' | 'running' | 'done' | 'cancelled';
};

/**
 * Folder
 */
export type Folder = {
    /**
     * Count
     */
    count: number;
    /**
     * Id
     */
    id: string;
    /**
     * Position
     */
    position?: number;
    /**
     * Title
     */
    title: string;
};

/**
 * History
 */
export type History = {
    /**
     * Available
     */
    available: number;
    /**
     * Cancelled
     */
    cancelled?: boolean;
    /**
     * Count
     */
    count: number | null;
    /**
     * Count Text
     */
    count_text: string | null;
    /**
     * Date
     */
    date: string | null;
    /**
     * Discovery Error
     */
    discovery_error?: string | null;
    /**
     * Discovery Retry At
     */
    discovery_retry_at?: number | null;
    /**
     * Errors
     */
    errors: Array<HistoryError>;
    /**
     * Frozen
     */
    frozen: boolean;
    /**
     * Incomplete
     */
    incomplete: number;
    /**
     * Loaded
     */
    loaded: number;
    /**
     * Mode
     */
    mode: 'count' | 'date';
    /**
     * Paused
     */
    paused: boolean;
    /**
     * Requests
     */
    requests?: number;
    /**
     * Resume
     */
    resume?: number;
    /**
     * Retrying
     */
    retrying: number;
    /**
     * Run
     */
    run?: string;
    /**
     * Scan Complete
     */
    scan_complete: boolean;
    /**
     * Since
     */
    since: number | null;
    /**
     * State
     */
    state: 'pending' | 'loading' | 'done' | 'selecting' | 'paused' | 'waiting' | 'cancelled';
    /**
     * Timezone
     */
    timezone: string;
    /**
     * Unavailable
     */
    unavailable: number;
    /**
     * Viewed
     */
    viewed: number;
    /**
     * Waiting
     */
    waiting?: boolean;
    /**
     * Warnings
     */
    warnings?: Array<string>;
};

/**
 * HistoryError
 */
export type HistoryError = {
    /**
     * Item
     */
    item: string;
    /**
     * Message
     */
    message: string;
};

/**
 * InitialHistoryStatus
 */
export type InitialHistoryStatus = {
    /**
     * Available
     */
    available: number;
    /**
     * Cancelled
     */
    cancelled?: boolean;
    /**
     * Count
     */
    count: number | null;
    /**
     * Count Text
     */
    count_text: string | null;
    /**
     * Date
     */
    date: string | null;
    /**
     * Discovery Error
     */
    discovery_error?: string | null;
    /**
     * Discovery Retry At
     */
    discovery_retry_at?: number | null;
    /**
     * Errors
     */
    errors: Array<HistoryError>;
    /**
     * Feed Id
     */
    feed_id: string;
    /**
     * Frozen
     */
    frozen: boolean;
    /**
     * Incomplete
     */
    incomplete: number;
    /**
     * Loaded
     */
    loaded: number;
    /**
     * Mode
     */
    mode: 'count' | 'date';
    /**
     * Paused
     */
    paused: boolean;
    /**
     * Requests
     */
    requests?: number;
    /**
     * Resume
     */
    resume?: number;
    /**
     * Retrying
     */
    retrying: number;
    /**
     * Run
     */
    run?: string;
    /**
     * Scan Complete
     */
    scan_complete: boolean;
    /**
     * Since
     */
    since: number | null;
    /**
     * State
     */
    state: 'pending' | 'loading' | 'done' | 'selecting' | 'paused' | 'waiting' | 'cancelled';
    /**
     * Timezone
     */
    timezone: string;
    /**
     * Unavailable
     */
    unavailable: number;
    /**
     * Viewed
     */
    viewed: number;
    /**
     * Waiting
     */
    waiting?: boolean;
    /**
     * Warnings
     */
    warnings?: Array<string>;
};

/**
 * Job
 */
export type Job = {
    /**
     * Count
     */
    count: number;
    /**
     * Error
     */
    error: string | null;
    /**
     * Kind
     */
    kind: 'discover' | 'fetch' | 'theses' | 'filter' | 'delete' | 'read';
    /**
     * Status
     */
    status: 'pending' | 'running' | 'done' | 'cancelled';
};

/**
 * LocalProvenance
 */
export type LocalProvenance = {
    /**
     * Model
     */
    model: string | null;
    /**
     * Processor
     */
    processor: string;
};

/**
 * Material
 */
export type Material = {
    ai?: Ai | null;
    /**
     * Attachments
     */
    attachments?: Array<Attachment>;
    /**
     * Captions
     */
    captions?: 'available' | 'not_requested' | 'unavailable' | 'error' | null;
    /**
     * Changed
     */
    changed?: boolean;
    /**
     * Complete
     */
    complete: boolean;
    /**
     * Description
     */
    description?: string;
    /**
     * Display Text
     */
    display_text?: string;
    /**
     * Feed Id
     */
    feed_id: string;
    filter?: Filter;
    /**
     * Folder Id
     */
    folder_id?: string;
    /**
     * Full
     */
    full?: boolean;
    /**
     * Id
     */
    id: string;
    /**
     * Images
     */
    images?: Array<string>;
    /**
     * Importance
     */
    importance?: 'normal' | 'elevated' | 'high';
    /**
     * Loading
     */
    loading?: boolean;
    /**
     * Members
     */
    members?: Array<number>;
    /**
     * Originals
     */
    originals?: boolean;
    /**
     * Previous
     */
    previous?: Array<Previous>;
    /**
     * Provenance
     */
    provenance?: UnknownProvenance | CodexProvenance | LocalProvenance | null;
    /**
     * Published
     */
    published: number;
    /**
     * Published Known
     */
    published_known?: boolean;
    /**
     * Reason
     */
    reason?: string;
    /**
     * Source Deleted
     */
    source_deleted?: boolean;
    /**
     * Source Order
     */
    source_order?: number | null;
    /**
     * Text
     */
    text: string;
    theses?: Summary | null;
    /**
     * Title
     */
    title: string;
    /**
     * Url
     */
    url: string;
    /**
     * Version
     */
    version?: string;
};

/**
 * Navigation
 */
export type Navigation = {
    /**
     * Count
     */
    count: number;
    /**
     * Feeds
     */
    feeds: Array<Feed>;
    /**
     * Folders
     */
    folders: Array<Folder>;
};

/**
 * Point
 */
export type Point = {
    /**
     * Sources
     */
    sources: Array<Reference>;
    /**
     * Text
     */
    text: string;
};

/**
 * Previous
 */
export type Previous = {
    /**
     * Number
     */
    number?: number;
    /**
     * Url
     */
    url: string;
    /**
     * Version
     */
    version?: string;
};

/**
 * Problem
 */
export type Problem = {
    action: ProblemAction | null;
    /**
     * Code
     */
    code: string;
    /**
     * Consequence
     */
    consequence: string;
    /**
     * Created At
     */
    created_at: number;
    /**
     * Finished At
     */
    finished_at: number | null;
    /**
     * Id
     */
    id: string;
    /**
     * Next At
     */
    next_at: number | null;
    /**
     * Next Step
     */
    next_step: string;
    /**
     * Objects
     */
    objects: Array<ProblemObject>;
    /**
     * Operation
     */
    operation?: string;
    /**
     * Outcome
     */
    outcome: 'resolved' | 'irrelevant' | null;
    /**
     * Reason
     */
    reason?: string;
    /**
     * Request Id
     */
    request_id?: string;
    /**
     * Section
     */
    section: 'action' | 'wait' | 'object';
    /**
     * State
     */
    state: 'current' | 'retrying' | 'finished';
    /**
     * Title
     */
    title: string;
};

/**
 * ProblemAction
 */
export type ProblemAction = {
    /**
     * Id
     */
    id?: string;
    /**
     * Kind
     */
    kind: string;
    /**
     * Page
     */
    page?: string;
    /**
     * Scope
     */
    scope?: 'oko' | 'folder' | 'feed';
};

/**
 * ProblemObject
 */
export type ProblemObject = {
    /**
     * Id
     */
    id: string;
    /**
     * Kind
     */
    kind: 'settings' | 'feed' | 'material';
    /**
     * Label
     */
    label: string;
};

/**
 * ProblemSnapshot
 */
export type ProblemSnapshot = {
    /**
     * Count
     */
    count: number;
    /**
     * Current
     */
    current: Array<Problem>;
    /**
     * Recent
     */
    recent: Array<Problem>;
    /**
     * Updated At
     */
    updated_at: number;
};

/**
 * Reference
 */
export type Reference = {
    /**
     * Id
     */
    id: string;
    /**
     * Url
     */
    url: string;
};

export type SettingOverrides = {
    originals?: boolean;
    processor?: string;
    prompt_filters?: string;
    prompt_theses?: string;
    theses?: boolean;
};

/**
 * SourceStatus
 */
export type SourceStatus = {
    /**
     * Changes
     */
    changes?: string | null;
    /**
     * Error
     */
    error?: string | null;
    /**
     * Feed Id
     */
    feed_id: string;
    /**
     * Gap
     */
    gap?: string | null;
    /**
     * Title
     */
    title: string;
};

/**
 * Status
 */
export type Status = {
    /**
     * Debug Count
     */
    debug_count?: number;
    /**
     * Debug Persistence Error
     */
    debug_persistence_error?: string;
    /**
     * Deleting
     */
    deleting?: boolean;
    /**
     * Error
     */
    error?: string | null;
    /**
     * Errors
     */
    errors: Array<string>;
    /**
     * Initial Histories
     */
    initial_histories?: Array<InitialHistoryStatus>;
    /**
     * Jobs
     */
    jobs?: Array<Job>;
    /**
     * Live Tabs
     */
    live_tabs?: number;
    problems?: ProblemSnapshot;
    /**
     * Ready Sources
     */
    ready_sources: number;
    /**
     * Sources
     */
    sources: Array<SourceStatus>;
    /**
     * Stage
     */
    stage: string;
    /**
     * Stopped
     */
    stopped?: boolean;
    /**
     * Telegram Reads
     */
    telegram_reads?: Array<TelegramRead>;
    /**
     * Total Sources
     */
    total_sources: number;
    /**
     * Viewed Ids
     */
    viewed_ids?: Array<string>;
    youtube?: YouTube | null;
};

/**
 * Feed
 */
export type SubscriptionFeed = {
    /**
     * Auto Read
     */
    auto_read?: boolean | null;
    /**
     * Connection
     */
    connection?: string;
    /**
     * Folder Id
     */
    folder_id: string;
    /**
     * Id
     */
    id: string;
    /**
     * Identity
     */
    identity?: string;
    /**
     * Importance
     */
    importance?: 'normal' | 'elevated' | 'high';
    /**
     * Initial Days
     */
    initial_days?: number;
    initial_history?: History | null;
    /**
     * Kind
     */
    kind: 'demo' | 'rss' | 'telegram' | 'youtube' | 'unresolved';
    /**
     * Legacy Since
     */
    legacy_since?: number | null;
    /**
     * Original Urls
     */
    original_urls?: Array<string>;
    /**
     * Public Ai
     */
    public_ai?: boolean | null;
    /**
     * Read History
     */
    read_history?: boolean | null;
    /**
     * Telegram Account
     */
    telegram_account?: string | null;
    /**
     * Telegram Peer
     */
    telegram_peer?: number | null;
    /**
     * Telegram Username
     */
    telegram_username?: string | null;
    /**
     * Title
     */
    title: string;
    /**
     * Transport
     */
    transport?: 'demo' | 'rss' | 'telegram' | 'youtube' | null;
};

export type SubscriptionFolder = {
    id: string;
    position: number;
    title: string;
};

export type Subscriptions = {
    feeds: Array<SubscriptionFeed>;
    folders: Array<SubscriptionFolder>;
};

/**
 * Summary
 */
export type Summary = {
    /**
     * Points
     */
    points: Array<Point>;
};

/**
 * TelegramRead
 */
export type TelegramRead = {
    /**
     * Confirmed
     */
    confirmed: number;
    /**
     * Error
     */
    error: string;
    /**
     * Feed Id
     */
    feed_id: string;
    /**
     * Initial
     */
    initial: number | null;
    /**
     * Pending
     */
    pending: number;
    /**
     * Waiting
     */
    waiting: string;
};

/**
 * UnknownProvenance
 */
export type UnknownProvenance = {
    /**
     * Model
     */
    model: null;
};

/**
 * Version
 */
export type Version = {
    /**
     * Attachments
     */
    attachments: Array<Attachment>;
    /**
     * Full
     */
    full: boolean;
    /**
     * Images
     */
    images: Array<string>;
    /**
     * Reason
     */
    reason: string;
    /**
     * Text
     */
    text: string;
    /**
     * Title
     */
    title: string;
};

/**
 * YouTube
 */
export type YouTube = {
    /**
     * Address
     */
    address: string;
    /**
     * Has Password
     */
    has_password: boolean;
    /**
     * Has Username
     */
    has_username: boolean;
    /**
     * Mode
     */
    mode: 'direct' | 'proxy';
    /**
     * Next At
     */
    next_at: number;
    /**
     * Paused
     */
    paused: boolean;
    /**
     * Reason
     */
    reason: string;
    /**
     * Strikes
     */
    strikes: number;
};
