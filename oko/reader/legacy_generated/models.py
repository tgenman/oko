# Generated from api/openapi.yaml; do not edit.
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from pydantic.experimental.missing_sentinel import MISSING

from oko.reader.wire import TimestampValue as datetime
from oko.reader.wire import WireModel, boolean_constant, integer_constant


class AI(WireModel):
    model_config = ConfigDict(extra="forbid")
    error: StrictStr | None = Field(..., title="Error")
    status: Literal["pending", "running", "done", "cancelled"] = Field(
        ..., title="Status"
    )


class ActionReceipt(WireModel):
    model_config = ConfigDict(extra="forbid")
    ok: StrictBool
    result: Literal["failed", "unknown", "succeeded", "abandoned", "pending"]


class Attachment(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "video", "audio", "circle", "file"] = Field(
        ..., title="Kind"
    )
    mime: StrictStr = Field(..., title="Mime")
    url: StrictStr = Field(..., title="Url")


class BrowserError(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(default=1, ge=1, le=1000000, title="Count")
    error_type: StrictStr = Field(default="Error", max_length=200, title="Error Type")
    event_id: StrictStr | MISSING = Field(
        default=MISSING, max_length=100, min_length=1, title="Event Id"
    )
    first_at: datetime | None | MISSING = Field(default=MISSING, title="First At")
    location: StrictStr = Field(default="", max_length=1000, title="Location")
    material_ids: list[StrictStr] | MISSING = Field(
        default=MISSING, max_length=1000, title="Material Ids"
    )
    message: StrictStr = Field(..., max_length=4000, title="Message")
    occurred_at: datetime | None | MISSING = Field(default=MISSING, title="Occurred At")
    sequence: StrictInt = Field(default=1, ge=1, le=9007199254740991, title="Sequence")
    source: StrictStr = Field(..., max_length=500, min_length=1, title="Source")
    stream_id: StrictStr = Field(default="", max_length=100, title="Stream Id")
    traceback: StrictStr = Field(default="", title="Traceback")


class CodexChoice(WireModel):
    model_config = ConfigDict(extra="forbid")
    fast: StrictBool = Field(default=False, title="Fast")
    model: StrictStr = Field(
        ...,
        max_length=200,
        min_length=1,
        pattern="^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$",
        title="Model",
    )
    reasoning_effort: StrictStr = Field(
        ..., max_length=30, min_length=1, pattern="^[a-z_]+$", title="Reasoning Effort"
    )


class CodexModel(WireModel):
    model_config = ConfigDict(extra="forbid")
    default: StrictBool
    default_reasoning_effort: StrictStr
    display_name: StrictStr
    fast_supported: StrictBool
    model: StrictStr
    reasoning_efforts: list[StrictStr]


class CodexModels(WireModel):
    model_config = ConfigDict(extra="forbid")
    models: list[CodexModel]


class CodexProvenance(WireModel):
    model_config = ConfigDict(extra="forbid")
    fast: StrictBool = Field(..., title="Fast")
    model: StrictStr = Field(..., title="Model")
    processor: Literal["codex"] = Field(..., title="Processor")
    reasoning_effort: StrictStr = Field(..., title="Reasoning Effort")


class CodexSettings(WireModel):
    model_config = ConfigDict(extra="forbid")
    filter: CodexChoice
    theses: CodexChoice


class CompletionPreview(WireModel):
    model_config = ConfigDict(extra="forbid")
    cards: StrictInt
    originals: StrictInt
    token: StrictStr


class CompletionRequest(WireModel):
    token: StrictStr = Field(..., max_length=100, min_length=1, title="Token")


class TopicId(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(..., ge=1, le=2147483647, title="Topic Id")


class DiagnosticClear(WireModel):
    model_config = ConfigDict(extra="forbid")
    ok: StrictBool
    persistence_error: StrictStr


class DiagnosticEntry(WireModel):
    model_config = ConfigDict(extra="forbid")
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


class DiagnosticJournal(WireModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[DiagnosticEntry]
    persistence_error: StrictStr


class DiagnosticReceipt(WireModel):
    model_config = ConfigDict(extra="forbid")
    entry: DiagnosticEntry | None
    group: StrictStr | None
    ok: StrictBool
    persistence_error: StrictStr


class Originals(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: StrictStr | None
    scope: Literal["default", "oko", "folder", "feed"]
    value: StrictBool


class Processor(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: StrictStr | None
    scope: Literal["default", "oko", "folder", "feed"]
    value: StrictStr


class PromptFilters(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: StrictStr | None
    scope: Literal["default", "oko", "folder", "feed"]
    value: StrictStr


class PromptTheses(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: StrictStr | None
    scope: Literal["default", "oko", "folder", "feed"]
    value: StrictStr


class Theses(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: StrictStr | None
    scope: Literal["default", "oko", "folder", "feed"]
    value: StrictBool


class EffectiveValues(WireModel):
    model_config = ConfigDict(extra="forbid")
    originals: Originals
    processor: Processor
    prompt_filters: PromptFilters
    prompt_theses: PromptTheses
    theses: Theses


class InitialDays(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(..., ge=1, le=3650, title="Initial Days")


class FeedPatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    auto_read: StrictBool | None | MISSING = Field(default=MISSING, title="Auto Read")
    folder_id: StrictStr | None | MISSING = Field(default=MISSING, title="Folder Id")
    importance: Literal["normal", "elevated", "high"] | None | MISSING = Field(
        default=MISSING, title="Importance"
    )
    initial_days: InitialDays | None | MISSING = Field(
        default=MISSING, title="Initial Days"
    )
    public_ai: StrictBool | None | MISSING = Field(default=MISSING, title="Public Ai")
    read_history: StrictBool | None | MISSING = Field(
        default=MISSING, title="Read History"
    )
    transport: Literal["telegram", "rss", "youtube"] | None | MISSING = Field(
        default=MISSING, title="Transport"
    )


class FeedURL(WireModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(..., max_length=4000, min_length=1, title="Url")


class Filter(WireModel):
    model_config = ConfigDict(extra="forbid")
    error: StrictStr | None | MISSING = Field(default=MISSING, title="Error")
    hidden: StrictBool = Field(..., title="Hidden")
    reason: StrictStr = Field(..., title="Reason")
    removed: list[StrictStr] = Field(..., title="Removed")
    restored: StrictBool = Field(..., title="Restored")
    status: Literal["off", "pending", "running", "done", "cancelled"] = Field(
        ..., title="Status"
    )


class Folder(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., title="Count")
    id: StrictStr = Field(..., title="Id")
    position: StrictInt | MISSING = Field(default=MISSING, title="Position")
    title: StrictStr = Field(..., title="Title")


class HistoryError(WireModel):
    model_config = ConfigDict(extra="forbid")
    item: StrictStr = Field(..., title="Item")
    message: StrictStr = Field(..., title="Message")


class HistoryPause(WireModel):
    paused: StrictBool = Field(..., title="Paused")


class Count(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(
        ...,
        description="Exact integers above JavaScript safe range are sent as ASCII decimal strings. Count mode requires count >= 1 and null date; date mode requires null count and a nonfuture ISO date in the named IANA timezone. These cross-field constraints are enforced by the server.",
        ge=1,
    )


class Count1(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ...,
        description="Exact integers above JavaScript safe range are sent as ASCII decimal strings. Count mode requires count >= 1 and null date; date mode requires null count and a nonfuture ISO date in the named IANA timezone. These cross-field constraints are enforced by the server.",
        pattern="^[0-9]+$",
    )


class InitialHistory(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: Count | Count1 | None = Field(
        default=20,
        description="Exact integers above JavaScript safe range are sent as ASCII decimal strings. Count mode requires count >= 1 and null date; date mode requires null count and a nonfuture ISO date in the named IANA timezone. These cross-field constraints are enforced by the server.",
        validate_default=True,
    )
    date: StrictStr | None | MISSING = Field(default=MISSING, title="Date")
    mode: Literal["count", "date"] = Field(default="count", title="Mode")
    timezone: StrictStr = Field(default="UTC", title="Timezone")


class InitialHistoryStatus(WireModel):
    model_config = ConfigDict(extra="forbid")
    available: StrictInt = Field(..., title="Available")
    cancelled: StrictBool | MISSING = Field(default=MISSING, title="Cancelled")
    count: StrictInt | None = Field(..., title="Count")
    count_text: StrictStr | None = Field(..., title="Count Text")
    date: StrictStr | None = Field(..., title="Date")
    discovery_error: StrictStr | None | MISSING = Field(
        default=MISSING, title="Discovery Error"
    )
    discovery_retry_at: StrictFloat | None | MISSING = Field(
        default=MISSING, title="Discovery Retry At"
    )
    errors: list[HistoryError] = Field(..., title="Errors")
    feed_id: StrictStr = Field(..., title="Feed Id")
    frozen: StrictBool = Field(..., title="Frozen")
    incomplete: StrictInt = Field(..., title="Incomplete")
    loaded: StrictInt = Field(..., title="Loaded")
    mode: Literal["count", "date"] = Field(..., title="Mode")
    paused: StrictBool = Field(..., title="Paused")
    requests: StrictInt | MISSING = Field(default=MISSING, title="Requests")
    resume: StrictInt | MISSING = Field(default=MISSING, title="Resume")
    retrying: StrictInt = Field(..., title="Retrying")
    run: StrictStr | MISSING = Field(default=MISSING, title="Run")
    scan_complete: StrictBool = Field(..., title="Scan Complete")
    since: StrictFloat | None = Field(..., title="Since")
    state: Literal[
        "pending", "loading", "done", "selecting", "paused", "waiting", "cancelled"
    ] = Field(..., title="State")
    timezone: StrictStr = Field(..., title="Timezone")
    unavailable: StrictInt = Field(..., title="Unavailable")
    viewed: StrictInt = Field(..., title="Viewed")
    waiting: StrictBool | MISSING = Field(default=MISSING, title="Waiting")
    warnings: list[StrictStr] | MISSING = Field(default=MISSING, title="Warnings")


class Job(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., title="Count")
    error: StrictStr | None = Field(..., title="Error")
    kind: Literal["discover", "fetch", "theses", "filter", "delete", "read"] = Field(
        ..., title="Kind"
    )
    status: Literal["pending", "running", "done", "cancelled"] = Field(
        ..., title="Status"
    )


class LocalProvenance(WireModel):
    model_config = ConfigDict(extra="forbid", regex_engine="python-re")
    model: StrictStr | None = Field(..., title="Model")
    processor: StrictStr = Field(..., pattern="^(?!codex$).+", title="Processor")


class Ok(WireModel):
    model_config = ConfigDict(extra="forbid")
    ok: StrictBool


class Processor1(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., max_length=200, min_length=1, title="Processor")


class PromptFilters1(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., max_length=20000, title="Prompt Filters")


class PromptTheses1(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., max_length=20000, title="Prompt Theses")


class Overrides(WireModel):
    model_config = ConfigDict(extra="forbid")
    originals: StrictBool | None | MISSING = Field(default=MISSING, title="Originals")
    processor: Processor1 | None | MISSING = Field(default=MISSING, title="Processor")
    prompt_filters: PromptFilters1 | None | MISSING = Field(
        default=MISSING, title="Prompt Filters"
    )
    prompt_theses: PromptTheses1 | None | MISSING = Field(
        default=MISSING, title="Prompt Theses"
    )
    theses: StrictBool | None | MISSING = Field(default=MISSING, title="Theses")


class Previous(WireModel):
    model_config = ConfigDict(extra="forbid")
    number: StrictInt | MISSING = Field(default=MISSING, title="Number")
    url: StrictStr = Field(..., title="Url")
    version: StrictStr | MISSING = Field(default=MISSING, title="Version")


class ProblemAction(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr | MISSING = Field(default=MISSING, title="Id")
    kind: StrictStr = Field(..., title="Kind")
    page: StrictStr | MISSING = Field(default=MISSING, title="Page")
    scope: Literal["oko", "folder", "feed"] | MISSING = Field(
        default=MISSING, title="Scope"
    )


class ProblemObject(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr = Field(..., title="Id")
    kind: Literal["settings", "feed", "material"] = Field(..., title="Kind")
    label: StrictStr = Field(..., title="Label")


class ProcessorCheck(WireModel):
    model_config = ConfigDict(extra="forbid")
    local: StrictBool
    mode: StrictStr
    model: StrictStr
    models: list[StrictStr]


class ProcessorConfig(WireModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: StrictStr = Field(..., max_length=2000, title="Endpoint")
    local_execution: StrictBool = Field(default=False, title="Local Execution")
    model: StrictStr = Field(..., max_length=200, min_length=1, title="Model")


class QuotaWindow(WireModel):
    model_config = ConfigDict(extra="forbid")
    resets_at: StrictFloat | None
    used_percent: StrictFloat


class Reference(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr = Field(..., title="Id")
    url: StrictStr = Field(..., title="Url")


class ReportAction(WireModel):
    fields: list[StrictStr] | MISSING = Field(
        default=MISSING, max_length=50, title="Fields"
    )
    id: StrictStr = Field(..., pattern="^[\\w-]{1,100}$", title="Id")
    method: Literal["POST", "PUT", "PATCH", "DELETE"] = Field(..., title="Method")
    path: StrictStr = Field(..., max_length=2000, title="Path")
    result: Literal["failed", "unknown"] = Field(..., title="Result")


class SettingOverrides(WireModel):
    model_config = ConfigDict(extra="forbid")
    originals: StrictBool | MISSING = MISSING
    processor: StrictStr | MISSING = MISSING
    prompt_filters: StrictStr | MISSING = MISSING
    prompt_theses: StrictStr | MISSING = MISSING
    theses: StrictBool | MISSING = MISSING


class SourceStatus(WireModel):
    model_config = ConfigDict(extra="forbid")
    changes: StrictStr | None | MISSING = Field(default=MISSING, title="Changes")
    error: StrictStr | None | MISSING = Field(default=MISSING, title="Error")
    feed_id: StrictStr = Field(..., title="Feed Id")
    gap: StrictStr | None | MISSING = Field(default=MISSING, title="Gap")
    title: StrictStr = Field(..., title="Title")


class SubscriptionFolder(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr
    position: StrictInt
    title: StrictStr


class TelegramPeer(WireModel):
    model_config = ConfigDict(extra="forbid")
    account: StrictStr = Field(
        ..., max_length=80, min_length=1, pattern="^[a-zA-Z0-9_-]+$", title="Account"
    )
    peer: StrictStr = Field(
        ...,
        max_length=100,
        min_length=1,
        pattern="^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$",
        title="Peer",
    )


class TelegramRead(WireModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictInt = Field(..., title="Confirmed")
    error: StrictStr = Field(..., title="Error")
    feed_id: StrictStr = Field(..., title="Feed Id")
    initial: StrictInt | None = Field(..., title="Initial")
    pending: StrictInt = Field(..., title="Pending")
    waiting: StrictStr = Field(..., title="Waiting")


class TelegramTopic(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt
    title: StrictStr


class UnknownProvenance(WireModel):
    model_config = ConfigDict(extra="forbid")
    model: None = Field(..., title="Model")


class ValidationError(WireModel):
    ctx: dict[str, Any] | MISSING = MISSING
    input: Any | MISSING = MISSING
    loc: list[StrictStr | StrictInt] = Field(..., title="Location")
    msg: StrictStr = Field(..., title="Message")
    type: StrictStr = Field(..., title="Error Type")
    url: StrictStr | MISSING = MISSING


class Version(WireModel):
    model_config = ConfigDict(extra="forbid")
    attachments: list[Attachment] = Field(..., title="Attachments")
    full: StrictBool = Field(..., title="Full")
    images: list[StrictStr] = Field(..., title="Images")
    reason: StrictStr = Field(..., title="Reason")
    text: StrictStr = Field(..., title="Text")
    title: StrictStr = Field(..., title="Title")


class YouTube(WireModel):
    model_config = ConfigDict(extra="forbid")
    address: StrictStr = Field(..., title="Address")
    has_password: StrictBool = Field(..., title="Has Password")
    has_username: StrictBool = Field(..., title="Has Username")
    mode: Literal["direct", "proxy"] = Field(..., title="Mode")
    next_at: StrictFloat = Field(..., title="Next At")
    paused: StrictBool = Field(..., title="Paused")
    reason: StrictStr = Field(..., title="Reason")
    strikes: StrictInt = Field(..., title="Strikes")


class YouTubeSettings(WireModel):
    model_config = ConfigDict(extra="forbid")
    address: StrictStr | MISSING = Field(default=MISSING, max_length=4096)
    clear_credentials: StrictBool | MISSING = MISSING
    mode: Literal["direct", "proxy"] | MISSING = MISSING
    password: StrictStr | MISSING = Field(default=MISSING, max_length=4096)
    username: StrictStr | MISSING = Field(default=MISSING, max_length=4096)


class AddFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder: StrictStr = Field(
        default="Без папки", max_length=200, min_length=1, title="Folder"
    )
    initial_history: InitialHistory | MISSING = MISSING
    title: StrictStr = Field(..., max_length=200, min_length=1, title="Title")
    url: StrictStr = Field(..., max_length=4000, min_length=1, title="Url")
    verification: StrictStr = Field(
        ..., max_length=100, min_length=1, title="Verification"
    )


class CodexCheck(WireModel):
    model_config = ConfigDict(extra="forbid")
    auth: StrictStr | MISSING = MISSING
    available: StrictBool
    limits_checked: StrictBool
    local: StrictBool | MISSING = MISSING
    quota_windows: list[QuotaWindow] | MISSING = MISSING
    reason: StrictStr
    version: StrictStr | None | MISSING = MISSING


class CodexState(WireModel):
    model_config = ConfigDict(extra="forbid")
    auth: StrictStr | MISSING = MISSING
    available: StrictBool
    config: CodexSettings | None
    limits_checked: StrictBool
    local: StrictBool | MISSING = MISSING
    quota_windows: list[QuotaWindow] | MISSING = MISSING
    reason: StrictStr
    version: StrictStr | None | MISSING = MISSING


class CompletionConflict(WireModel):
    model_config = ConfigDict(extra="forbid")
    message: StrictStr
    preview: CompletionPreview


class ConnectTelegram(WireModel):
    model_config = ConfigDict(extra="forbid")
    account: StrictStr = Field(
        ..., max_length=80, min_length=1, pattern="^[a-zA-Z0-9_-]+$", title="Account"
    )
    auto_read: StrictBool = Field(..., title="Auto Read")
    feed_id: StrictStr | None | MISSING = Field(default=MISSING, title="Feed Id")
    folder: StrictStr = Field(..., max_length=200, min_length=1, title="Folder")
    initial_history: InitialHistory | None | MISSING = MISSING
    peer: StrictStr = Field(
        ...,
        max_length=100,
        min_length=1,
        pattern="^(@?[a-zA-Z][a-zA-Z0-9_]{3,31}|-[0-9]+)$",
        title="Peer",
    )
    topic_id: TopicId | None | MISSING = Field(default=MISSING, title="Topic Id")


class Error(WireModel):
    model_config = ConfigDict(extra="forbid")
    detail: StrictStr | CompletionConflict


class HTTPValidationError(WireModel):
    detail: list[ValidationError] | MISSING = Field(default=MISSING, title="Detail")


class History(WireModel):
    model_config = ConfigDict(extra="forbid")
    available: StrictInt = Field(..., title="Available")
    cancelled: StrictBool | MISSING = Field(default=MISSING, title="Cancelled")
    count: StrictInt | None = Field(..., title="Count")
    count_text: StrictStr | None = Field(..., title="Count Text")
    date: StrictStr | None = Field(..., title="Date")
    discovery_error: StrictStr | None | MISSING = Field(
        default=MISSING, title="Discovery Error"
    )
    discovery_retry_at: StrictFloat | None | MISSING = Field(
        default=MISSING, title="Discovery Retry At"
    )
    errors: list[HistoryError] = Field(..., title="Errors")
    frozen: StrictBool = Field(..., title="Frozen")
    incomplete: StrictInt = Field(..., title="Incomplete")
    loaded: StrictInt = Field(..., title="Loaded")
    mode: Literal["count", "date"] = Field(..., title="Mode")
    paused: StrictBool = Field(..., title="Paused")
    requests: StrictInt | MISSING = Field(default=MISSING, title="Requests")
    resume: StrictInt | MISSING = Field(default=MISSING, title="Resume")
    retrying: StrictInt = Field(..., title="Retrying")
    run: StrictStr | MISSING = Field(default=MISSING, title="Run")
    scan_complete: StrictBool = Field(..., title="Scan Complete")
    since: StrictFloat | None = Field(..., title="Since")
    state: Literal[
        "pending", "loading", "done", "selecting", "paused", "waiting", "cancelled"
    ] = Field(..., title="State")
    timezone: StrictStr = Field(..., title="Timezone")
    unavailable: StrictInt = Field(..., title="Unavailable")
    viewed: StrictInt = Field(..., title="Viewed")
    waiting: StrictBool | MISSING = Field(default=MISSING, title="Waiting")
    warnings: list[StrictStr] | MISSING = Field(default=MISSING, title="Warnings")


class Point(WireModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[Reference] = Field(..., title="Sources")
    text: StrictStr = Field(..., title="Text")


class Problem(WireModel):
    model_config = ConfigDict(extra="forbid")
    action: ProblemAction | None
    code: StrictStr = Field(..., title="Code")
    consequence: StrictStr = Field(..., title="Consequence")
    created_at: StrictFloat = Field(..., title="Created At")
    finished_at: StrictFloat | None = Field(..., title="Finished At")
    id: StrictStr = Field(..., title="Id")
    next_at: StrictFloat | None = Field(..., title="Next At")
    next_step: StrictStr = Field(..., title="Next Step")
    objects: list[ProblemObject] = Field(..., title="Objects")
    operation: StrictStr | MISSING = Field(default=MISSING, title="Operation")
    outcome: Literal["resolved", "irrelevant"] | None = Field(..., title="Outcome")
    reason: StrictStr | MISSING = Field(default=MISSING, title="Reason")
    request_id: StrictStr | MISSING = Field(default=MISSING, title="Request Id")
    section: Literal["action", "wait", "object"] = Field(..., title="Section")
    state: Literal["current", "retrying", "finished"] = Field(..., title="State")
    title: StrictStr = Field(..., title="Title")


class ProblemSnapshot(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., title="Count")
    current: list[Problem] = Field(..., title="Current")
    recent: list[Problem] = Field(..., title="Recent")
    updated_at: StrictFloat = Field(..., title="Updated At")


class Status(WireModel):
    model_config = ConfigDict(extra="forbid")
    debug_count: StrictInt | MISSING = Field(default=MISSING, title="Debug Count")
    debug_persistence_error: StrictStr | MISSING = Field(
        default=MISSING, title="Debug Persistence Error"
    )
    deleting: StrictBool | MISSING = Field(default=MISSING, title="Deleting")
    error: StrictStr | None | MISSING = Field(default=MISSING, title="Error")
    errors: list[StrictStr] = Field(..., title="Errors")
    initial_histories: list[InitialHistoryStatus] | MISSING = Field(
        default=MISSING, title="Initial Histories"
    )
    jobs: list[Job] | MISSING = Field(default=MISSING, title="Jobs")
    live_tabs: StrictInt | MISSING = Field(default=MISSING, title="Live Tabs")
    problems: ProblemSnapshot | MISSING = MISSING
    ready_sources: StrictInt = Field(..., title="Ready Sources")
    sources: list[SourceStatus] = Field(..., title="Sources")
    stage: StrictStr = Field(..., title="Stage")
    stopped: StrictBool | MISSING = Field(default=MISSING, title="Stopped")
    telegram_reads: list[TelegramRead] | MISSING = Field(
        default=MISSING, title="Telegram Reads"
    )
    total_sources: StrictInt = Field(..., title="Total Sources")
    viewed_ids: list[StrictStr] | MISSING = Field(default=MISSING, title="Viewed Ids")
    youtube: YouTube | None | MISSING = MISSING


class SubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    auto_read: StrictBool | None | MISSING = Field(default=MISSING, title="Auto Read")
    connection: StrictStr | MISSING = Field(default=MISSING, title="Connection")
    folder_id: StrictStr = Field(..., title="Folder Id")
    id: StrictStr = Field(..., title="Id")
    identity: StrictStr | MISSING = Field(default=MISSING, title="Identity")
    importance: Literal["normal", "elevated", "high"] | MISSING = Field(
        default=MISSING, title="Importance"
    )
    initial_days: StrictInt | MISSING = Field(default=MISSING, title="Initial Days")
    initial_history: History | None | MISSING = MISSING
    kind: Literal["demo", "rss", "telegram", "youtube", "unresolved"] = Field(
        ..., title="Kind"
    )
    legacy_since: StrictFloat | None | MISSING = Field(
        default=MISSING, title="Legacy Since"
    )
    original_urls: list[StrictStr] | MISSING = Field(
        default=MISSING, title="Original Urls"
    )
    public_ai: StrictBool | None | MISSING = Field(default=MISSING, title="Public Ai")
    read_history: StrictBool | None | MISSING = Field(
        default=MISSING, title="Read History"
    )
    telegram_account: StrictStr | None | MISSING = Field(
        default=MISSING, title="Telegram Account"
    )
    telegram_peer: StrictInt | None | MISSING = Field(
        default=MISSING, title="Telegram Peer"
    )
    telegram_topic: StrictInt | None | MISSING = Field(
        default=MISSING, title="Telegram Topic"
    )
    telegram_username: StrictStr | None | MISSING = Field(
        default=MISSING, title="Telegram Username"
    )
    title: StrictStr = Field(..., title="Title")
    transport: Literal["demo", "rss", "telegram", "youtube"] | None | MISSING = Field(
        default=MISSING, title="Transport"
    )


class Subscriptions(WireModel):
    model_config = ConfigDict(extra="forbid")
    feeds: list[SubscriptionFeed]
    folders: list[SubscriptionFolder]


class Summary(WireModel):
    model_config = ConfigDict(extra="forbid")
    points: list[Point] = Field(..., title="Points")


class Card(WireModel):
    model_config = ConfigDict(extra="forbid")
    ai: AI | None | MISSING = MISSING
    attachments: list[Attachment] | MISSING = Field(
        default=MISSING, title="Attachments"
    )
    captions: (
        Literal["available", "not_requested", "unavailable", "error"] | None | MISSING
    ) = Field(default=MISSING, title="Captions")
    changed: StrictBool | MISSING = Field(default=MISSING, title="Changed")
    complete: StrictBool = Field(..., title="Complete")
    feed_id: StrictStr = Field(..., title="Feed Id")
    feed_title: StrictStr = Field(..., title="Feed Title")
    filter: Filter | MISSING = MISSING
    folder_id: StrictStr = Field(..., title="Folder Id")
    full: StrictBool | MISSING = Field(default=MISSING, title="Full")
    id: StrictStr = Field(..., title="Id")
    images: list[StrictStr] | MISSING = Field(default=MISSING, title="Images")
    importance: Literal["normal", "elevated", "high"] = Field(..., title="Importance")
    loading: StrictBool | MISSING = Field(default=MISSING, title="Loading")
    members: list[StrictInt] | MISSING = Field(default=MISSING, title="Members")
    originals: StrictBool | MISSING = Field(default=MISSING, title="Originals")
    previous: list[Previous] | MISSING = Field(default=MISSING, title="Previous")
    provenance: (
        UnknownProvenance | CodexProvenance | LocalProvenance | None | MISSING
    ) = Field(default=MISSING, title="Provenance")
    published: StrictFloat = Field(..., title="Published")
    published_known: StrictBool = Field(..., title="Published Known")
    reason: StrictStr | MISSING = Field(default=MISSING, title="Reason")
    snippet: StrictStr = Field(..., title="Snippet")
    source_deleted: StrictBool | MISSING = Field(
        default=MISSING, title="Source Deleted"
    )
    source_order: StrictInt | None | MISSING = Field(
        default=MISSING, title="Source Order"
    )
    theses: Summary | None | MISSING = MISSING
    title: StrictStr = Field(..., title="Title")
    url: StrictStr = Field(..., title="Url")
    version: StrictStr | MISSING = Field(default=MISSING, title="Version")


class EffectiveSettings(WireModel):
    model_config = ConfigDict(extra="forbid")
    effective: EffectiveValues
    feed: SubscriptionFeed | MISSING = MISSING
    overrides: SettingOverrides


class Feed(WireModel):
    model_config = ConfigDict(extra="forbid")
    auto_read: StrictBool | None | MISSING = Field(default=MISSING, title="Auto Read")
    connection: StrictStr | MISSING = Field(default=MISSING, title="Connection")
    count: StrictInt = Field(..., title="Count")
    folder_id: StrictStr = Field(..., title="Folder Id")
    id: StrictStr = Field(..., title="Id")
    identity: StrictStr | MISSING = Field(default=MISSING, title="Identity")
    importance: Literal["normal", "elevated", "high"] | MISSING = Field(
        default=MISSING, title="Importance"
    )
    initial_days: StrictInt | MISSING = Field(default=MISSING, title="Initial Days")
    initial_history: History | None | MISSING = MISSING
    kind: Literal["demo", "rss", "telegram", "youtube", "unresolved"] = Field(
        ..., title="Kind"
    )
    legacy_since: StrictFloat | None | MISSING = Field(
        default=MISSING, title="Legacy Since"
    )
    original_urls: list[StrictStr] | MISSING = Field(
        default=MISSING, title="Original Urls"
    )
    public_ai: StrictBool | None | MISSING = Field(default=MISSING, title="Public Ai")
    read_history: StrictBool | None | MISSING = Field(
        default=MISSING, title="Read History"
    )
    telegram_account: StrictStr | None | MISSING = Field(
        default=MISSING, title="Telegram Account"
    )
    telegram_peer: StrictInt | None | MISSING = Field(
        default=MISSING, title="Telegram Peer"
    )
    telegram_topic: StrictInt | None | MISSING = Field(
        default=MISSING, title="Telegram Topic"
    )
    telegram_username: StrictStr | None | MISSING = Field(
        default=MISSING, title="Telegram Username"
    )
    title: StrictStr = Field(..., title="Title")
    transport: Literal["demo", "rss", "telegram", "youtube"] | None | MISSING = Field(
        default=MISSING, title="Transport"
    )


class FeedVerification(WireModel):
    model_config = ConfigDict(extra="forbid")
    existing: SubscriptionFeed | None
    kind: Literal["rss", "youtube"]
    title: StrictStr
    url: StrictStr
    verification: StrictStr


class Material(WireModel):
    model_config = ConfigDict(extra="forbid")
    ai: AI | None | MISSING = MISSING
    attachments: list[Attachment] | MISSING = Field(
        default=MISSING, title="Attachments"
    )
    captions: (
        Literal["available", "not_requested", "unavailable", "error"] | None | MISSING
    ) = Field(default=MISSING, title="Captions")
    changed: StrictBool | MISSING = Field(default=MISSING, title="Changed")
    complete: StrictBool = Field(..., title="Complete")
    description: StrictStr | MISSING = Field(default=MISSING, title="Description")
    display_text: StrictStr | MISSING = Field(default=MISSING, title="Display Text")
    feed_id: StrictStr = Field(..., title="Feed Id")
    filter: Filter | MISSING = MISSING
    folder_id: StrictStr | MISSING = Field(default=MISSING, title="Folder Id")
    full: StrictBool | MISSING = Field(default=MISSING, title="Full")
    id: StrictStr = Field(..., title="Id")
    images: list[StrictStr] | MISSING = Field(default=MISSING, title="Images")
    importance: Literal["normal", "elevated", "high"] | MISSING = Field(
        default=MISSING, title="Importance"
    )
    loading: StrictBool | MISSING = Field(default=MISSING, title="Loading")
    members: list[StrictInt] | MISSING = Field(default=MISSING, title="Members")
    originals: StrictBool | MISSING = Field(default=MISSING, title="Originals")
    previous: list[Previous] | MISSING = Field(default=MISSING, title="Previous")
    provenance: (
        UnknownProvenance | CodexProvenance | LocalProvenance | None | MISSING
    ) = Field(default=MISSING, title="Provenance")
    published: StrictFloat = Field(..., title="Published")
    published_known: StrictBool | MISSING = Field(
        default=MISSING, title="Published Known"
    )
    reason: StrictStr | MISSING = Field(default=MISSING, title="Reason")
    source_deleted: StrictBool | MISSING = Field(
        default=MISSING, title="Source Deleted"
    )
    source_order: StrictInt | None | MISSING = Field(
        default=MISSING, title="Source Order"
    )
    text: StrictStr = Field(..., title="Text")
    theses: Summary | None | MISSING = MISSING
    title: StrictStr = Field(..., title="Title")
    url: StrictStr = Field(..., title="Url")
    version: StrictStr | MISSING = Field(default=MISSING, title="Version")


class Navigation(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., title="Count")
    feeds: list[Feed] = Field(..., title="Feeds")
    folders: list[Folder] = Field(..., title="Folders")
