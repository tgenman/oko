"""Validated internal snapshots from Store and Runtime.

These describe persisted/domain facts (including historical optional fields),
not HTTP requests or responses. Only generated v3 models cross the HTTP boundary.
"""
from __future__ import annotations
from oko.collection.contracts import ReplyPreview
from typing import Annotated, Any, Literal
from pydantic import BeforeValidator, ConfigDict, Field, RootModel, StrictBool, StrictFloat, StrictInt, StrictStr
from pydantic.experimental.missing_sentinel import MISSING
from oko.reader.wire import TimestampValue as datetime
from oko.reader.wire import WireModel, boolean_constant, integer_constant

class AI(WireModel):
    model_config = ConfigDict(extra='forbid')
    error: StrictStr | None = Field(..., title='Error')
    status: Literal['off', 'pending', 'running', 'done', 'cancelled'] = Field(..., title='Status')

class Attachment(WireModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['image', 'video', 'audio', 'circle', 'file'] = Field(..., title='Kind')
    mime: StrictStr = Field(..., title='Mime')
    url: StrictStr = Field(..., title='Url')

class CodexChoice(WireModel):
    model_config = ConfigDict(extra='forbid')
    fast: StrictBool = Field(default=False, title='Fast')
    model: StrictStr = Field(..., max_length=200, min_length=1, pattern='^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$', title='Model')
    reasoning_effort: StrictStr = Field(..., max_length=30, min_length=1, pattern='^[a-z_]+$', title='Reasoning Effort')

class CodexProvenance(WireModel):
    model_config = ConfigDict(extra='forbid')
    fast: StrictBool = Field(..., title='Fast')
    model: StrictStr = Field(..., title='Model')
    processor: Literal['codex'] = Field(..., title='Processor')
    reasoning_effort: StrictStr = Field(..., title='Reasoning Effort')

class CodexSettings(WireModel):
    model_config = ConfigDict(extra='forbid')
    filter: CodexChoice
    theses: CodexChoice

class CompletionPreview(WireModel):
    model_config = ConfigDict(extra='forbid')
    cards: StrictInt
    originals: StrictInt
    token: StrictStr

class DiagnosticEntry(WireModel):
    model_config = ConfigDict(extra='forbid')
    count: StrictInt
    error_type: StrictStr
    first_time: StrictStr
    id: StrictStr
    last_time: StrictStr
    location: StrictStr
    material_ids: list[StrictStr] | MISSING = MISSING
    message: StrictStr
    source: StrictStr
    streams: dict[str, StrictInt]
    traceback: StrictStr

class Originals(WireModel):
    model_config = ConfigDict(extra='forbid')
    owner: StrictStr | None
    scope: Literal['default', 'oko', 'folder', 'feed']
    value: StrictBool

class Processor(WireModel):
    model_config = ConfigDict(extra='forbid')
    owner: StrictStr | None
    scope: Literal['default', 'oko', 'folder', 'feed']
    value: StrictStr

class PromptFilters(WireModel):
    model_config = ConfigDict(extra='forbid')
    owner: StrictStr | None
    scope: Literal['default', 'oko', 'folder', 'feed']
    value: StrictStr

class PromptTheses(WireModel):
    model_config = ConfigDict(extra='forbid')
    owner: StrictStr | None
    scope: Literal['default', 'oko', 'folder', 'feed']
    value: StrictStr

class Theses(WireModel):
    model_config = ConfigDict(extra='forbid')
    owner: StrictStr | None
    scope: Literal['default', 'oko', 'folder', 'feed']
    value: StrictBool

class FeedRefreshMinutes(WireModel):
    owner: StrictStr | None
    scope: Literal['default', 'oko']
    value: StrictInt


class EffectiveValues(WireModel):
    model_config = ConfigDict(extra='forbid')
    originals: Originals
    feed_refresh_minutes: FeedRefreshMinutes | MISSING = MISSING
    processor: Processor
    prompt_filters: PromptFilters
    filter_enabled: Theses
    prompt_theses: PromptTheses
    theses: Theses

class Filter(WireModel):
    model_config = ConfigDict(extra='forbid')
    error: StrictStr | None | MISSING = Field(default=MISSING, title='Error')
    hidden: StrictBool = Field(..., title='Hidden')
    reason: StrictStr = Field(..., title='Reason')
    removed: list[StrictStr] = Field(..., title='Removed')
    status: Literal['off', 'pending', 'running', 'done', 'cancelled'] = Field(..., title='Status')

class Folder(WireModel):
    model_config = ConfigDict(extra='forbid')
    count: StrictInt = Field(..., title='Count')
    id: StrictStr = Field(..., title='Id')
    position: StrictInt | MISSING = Field(default=MISSING, title='Position')
    title: StrictStr = Field(..., title='Title')

class HistoryError(WireModel):
    model_config = ConfigDict(extra='forbid')
    item: StrictStr = Field(..., title='Item')
    message: StrictStr = Field(..., title='Message')

class InitialHistoryStatus(WireModel):
    model_config = ConfigDict(extra='forbid')
    available: StrictInt = Field(..., title='Available')
    cancelled: StrictBool | MISSING = Field(default=MISSING, title='Cancelled')
    count: StrictInt | None = Field(..., title='Count')
    count_text: StrictStr | None = Field(..., title='Count Text')
    discovery_error: StrictStr | None | MISSING = Field(default=MISSING, title='Discovery Error')
    discovery_retry_at: StrictFloat | None | MISSING = Field(default=MISSING, title='Discovery Retry At')
    errors: list[HistoryError] = Field(..., title='Errors')
    feed_id: StrictStr = Field(..., title='Feed Id')
    frozen: StrictBool = Field(..., title='Frozen')
    incomplete: StrictInt = Field(..., title='Incomplete')
    loaded: StrictInt = Field(..., title='Loaded')
    mode: Literal['count'] = Field(..., title='Mode')
    paused: StrictBool = Field(..., title='Paused')
    requests: StrictInt | MISSING = Field(default=MISSING, title='Requests')
    resume: StrictInt | MISSING = Field(default=MISSING, title='Resume')
    retrying: StrictInt = Field(..., title='Retrying')
    run: StrictStr | MISSING = Field(default=MISSING, title='Run')
    scan_complete: StrictBool = Field(..., title='Scan Complete')
    since: StrictFloat | None = Field(..., title='Since')
    stop_reason: StrictStr | None | MISSING = MISSING
    state: Literal['pending', 'loading', 'done', 'selecting', 'paused', 'waiting', 'cancelled'] = Field(..., title='State')
    unavailable: StrictInt = Field(..., title='Unavailable')
    viewed: StrictInt = Field(..., title='Viewed')
    waiting: StrictBool | MISSING = Field(default=MISSING, title='Waiting')
    warnings: list[StrictStr] | MISSING = Field(default=MISSING, title='Warnings')

class LocalProvenance(WireModel):
    model_config = ConfigDict(extra='forbid', regex_engine='python-re')
    model: StrictStr | None = Field(..., title='Model')
    processor: StrictStr = Field(..., pattern='^(?!codex$).+', title='Processor')


class ProblemObject(WireModel):
    model_config = ConfigDict(extra='forbid')
    id: StrictStr = Field(..., title='Id')
    kind: Literal['settings', 'feed', 'folder', 'material'] = Field(..., title='Kind')

class ProcessorCheck(WireModel):
    model_config = ConfigDict(extra='forbid')
    local: StrictBool
    mode: StrictStr
    model: StrictStr
    models: list[StrictStr]

class QuotaWindow(WireModel):
    model_config = ConfigDict(extra='forbid')
    resets_at: StrictFloat | None
    used_percent: StrictFloat

class Reference(WireModel):
    model_config = ConfigDict(extra='forbid')
    id: StrictStr = Field(..., title='Id')
    url: StrictStr = Field(..., title='Url')

class SettingOverrides(WireModel):
    feed_refresh_minutes: StrictInt | MISSING = MISSING
    model_config = ConfigDict(extra='forbid')
    originals: StrictBool | MISSING = MISSING
    processor: StrictStr | MISSING = MISSING
    prompt_filters: StrictStr | MISSING = MISSING
    filter_enabled: StrictBool | MISSING = MISSING
    prompt_theses: StrictStr | MISSING = MISSING
    theses: StrictBool | MISSING = MISSING

class SourceStatus(WireModel):
    model_config = ConfigDict(extra='forbid')
    changes: StrictStr | None | MISSING = Field(default=MISSING, title='Changes')
    error: StrictStr | None | MISSING = Field(default=MISSING, title='Error')
    feed_id: StrictStr = Field(..., title='Feed Id')
    gap: StrictStr | None | MISSING = Field(default=MISSING, title='Gap')
    title: StrictStr = Field(..., title='Title')

class TelegramRead(WireModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: StrictInt = Field(..., title='Confirmed')
    error: StrictStr = Field(..., title='Error')
    feed_id: StrictStr = Field(..., title='Feed Id')
    initial: StrictInt | None = Field(..., title='Initial')
    pending: StrictInt = Field(..., title='Pending')
    waiting: StrictStr = Field(..., title='Waiting')

class UnknownProvenance(WireModel):
    model_config = ConfigDict(extra='forbid')
    model: None = Field(..., title='Model')

class ExternalAttachment(WireModel):
    kind: Literal["audio", "video", "image", "document", "unsupported"]
    source_url: str
    reason: Literal["public_preview_unavailable"]
    restriction: Literal["size_limit"] | None = None
    title: str | None = None
    size_label: str | None = None
    author: str | None = None
    duration: str | None = None
    poster_url: str | None = None


class LinkedArticle(WireModel):
    source_url: str
    title: str
    text: str
    error: str | None
    media_urls: list[str]


class LinkedContent(WireModel):
    post_text: str
    articles: list[LinkedArticle]



class YouTube(WireModel):
    model_config = ConfigDict(extra='forbid')
    address: StrictStr = Field(..., title='Address')
    has_password: StrictBool = Field(..., title='Has Password')
    has_username: StrictBool = Field(..., title='Has Username')
    mode: Literal['direct', 'proxy'] = Field(..., title='Mode')
    next_at: StrictFloat | None = Field(..., title='Next At')
    time_unknown: StrictBool = False
    paused: StrictBool = Field(..., title='Paused')
    reason: StrictStr = Field(..., title='Reason')
    strikes: StrictInt | None = Field(..., title='Strikes')

class CodexState(WireModel):
    model_config = ConfigDict(extra='forbid')
    auth: StrictStr | MISSING = MISSING
    available: StrictBool
    config: CodexSettings | None
    limits_checked: StrictBool
    local: StrictBool | MISSING = MISSING
    quota_windows: list[QuotaWindow] | MISSING = MISSING
    reason: StrictStr
    version: StrictStr | None | MISSING = MISSING

class CompletionConflict(WireModel):
    model_config = ConfigDict(extra='forbid')
    message: StrictStr
    preview: CompletionPreview

class History(WireModel):
    model_config = ConfigDict(extra='forbid')
    available: StrictInt = Field(..., title='Available')
    cancelled: StrictBool | MISSING = Field(default=MISSING, title='Cancelled')
    count: StrictInt | None = Field(..., title='Count')
    count_text: StrictStr | None = Field(..., title='Count Text')
    discovery_error: StrictStr | None | MISSING = Field(default=MISSING, title='Discovery Error')
    discovery_retry_at: StrictFloat | None | MISSING = Field(default=MISSING, title='Discovery Retry At')
    errors: list[HistoryError] = Field(..., title='Errors')
    frozen: StrictBool = Field(..., title='Frozen')
    incomplete: StrictInt = Field(..., title='Incomplete')
    loaded: StrictInt = Field(..., title='Loaded')
    mode: Literal['count'] = Field(..., title='Mode')
    paused: StrictBool = Field(..., title='Paused')
    requests: StrictInt | MISSING = Field(default=MISSING, title='Requests')
    resume: StrictInt | MISSING = Field(default=MISSING, title='Resume')
    retrying: StrictInt = Field(..., title='Retrying')
    run: StrictStr | MISSING = Field(default=MISSING, title='Run')
    scan_complete: StrictBool = Field(..., title='Scan Complete')
    since: StrictFloat | None = Field(..., title='Since')
    stop_reason: StrictStr | None | MISSING = MISSING
    state: Literal['pending', 'loading', 'done', 'selecting', 'paused', 'waiting', 'cancelled'] = Field(..., title='State')
    unavailable: StrictInt = Field(..., title='Unavailable')
    viewed: StrictInt = Field(..., title='Viewed')
    waiting: StrictBool | MISSING = Field(default=MISSING, title='Waiting')
    warnings: list[StrictStr] | MISSING = Field(default=MISSING, title='Warnings')

class Point(WireModel):
    model_config = ConfigDict(extra='forbid')
    sources: list[Reference] = Field(..., title='Sources')
    text: StrictStr = Field(..., title='Text')

class Problem(WireModel):
    model_config = ConfigDict(extra='ignore')
    code: StrictStr = Field(..., title='Code')
    consequence: StrictStr = Field(..., title='Consequence')
    created_at: StrictFloat | None = Field(..., title='Created At')
    finished_at: StrictFloat | None = Field(..., title='Finished At')
    id: StrictStr = Field(..., title='Id')
    next_at: StrictFloat | None = Field(..., title='Next At')
    next_step: StrictStr = Field(..., title='Next Step')
    objects: list[ProblemObject] = Field(..., title='Objects')
    operation: StrictStr | MISSING = Field(default=MISSING, title='Operation')
    outcome: Literal['resolved', 'irrelevant'] | None = Field(..., title='Outcome')
    reason: StrictStr | MISSING = Field(default=MISSING, title='Reason')
    request_id: StrictStr | MISSING = Field(default=MISSING, title='Request Id')
    state: Literal['current', 'retrying', 'finished'] = Field(..., title='State')
    title: StrictStr = Field(..., title='Title')

class ProblemSnapshot(WireModel):
    model_config = ConfigDict(extra='forbid')
    count: StrictInt = Field(..., title='Count')
    current: list[Problem] = Field(..., title='Current')
    recent: list[Problem] = Field(..., title='Recent')
    updated_at: StrictFloat = Field(..., title='Updated At')

class Status(WireModel):
    model_config = ConfigDict(extra='forbid')
    error: StrictStr | None | MISSING = Field(default=MISSING, title='Error')
    initial_histories: list[InitialHistoryStatus] | MISSING = Field(default=MISSING, title='Initial Histories')
    problems: ProblemSnapshot | MISSING = MISSING
    ready_sources: StrictInt = Field(..., title='Ready Sources')
    sources: list[SourceStatus] = Field(..., title='Sources')
    telegram_reads: list[TelegramRead] | MISSING = Field(default=MISSING, title='Telegram Reads')
    total_sources: StrictInt = Field(..., title='Total Sources')
    viewed_ids: list[StrictStr] | MISSING = Field(default=MISSING, title='Viewed Ids')
    youtube: YouTube | None | MISSING = MISSING

class SubscriptionFeed(WireModel):
    avatar_url: StrictStr | MISSING = MISSING
    model_config = ConfigDict(extra='forbid')
    auto_read: StrictBool | None | MISSING = Field(default=MISSING, title='Auto Read')
    connection: StrictStr | MISSING = Field(default=MISSING, title='Connection')
    folder_id: StrictStr = Field(..., title='Folder Id')
    id: StrictStr = Field(..., title='Id')
    identity: StrictStr | MISSING = Field(default=MISSING, title='Identity')
    importance: Literal['normal', 'elevated', 'high'] | MISSING = Field(default=MISSING, title='Importance')
    initial_days: StrictInt | MISSING = Field(default=MISSING, title='Initial Days')
    initial_history: History | None | MISSING = MISSING
    kind: Literal['demo', 'rss', 'telegram', 'youtube', 'unresolved'] = Field(..., title='Kind')
    original_urls: list[StrictStr] | MISSING = Field(default=MISSING, title='Original Urls')
    public_ai: StrictBool | None | MISSING = Field(default=MISSING, title='Public Ai')
    read_history: StrictBool | None | MISSING = Field(default=MISSING, title='Read History')
    telegram_account: StrictStr | None | MISSING = Field(default=MISSING, title='Telegram Account')
    telegram_peer: StrictInt | None | MISSING = Field(default=MISSING, title='Telegram Peer')
    telegram_username: StrictStr | None | MISSING = Field(default=MISSING, title='Telegram Username')
    title: StrictStr = Field(..., title='Title')
    transport: Literal['demo', 'rss', 'telegram', 'youtube'] | None | MISSING = Field(default=MISSING, title='Transport')

class Summary(WireModel):
    model_config = ConfigDict(extra='forbid')
    points: list[Point] = Field(..., title='Points')

class EffectiveSettings(WireModel):
    model_config = ConfigDict(extra='forbid')
    effective: EffectiveValues
    feed: SubscriptionFeed | MISSING = MISSING
    overrides: SettingOverrides

class Feed(WireModel):
    has_unviewed: StrictBool
    avatar_url: StrictStr | MISSING = MISSING
    model_config = ConfigDict(extra='forbid')
    auto_read: StrictBool | None | MISSING = Field(default=MISSING, title='Auto Read')
    connection: StrictStr | MISSING = Field(default=MISSING, title='Connection')
    count: StrictInt = Field(..., title='Count')
    folder_id: StrictStr = Field(..., title='Folder Id')
    id: StrictStr = Field(..., title='Id')
    identity: StrictStr | MISSING = Field(default=MISSING, title='Identity')
    importance: Literal['normal', 'elevated', 'high'] | MISSING = Field(default=MISSING, title='Importance')
    initial_days: StrictInt | MISSING = Field(default=MISSING, title='Initial Days')
    initial_history: History | None | MISSING = MISSING
    kind: Literal['demo', 'rss', 'telegram', 'youtube', 'unresolved'] = Field(..., title='Kind')
    original_urls: list[StrictStr] | MISSING = Field(default=MISSING, title='Original Urls')
    public_ai: StrictBool | None | MISSING = Field(default=MISSING, title='Public Ai')
    read_history: StrictBool | None | MISSING = Field(default=MISSING, title='Read History')
    telegram_account: StrictStr | None | MISSING = Field(default=MISSING, title='Telegram Account')
    telegram_peer: StrictInt | None | MISSING = Field(default=MISSING, title='Telegram Peer')
    telegram_topic: StrictInt | None | MISSING = Field(default=MISSING, title='Telegram Topic')
    telegram_username: StrictStr | None | MISSING = Field(default=MISSING, title='Telegram Username')
    title: StrictStr = Field(..., title='Title')
    transport: Literal['demo', 'rss', 'telegram', 'youtube'] | None | MISSING = Field(default=MISSING, title='Transport')

class FeedVerification(WireModel):
    model_config = ConfigDict(extra='forbid')
    existing: SubscriptionFeed | None
    kind: Literal['rss', 'youtube']
    title: StrictStr
    url: StrictStr
    verification: StrictStr

class Material(WireModel):
    linked_content: LinkedContent | None = None
    external_attachments: list[ExternalAttachment] = Field(default_factory=list)
    external_only: bool = False
    post_web_unavailable: bool = False
    reply_preview: ReplyPreview | None = None
    model_config = ConfigDict(extra='forbid')
    ai: AI | None | MISSING = MISSING
    attachments: list[Attachment] | MISSING = Field(default=MISSING, title='Attachments')
    captions: Literal['available', 'not_requested', 'unavailable', 'error'] | None | MISSING = Field(default=MISSING, title='Captions')
    changed: StrictBool | MISSING = Field(default=MISSING, title='Changed')
    complete: StrictBool = Field(..., title='Complete')
    description: StrictStr | MISSING = Field(default=MISSING, title='Description')
    display_text: StrictStr | MISSING = Field(default=MISSING, title='Display Text')
    feed_id: StrictStr = Field(..., title='Feed Id')
    filter: Filter | MISSING = MISSING
    folder_id: StrictStr | MISSING = Field(default=MISSING, title='Folder Id')
    full: StrictBool | MISSING = Field(default=MISSING, title='Full')
    id: StrictStr = Field(..., title='Id')
    images: list[StrictStr] | MISSING = Field(default=MISSING, title='Images')
    importance: Literal['normal', 'elevated', 'high'] | MISSING = Field(default=MISSING, title='Importance')
    loading: StrictBool | MISSING = Field(default=MISSING, title='Loading')
    members: list[StrictInt] | MISSING = Field(default=MISSING, title='Members')
    originals: StrictBool | MISSING = Field(default=MISSING, title='Originals')
    provenance: UnknownProvenance | CodexProvenance | LocalProvenance | None | MISSING = Field(default=MISSING, title='Provenance')
    published: StrictFloat = Field(..., title='Published')
    published_known: StrictBool | MISSING = Field(default=MISSING, title='Published Known')
    reason: StrictStr | MISSING = Field(default=MISSING, title='Reason')
    source_deleted: StrictBool | MISSING = Field(default=MISSING, title='Source Deleted')
    source_order: StrictInt | None | MISSING = Field(default=MISSING, title='Source Order')
    text: StrictStr = Field(..., title='Text')
    theses: Summary | None | MISSING = MISSING
    title: StrictStr = Field(..., title='Title')
    url: StrictStr = Field(..., title='Url')
    version: StrictStr | MISSING = Field(default=MISSING, title='Version')

class Navigation(WireModel):
    model_config = ConfigDict(extra='forbid')
    count: StrictInt = Field(..., title='Count')
    feeds: list[Feed] = Field(..., title='Feeds')
    folders: list[Folder] = Field(..., title='Folders')
