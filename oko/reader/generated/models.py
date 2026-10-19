# Generated from api/openapi.yaml; do not edit.
from __future__ import annotations

from typing import Annotated, Literal

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

from oko.reader.wire import DateValue as date_aliased
from oko.reader.wire import TimestampValue as datetime
from oko.reader.wire import UriValue as AnyUrl
from oko.reader.wire import WireModel, boolean_constant, integer_constant


class DiagnosticId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., min_length=1)


class ActionInvalidErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    reason: StrictStr


class AllCollectionTarget(WireModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all"]


class ApiVersionRemovedErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    api_version: StrictStr


class SourcePosition(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(..., ge=0, le=9007199254740991)


class ClientPresenceLifetimePolicy(WireModel):
    model_config = ConfigDict(extra="forbid")
    grace_seconds: StrictFloat = Field(..., description="Default: 15 seconds.", gt=0.0)
    kind: Literal["client_presence"]
    lease_seconds: StrictFloat = Field(..., description="Default: 6 seconds.", gt=0.0)
    renew_after_seconds: StrictFloat = Field(
        ..., description="Default: 2 seconds; less than lease_seconds.", gt=0.0
    )


class ClientSessionId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class CodexChoice(WireModel):
    model_config = ConfigDict(extra="forbid")
    fast: StrictBool = Field(..., title="Fast")
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


class CodexChoiceRequest(WireModel):
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


class CodexConfiguration(WireModel):
    model_config = ConfigDict(extra="forbid")
    filter: CodexChoice
    theses: CodexChoice


class CodexConfigurationRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    filter: CodexChoiceRequest
    theses: CodexChoiceRequest


class CodexModelDescriptor(WireModel):
    model_config = ConfigDict(extra="forbid")
    default: StrictBool
    default_reasoning_effort: StrictStr
    display_name: StrictStr
    fast_supported: StrictBool
    model: StrictStr
    reasoning_efforts: list[StrictStr]


class CodexResultProvenance(WireModel):
    model_config = ConfigDict(extra="forbid")
    fast: StrictBool
    kind: Literal["codex"]
    model: StrictStr = Field(..., min_length=1)
    reasoning_effort: StrictStr = Field(..., min_length=1)


class CollectionTelegramProfileUnconfigured(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["collection"]
    state: Literal["unconfigured"]


class CommandAddFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["add_feed"]


class CommandCodexCheck(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["codex_check"]


class CommandCodexSave(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["codex_save"]


class CommandId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ...,
        examples=["example"],
        max_length=100,
        min_length=1,
        pattern="^[A-Za-z0-9_-]+$",
    )


class CommandPower(WireModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    operation: Literal["power"]


class CommandProcessorCheck(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["processor_check"]


class CommandProcessorSave(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["processor_save"]


class CommandReceiptUnknown(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: CommandId
    outcome: Literal["unknown"]


class CommandResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    command_id: CommandId
    kind: Literal["command"]


class CommandStop(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["stop"]


class CommandTelegramConnect(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["telegram_connect"]


class CommandVerifyFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["verify_feed"]


class CommandYoutubeConfigure(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["youtube_configure"]


class CompleteContent(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["complete"]


class CompleteFeedRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    token: StrictStr = Field(..., max_length=100, min_length=1)


class CompleteFolderRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    token: StrictStr = Field(..., max_length=100, min_length=1)


class CompletionPreview(WireModel):
    model_config = ConfigDict(extra="forbid")
    cards_count: StrictInt = Field(..., ge=0, le=9007199254740991)
    publications_count: StrictInt = Field(..., ge=0, le=9007199254740991)
    token: StrictStr = Field(..., min_length=1)


class CompositionChangedErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    preview: CompletionPreview


class ConfigurationRef(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class CredentialsClear(WireModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["clear"]


class CredentialsKeep(WireModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["keep"]


class CredentialsReplace(WireModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["replace"]
    password: StrictStr = Field(..., max_length=4096)
    username: StrictStr = Field(..., max_length=4096)


class DefaultSettingOrigin(WireModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["default"]


class DiagnosticClearFailure(WireModel):
    model_config = ConfigDict(extra="forbid")
    message: StrictStr
    state: Literal["persistence_failure"]


class DiagnosticClearSuccess(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["cleared"]


class Streams(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(..., ge=0, le=9007199254740991)


class DiagnosticIgnored(WireModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal["material_viewed"]
    state: Literal["ignored"]


class DiagnosticPersistenceFailure(WireModel):
    model_config = ConfigDict(extra="forbid")
    message: StrictStr
    state: Literal["persistence_failure"]


class ExplicitLifetimePolicy(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["explicit"]


class FeedAvatarUrl(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ...,
        examples=[
            "/api/v3/feeds/example/avatar/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        ],
        pattern="^/api/v3/feeds/[^/]+/avatar/[a-f0-9]{64}$",
    )


class FeedConnectionUrl(RootModel[AnyUrl]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: AnyUrl = Field(..., examples=["https://example.test/feed.xml"])


class FeedField(
    RootModel[
        Literal[
            "new_folder",
            "folder_id",
            "importance",
            "initial_days",
            "public_ai",
            "read_history",
            "auto_read",
            "transport",
        ]
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal[
        "new_folder",
        "folder_id",
        "importance",
        "initial_days",
        "public_ai",
        "read_history",
        "auto_read",
        "transport",
    ] = Field(..., examples=["folder_id"])


class FeedId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class FeedResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    kind: Literal["feed"]


class FeedSettingOrigin(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: FeedId
    scope: Literal["feed"]


class FeedSettingsTarget(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: FeedId
    scope: Literal["feed"]


class FeedUrlRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    url: FeedConnectionUrl


class Segments(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(..., ge=0, le=9007199254740991)


class FieldViolation(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[
        "required",
        "unknown_field",
        "invalid_type",
        "invalid_value",
        "invalid_format",
        "out_of_range",
        "duplicate_parameter",
    ]
    location: Literal["body", "path", "query", "header"]
    message: StrictStr
    segments: list[StrictStr | Segments]


class FolderId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class FolderNameConflictErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId


class FolderNotEmptyErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId


class FolderResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId
    kind: Literal["folder"]


class FolderSettingOrigin(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: FolderId
    scope: Literal["folder"]


class FolderSettingsTarget(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: FolderId
    scope: Literal["folder"]


class HistoryPauseRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    paused: StrictBool


class HostNotAllowedError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["host_not_allowed"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class IanaTimezone(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["Europe/Moscow"], min_length=1)


class IdentityUnconfirmedErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId | None


class Importance(RootModel[Literal["normal", "elevated", "high"]]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal["normal", "elevated", "high"] = Field(..., examples=["normal"])


class IncompleteContent(WireModel):
    model_config = ConfigDict(extra="forbid")
    message: StrictStr
    reason: Literal[
        "article_unavailable",
        "media_unavailable",
        "captions_unavailable",
        "source_unavailable",
        "legacy",
    ]
    state: Literal["incomplete"]


class InternalErrorError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["internal_error"]
    command_id: CommandId | None
    details: None
    diagnostic_id: StrictStr = Field(..., min_length=1)
    message: StrictStr


class KnownLocalModel(WireModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(..., min_length=1)
    state: Literal["known"]


class LegacyCommandContext(WireModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal[
        "import_subscriptions",
        "import_preview",
        "create_digest",
        "unrecognized_operation",
    ]
    operation: Literal["legacy"]


class LegacyProcessorSelection(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["legacy"]
    value: StrictStr = Field(..., max_length=200, min_length=1)


class LifetimePolicy(RootModel[ExplicitLifetimePolicy | ClientPresenceLifetimePolicy]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ExplicitLifetimePolicy | ClientPresenceLifetimePolicy = Field(
        ..., discriminator="kind", examples=[{"kind": "explicit"}]
    )


class LifetimePolicyIncompatibleError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["lifetime_policy_incompatible"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class LocalDate(RootModel[date_aliased]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: date_aliased = Field(..., examples=["2026-10-14"])


class LocalProcessorCheck(WireModel):
    model_config = ConfigDict(extra="forbid")
    locality: Literal["local", "remote"]
    model: StrictStr
    models: list[StrictStr]


class LocalProcessorConfiguration(WireModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: StrictStr = Field(..., max_length=2000, title="Endpoint")
    local_execution: StrictBool = Field(..., title="Local Execution")
    model: StrictStr = Field(..., max_length=200, min_length=1, title="Model")


class LocalProcessorConfigurationRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: StrictStr = Field(..., max_length=2000, title="Endpoint")
    local_execution: StrictBool = Field(default=False, title="Local Execution")
    model: StrictStr = Field(..., max_length=200, min_length=1, title="Model")


class LocalProcessorState(WireModel):
    model_config = ConfigDict(extra="forbid")
    configuration: LocalProcessorConfiguration | None


class MaterialId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class MaterialResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["material"]
    material_id: MaterialId


class MediaUnavailableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    name: StrictStr


class MediaUrl(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ...,
        examples=["/api/v3/materials/example/images/image.png"],
        pattern="^/api/v3/materials/",
    )


class OkoSettingOrigin(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: Literal["global"]
    scope: Literal["oko"]


class OkoSettingsTarget(WireModel):
    model_config = ConfigDict(extra="forbid")
    owner: Literal["global"]
    scope: Literal["oko"]


class OriginNotAllowedError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["origin_not_allowed"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class PendingPublicationContent(WireModel):
    model_config = ConfigDict(extra="forbid")
    availability: Literal["pending"]
    loading: StrictBool


class PersonalTelegramProfileUnconfigured(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["personal"]
    state: Literal["unconfigured"]


class PositiveDecimal(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ..., examples=["20"], max_length=4096, pattern="^[1-9][0-9]*$"
    )


class PowerRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ProblemCode(
    RootModel[
        Literal[
            "processor_not_configured",
            "processor_configuration_invalid",
            "public_ai_not_allowed",
            "source_reconnect_required",
            "source_unavailable",
            "material_incomplete",
            "processing_failed",
            "youtube_rate_limited",
            "storage_unavailable",
            "command_failed",
            "command_outcome_unknown",
            "material_save_failed",
            "material_read_failed",
            "feed_delete_failed",
            "telegram_read_failed",
            "profile_not_configured",
            "profile_unavailable",
            "account_identity_mismatch",
            "source_identity_conflict",
            "duplicate_source",
            "profile_in_use",
            "profile_account_conflict",
        ]
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal[
        "processor_not_configured",
        "processor_configuration_invalid",
        "public_ai_not_allowed",
        "source_reconnect_required",
        "source_unavailable",
        "material_incomplete",
        "processing_failed",
        "youtube_rate_limited",
        "storage_unavailable",
        "command_failed",
        "command_outcome_unknown",
        "material_save_failed",
        "material_read_failed",
        "feed_delete_failed",
        "telegram_read_failed",
        "profile_not_configured",
        "profile_unavailable",
        "account_identity_mismatch",
        "source_identity_conflict",
        "duplicate_source",
        "profile_in_use",
        "profile_account_conflict",
    ] = Field(..., examples=["processor_not_configured"])


class ProblemId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class ProblemResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["problem"]
    problem_id: ProblemId


class ProcessingCancelled(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["cancelled"]


class ProcessingDisabled(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["disabled"]


class ProcessingQueued(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["queued"]


class ProcessingReady(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["ready"]


class ProcessingRunning(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["running"]


class ProcessorChoiceInvalidErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    reason: StrictStr


class ProcessorId(RootModel[Literal["codex", "llamacpp"]]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal["codex", "llamacpp"] = Field(..., examples=["codex"])


class ProcessorResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["processor"]
    processor_id: ProcessorId


class ProcessorSelection1(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ..., examples=["codex"], max_length=200, pattern="^llamacpp:.+$"
    )


class ProcessorSelection(RootModel[Literal["codex", "llamacpp"] | ProcessorSelection1]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal["codex", "llamacpp"] | ProcessorSelection1 = Field(
        ..., examples=["codex"]
    )


class ReadingFolder(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., ge=0, le=9007199254740991)
    id: FolderId
    position: StrictInt = Field(
        ...,
        description="Persisted ordering position; demo source legitimately uses -1.",
        ge=-9007199254740991,
        le=9007199254740991,
    )
    title: StrictStr


class RecoveryConfigureProcessor(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["configure_processor"]
    processor_id: ProcessorId


class RecoveryConnectSource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["connect_source"]
    source_kind: Literal["rss", "telegram", "youtube"]


class RecoveryInspectMaterials(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["inspect_materials"]
    material_ids: list[MaterialId] = Field(..., min_length=1)


class RecoveryRegenerate(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["regenerate"]
    material_ids: list[MaterialId] = Field(..., min_length=1)
    result_kind: Literal["filter", "theses"]


class RecoveryRetryFilter(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["retry_filter"]
    material_ids: list[MaterialId] = Field(..., min_length=1)


class RecoveryRetryMaterial(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["retry_material"]
    material_ids: list[MaterialId] = Field(..., min_length=1)


class RecoveryRetryTheses(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["retry_theses"]
    material_ids: list[MaterialId] = Field(..., min_length=1)


class RemoveFeedResult(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    state: Literal["deleting", "deleted"]


class RenameFolderRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    title: StrictStr = Field(
        ...,
        description="Trimmed before saving. Must be unique among current folder titles (case-sensitive).",
        max_length=200,
        min_length=1,
        pattern="\\S",
    )


class RequestInvalidErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    violations: list[FieldViolation] = Field(..., min_length=1)


class RevisionId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["example"], min_length=1)


class RuntimeDisabledError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["runtime_disabled"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class RuntimeResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["runtime"]


class RuntimeStoppedError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["runtime_stopped"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class SessionRequiredError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["session_required"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class SettingKey(
    RootModel[
        Literal["originals", "theses", "prompt_theses", "prompt_filters", "processor"]
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal[
        "originals", "theses", "prompt_theses", "prompt_filters", "processor"
    ] = Field(..., examples=["originals"])


class SettingOrigin(
    RootModel[
        DefaultSettingOrigin
        | OkoSettingOrigin
        | FolderSettingOrigin
        | FeedSettingOrigin
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        DefaultSettingOrigin
        | OkoSettingOrigin
        | FolderSettingOrigin
        | FeedSettingOrigin
    ) = Field(..., discriminator="scope", examples=[{"scope": "default"}])


class SettingScopeInvalidErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    reason: StrictStr


class FeedRefreshMinutes(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(
        ...,
        description="Global Oko setting only; minutes between automatic collection rounds. Null resets to 15.",
        ge=1,
        le=1440,
    )


class PromptFilters(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., max_length=20000)


class PromptTheses(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., max_length=20000)


class SettingsPatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_refresh_minutes: FeedRefreshMinutes | None | MISSING = Field(
        default=MISSING,
        description="Global Oko setting only; minutes between automatic collection rounds. Null resets to 15.",
    )
    originals: StrictBool | None | MISSING = MISSING
    processor: ProcessorSelection | None | MISSING = MISSING
    prompt_filters: PromptFilters | None | MISSING = MISSING
    prompt_theses: PromptTheses | None | MISSING = MISSING
    theses: StrictBool | None | MISSING = MISSING


class SettingsTarget(
    RootModel[OkoSettingsTarget | FolderSettingsTarget | FeedSettingsTarget]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: OkoSettingsTarget | FolderSettingsTarget | FeedSettingsTarget = Field(
        ..., discriminator="scope", examples=[{"owner": "global", "scope": "oko"}]
    )


class SourceConnectionInvalidErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    reason: StrictStr


class SourceIdentityConflictErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId | None


class SourceLocator(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(
        ...,
        description="Saved source address or identifier, retained verbatim. New connections validate the locator against their adapter; this value is not a filesystem path.",
        examples=["example"],
        min_length=1,
    )


class SourceUrl(RootModel[AnyUrl]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: AnyUrl = Field(..., examples=["https://example.test/post/1"])


class StoredProcessorSelection(
    RootModel[ProcessorSelection | LegacyProcessorSelection]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ProcessorSelection | LegacyProcessorSelection = Field(..., examples=["codex"])


class SubscriptionFolder(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: FolderId
    position: StrictInt = Field(
        ...,
        description="Persisted ordering position; demo source legitimately uses -1.",
        ge=-9007199254740991,
        le=9007199254740991,
    )
    title: StrictStr


class TelegramMessageId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["123"], pattern="^[1-9][0-9]*$")


class TelegramPeerId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["-1001234567890"], pattern="^-?[1-9][0-9]*$")


class TelegramProfileConfiguration(WireModel):
    model_config = ConfigDict(extra="forbid")
    configuration_ref: ConfigurationRef
    label: StrictStr


class TelegramProfileRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    configuration_ref: ConfigurationRef


class TelegramRole(RootModel[Literal["personal", "collection"]]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal["personal", "collection"] = Field(..., examples=["personal"])


class TelegramUnconfiguredAccess(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["unconfigured"]
    source_locator: StrictStr = Field(..., min_length=1)


class TelegramUserId(RootModel[StrictStr]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictStr = Field(..., examples=["9007199254740993"], pattern="^[1-9][0-9]*$")


class TelegramWebAccess(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["web"]
    preview_url: SourceUrl
    username: StrictStr = Field(..., min_length=1)


class TelegramWebAccessRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["web"]


class Timestamp(RootModel[datetime]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: datetime = Field(
        ...,
        description="RFC 3339 timestamp with a mandatory timezone offset, normalized to UTC in responses.",
        examples=["2026-10-15T10:00:00Z"],
    )


class UnknownLocalModel(WireModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["unknown"]


class UnknownResultProvenance(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["unknown"]
    reason: Literal["legacy", "not_recorded"]


class VerificationExpiredError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["verification_expired"]
    command_id: CommandId | None
    details: None
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class VersionUnavailableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    number: StrictInt = Field(..., ge=1, le=9007199254740991)


class WaitingReason(
    RootModel[
        Literal[
            "processor_unavailable",
            "processor_not_configured",
            "public_ai_not_allowed",
            "source_unavailable",
            "youtube_rate_limited",
            "profile_unavailable",
            "storage_unavailable",
            "client_presence_required",
            "retry_scheduled",
            "telegram_read_frontier",
        ]
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: Literal[
        "processor_unavailable",
        "processor_not_configured",
        "public_ai_not_allowed",
        "source_unavailable",
        "youtube_rate_limited",
        "profile_unavailable",
        "storage_unavailable",
        "client_presence_required",
        "retry_scheduled",
        "telegram_read_frontier",
    ] = Field(..., examples=["processor_unavailable"])


class WorkIdle(WireModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["idle"]


class Total(RootModel[StrictInt]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: StrictInt = Field(..., ge=0, le=9007199254740991)


class WorkProgress(WireModel):
    model_config = ConfigDict(extra="forbid")
    completed: StrictInt = Field(..., ge=0, le=9007199254740991)
    total: Total | None


class WorkWaiting(WireModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["waiting"]
    reason: WaitingReason
    retry_at: Timestamp | None


class YouTubeState(WireModel):
    model_config = ConfigDict(extra="forbid")
    address: StrictStr
    admission: Literal["ready", "paused", "waiting"]
    has_password: StrictBool
    has_username: StrictBool
    mode: Literal["direct", "proxy"]
    reason: WaitingReason | None
    retry_at: Timestamp | None
    strikes: StrictInt = Field(..., ge=0, le=9007199254740991)


class YoutubeSourceReference(WireModel):
    model_config = ConfigDict(extra="forbid")
    captions: Literal["available", "not_requested", "unavailable", "error"]
    feed_id: FeedId
    kind: Literal["youtube"]
    source_url: SourceUrl


class AccessImmutableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId | None


class AccountIdentityMismatchErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: TelegramRole


class ActionInvalidError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["action_invalid"]
    command_id: CommandId | None
    details: ActionInvalidErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class AddFeed409Errors(RootModel[VerificationExpiredError | RuntimeStoppedError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: VerificationExpiredError | RuntimeStoppedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "verification_expired",
                "command_id": None,
                "details": None,
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class ApiVersionRemovedError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["api_version_removed"]
    command_id: CommandId | None
    details: ApiVersionRemovedErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class Attachment(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "video", "audio", "circle", "file"]
    mime: StrictStr
    url: MediaUrl


class AuthorizationErrors(
    RootModel[SessionRequiredError | HostNotAllowedError | OriginNotAllowedError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: SessionRequiredError | HostNotAllowedError | OriginNotAllowedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "session_required",
                "command_id": None,
                "details": None,
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class ClientDiagnosticEvent(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(default=1, ge=1, le=1000000, title="Count")
    error_type: StrictStr = Field(default="Error", max_length=200, title="Error Type")
    event_id: StrictStr | MISSING = Field(
        default=MISSING, max_length=100, min_length=1, title="Event Id"
    )
    first_at: Timestamp | MISSING = MISSING
    location: StrictStr = Field(default="", max_length=1000, title="Location")
    material_ids: list[MaterialId] | MISSING = Field(default=MISSING, max_length=1000)
    message: StrictStr = Field(..., max_length=4000, title="Message")
    occurred_at: Timestamp | MISSING = MISSING
    sequence: StrictInt = Field(default=1, ge=1, le=9007199254740991, title="Sequence")
    source: StrictStr = Field(..., max_length=500, min_length=1, title="Source")
    stream_id: StrictStr = Field(default="", max_length=100, title="Stream Id")
    traceback: StrictStr = Field(default="", title="Traceback")


class ClientSession(WireModel):
    model_config = ConfigDict(extra="forbid")
    expires_at: Timestamp
    id: ClientSessionId
    renew_after_seconds: StrictFloat = Field(..., gt=0.0)


class ClientSessionClosed(WireModel):
    model_config = ConfigDict(extra="forbid")
    closed: Annotated[Literal[True], BeforeValidator(boolean_constant)]
    id: ClientSessionId


class ClientSessionExpiredErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    id: ClientSessionId


class CodexModelCatalog(WireModel):
    model_config = ConfigDict(extra="forbid")
    models: list[CodexModelDescriptor]


class CollectionTelegramProfileIdentityMismatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    message: StrictStr
    reason: Literal["account_identity_mismatch"]
    role: Literal["collection"]
    state: Literal["identity_mismatch"]
    verified_user_id: TelegramUserId


class CollectionTelegramProfileReady(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    role: Literal["collection"]
    state: Literal["ready"]
    verified_user_id: TelegramUserId


class CollectionTelegramProfileUnavailable(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    message: StrictStr
    reason: WaitingReason
    role: Literal["collection"]
    state: Literal["unavailable"]
    verified_user_id: TelegramUserId | None


class CommandAcknowledgement(WireModel):
    model_config = ConfigDict(extra="forbid")
    command_id: CommandId
    ok: Annotated[Literal[True], BeforeValidator(boolean_constant)]


class CommandCompleteFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    operation: Literal["complete_feed"]


class CommandCompleteFolder(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId
    operation: Literal["complete_folder"]


class CommandContextMismatchErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    command_id: CommandId


class CommandFeedPatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    fields: list[FeedField]
    operation: Literal["feed_patch"]


class CommandHistoryCancel(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    operation: Literal["history_cancel"]


class CommandHistoryPause(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    operation: Literal["history_pause"]


class CommandHistoryStart(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    operation: Literal["history_start"]


class CommandPreviewCompletion(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId
    operation: Literal["preview_completion"]


class CommandRegenerate(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    operation: Literal["regenerate"]


class CommandRemoveFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    operation: Literal["remove_feed"]


class CommandRemoveFolder(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId
    operation: Literal["remove_folder"]


class CommandRenameFolder(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: FolderId
    operation: Literal["rename_folder"]


class CommandRestoreFilter(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    operation: Literal["restore_filter"]


class CommandRetryFilter(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    operation: Literal["retry_filter"]


class CommandRetryMaterial(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    operation: Literal["retry_material"]


class CommandRetryTheses(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    operation: Literal["retry_theses"]


class CommandSettingsPatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[SettingKey]
    operation: Literal["settings_patch"]
    target: SettingsTarget


class CommandTelegramProfileSave(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["telegram_profile_save"]
    role: TelegramRole


class CommandViewed(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    operation: Literal["viewed"]


class CompositionChangedError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["composition_changed"]
    command_id: CommandId | None
    details: CompositionChangedErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class ContentCompleteness(RootModel[CompleteContent | IncompleteContent]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: CompleteContent | IncompleteContent = Field(
        ..., discriminator="state", examples=[{"state": "complete"}]
    )


class CreateClientSession409Errors(
    RootModel[LifetimePolicyIncompatibleError | RuntimeStoppedError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: LifetimePolicyIncompatibleError | RuntimeStoppedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "lifetime_policy_incompatible",
                "command_id": None,
                "details": None,
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class CredentialsChange(
    RootModel[CredentialsKeep | CredentialsClear | CredentialsReplace]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: CredentialsKeep | CredentialsClear | CredentialsReplace = Field(
        ..., discriminator="action", examples=[{"action": "keep"}]
    )


class DeletionInProgressErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId


class DemoSourceReference(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    kind: Literal["demo"]
    source_url: SourceUrl


class DiagnosticClearResult(RootModel[DiagnosticClearSuccess | DiagnosticClearFailure]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: DiagnosticClearSuccess | DiagnosticClearFailure = Field(
        ..., discriminator="state", examples=[{"state": "cleared"}]
    )


class DiagnosticEntry(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., ge=1, le=9007199254740991)
    error_type: StrictStr
    first_at: Timestamp
    id: StrictStr = Field(..., min_length=1)
    last_at: Timestamp
    location: StrictStr
    material_ids: list[MaterialId]
    message: StrictStr
    source: StrictStr
    streams: dict[str, Streams]
    traceback: StrictStr


class DiagnosticJournal(WireModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[DiagnosticEntry]
    persistence_error: StrictStr | None


class EffectiveFeedRefreshMinutes(WireModel):
    model_config = ConfigDict(extra="forbid")
    origin: DefaultSettingOrigin | OkoSettingOrigin = Field(..., discriminator="scope")
    value: StrictInt = Field(..., ge=1, le=1440)


class EffectiveOriginals(WireModel):
    model_config = ConfigDict(extra="forbid")
    origin: SettingOrigin
    value: StrictBool


class EffectiveProcessor(WireModel):
    model_config = ConfigDict(extra="forbid")
    origin: SettingOrigin
    value: StoredProcessorSelection


class EffectivePromptFilters(WireModel):
    model_config = ConfigDict(extra="forbid")
    origin: SettingOrigin
    value: StrictStr = Field(..., max_length=20000)


class EffectivePromptTheses(WireModel):
    model_config = ConfigDict(extra="forbid")
    origin: SettingOrigin
    value: StrictStr = Field(..., max_length=20000)


class EffectiveTheses(WireModel):
    model_config = ConfigDict(extra="forbid")
    origin: SettingOrigin
    value: StrictBool


class FeedCandidate(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rss", "youtube"]
    title: StrictStr
    url: SourceUrl


class FeedCollectionTarget(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    scope: Literal["feed"]


class FeedPatchRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    auto_read: StrictBool | None | MISSING = MISSING
    folder_id: FolderId | MISSING = MISSING
    importance: Importance | MISSING = MISSING
    initial_days: StrictInt | MISSING = Field(default=MISSING, ge=1, le=3650)
    new_folder: StrictStr | MISSING = Field(
        default=MISSING,
        description="Trimmed before saving; case-sensitive uniqueness.",
        max_length=200,
        min_length=1,
        pattern="\\S",
    )
    public_ai: StrictBool | MISSING = MISSING
    read_history: StrictBool | MISSING = MISSING
    transport: Literal["telegram", "rss", "youtube"] | None | MISSING = MISSING


class FolderNameConflictError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["folder_name_conflict"]
    command_id: CommandId | None
    details: FolderNameConflictErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class FolderNotEmptyError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["folder_not_empty"]
    command_id: CommandId | None
    details: FolderNotEmptyErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class HistoryAbsent(WireModel):
    model_config = ConfigDict(extra="forbid")
    legacy_since: Timestamp | None
    state: Literal["absent"]


class HistoryCountSelection(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: PositiveDecimal
    mode: Literal["count"]


class HistoryDateSelection(WireModel):
    model_config = ConfigDict(extra="forbid")
    date: LocalDate
    mode: Literal["date"]
    timezone: IanaTimezone


class HistoryDiscoveryProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: WaitingReason
    message: StrictStr = Field(
        ...,
        description="Server-produced publication text after accepted filtering; clients do not reapply business filtering rules.",
    )
    retry_at: Timestamp | None


class HistoryItemError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["source_unavailable", "material_incomplete", "storage_unavailable"]
    material_id: MaterialId
    message: StrictStr


class HistoryProgress(WireModel):
    model_config = ConfigDict(extra="forbid")
    available: StrictInt = Field(..., ge=0, le=9007199254740991)
    discovery_problem: HistoryDiscoveryProblem | None
    errors: list[HistoryItemError]
    incomplete: StrictInt = Field(..., ge=0, le=9007199254740991)
    loaded: StrictInt = Field(..., ge=0, le=9007199254740991)
    manifest_complete: StrictBool = Field(
        ...,
        description="The selected archive composition is fixed; distinct from discovery scan completion.",
    )
    requests: StrictInt = Field(..., ge=0, le=9007199254740991)
    retrying: StrictInt = Field(..., ge=0, le=9007199254740991)
    scan_complete: StrictBool
    unavailable: StrictInt = Field(..., ge=0, le=9007199254740991)
    viewed: StrictInt = Field(..., ge=0, le=9007199254740991)
    warnings: list[StrictStr]


class HistorySelection(RootModel[HistoryCountSelection | HistoryDateSelection]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: HistoryCountSelection | HistoryDateSelection = Field(
        ..., discriminator="mode", examples=[{"count": "20", "mode": "count"}]
    )


class HistoryWaiting(WireModel):
    model_config = ConfigDict(extra="forbid")
    progress: HistoryProgress
    reason: WaitingReason
    retry_at: Timestamp | None
    run: StrictStr = Field(..., min_length=1)
    selection: HistorySelection
    state: Literal["waiting"]


class IdentityUnconfirmedError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["identity_unconfirmed"]
    command_id: CommandId | None
    details: IdentityUnconfirmedErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class LocalModel(RootModel[KnownLocalModel | UnknownLocalModel]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: KnownLocalModel | UnknownLocalModel = Field(
        ..., discriminator="state", examples=[{"name": "example", "state": "known"}]
    )


class LocalResultProvenance(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["local"]
    model: LocalModel
    processor_id: Literal["llamacpp"]


class MediaUnavailableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["media_unavailable"]
    command_id: CommandId | None
    details: MediaUnavailableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class NewFeedVerification(WireModel):
    model_config = ConfigDict(extra="forbid")
    candidate: FeedCandidate
    state: Literal["new"]
    verification: StrictStr = Field(..., min_length=1)


class PersonalTelegramProfileIdentityMismatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    message: StrictStr
    reason: Literal["account_identity_mismatch"]
    role: Literal["personal"]
    state: Literal["identity_mismatch"]
    verified_user_id: TelegramUserId


class PersonalTelegramProfileReady(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    role: Literal["personal"]
    state: Literal["ready"]
    verified_user_id: TelegramUserId


class PersonalTelegramProfileUnavailable(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    message: StrictStr
    reason: WaitingReason
    role: Literal["personal"]
    state: Literal["unavailable"]
    verified_user_id: TelegramUserId | None


class PreviousVersionReference(WireModel):
    model_config = ConfigDict(extra="forbid")
    number: StrictInt = Field(..., ge=1, le=9007199254740991)
    revision_id: RevisionId
    url: MediaUrl


class ProcessingWaiting(WireModel):
    model_config = ConfigDict(extra="forbid")
    reason: WaitingReason
    retry_at: Timestamp | None
    state: Literal["waiting"]


class ProcessorChoiceInvalidError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["processor_choice_invalid"]
    command_id: CommandId | None
    details: ProcessorChoiceInvalidErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class ProcessorUnavailableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    retry_at: Timestamp | None


class ProfileAccountConflictErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: TelegramRole


class ProfileInUseErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: TelegramRole


class ProfileNotConfiguredErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: TelegramRole


class ProfileUnavailableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    retry_at: Timestamp | None


class Provenance(
    RootModel[UnknownResultProvenance | CodexResultProvenance | LocalResultProvenance]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: UnknownResultProvenance | CodexResultProvenance | LocalResultProvenance = (
        Field(
            ...,
            discriminator="kind",
            examples=[{"kind": "unknown", "reason": "legacy"}],
        )
    )


class PublicationReference(WireModel):
    model_config = ConfigDict(extra="forbid")
    material_id: MaterialId
    source_url: SourceUrl


class PublicationVersion(WireModel):
    model_config = ConfigDict(extra="forbid")
    attachments: list[Attachment]
    completeness: ContentCompleteness
    images: list[MediaUrl]
    number: StrictInt = Field(..., ge=1, le=9007199254740991)
    revision_id: RevisionId
    text: StrictStr
    title: StrictStr


class QuotaWindow(WireModel):
    model_config = ConfigDict(extra="forbid")
    resets_at: Timestamp | None
    used_percent: StrictFloat = Field(..., ge=0.0, le=100.0)


class RecoveryChangeSetting(WireModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[SettingKey] = Field(..., min_length=1)
    kind: Literal["change_setting"]
    target: SettingsTarget


class RecoveryConfigureTelegramProfile(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["configure_telegram_profile"]
    role: TelegramRole


class RemoveFolder409Errors(RootModel[FolderNotEmptyError | RuntimeStoppedError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: FolderNotEmptyError | RuntimeStoppedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "folder_not_empty",
                "command_id": None,
                "details": {"folder_id": "example"},
                "diagnostic_id": None,
                "message": "example",
            },
            {
                "code": "runtime_stopped",
                "command_id": None,
                "details": None,
                "diagnostic_id": None,
                "message": "example",
            },
        ],
    )


class RenameFolder409Errors(RootModel[FolderNameConflictError | RuntimeStoppedError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: FolderNameConflictError | RuntimeStoppedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "folder_name_conflict",
                "command_id": None,
                "details": {"folder_id": "example"},
                "diagnostic_id": None,
                "message": "example",
            },
            {
                "code": "runtime_stopped",
                "command_id": None,
                "details": None,
                "diagnostic_id": None,
                "message": "example",
            },
        ],
    )


class RequestInvalidError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["request_invalid"]
    command_id: CommandId | None
    details: RequestInvalidErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class RssSourceReference(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    kind: Literal["rss"]
    source_url: SourceUrl


class SettingScopeInvalidError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["setting_scope_invalid"]
    command_id: CommandId | None
    details: SettingScopeInvalidErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class SettingsPatch400Errors(
    RootModel[SettingScopeInvalidError | ProcessorChoiceInvalidError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: SettingScopeInvalidError | ProcessorChoiceInvalidError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "setting_scope_invalid",
                "command_id": None,
                "details": {"reason": "example"},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class SettingsResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["settings"]
    target: SettingsTarget


class SourceConnectionInvalidError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["source_connection_invalid"]
    command_id: CommandId | None
    details: SourceConnectionInvalidErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class SourceIdentityConflictError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["source_identity_conflict"]
    command_id: CommandId | None
    details: SourceIdentityConflictErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class SourceState(WireModel):
    model_config = ConfigDict(extra="forbid")
    changes: StrictStr | None
    feed_id: FeedId
    gap: StrictStr | None
    message: StrictStr | None
    reason: WaitingReason | None
    state: Literal["ready", "collecting", "unavailable", "deleting"]
    title: StrictStr


class SourceUnavailableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    retry_at: Timestamp | None


class StorageUnavailableErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    retry_at: Timestamp | None


class StoredOverrides(WireModel):
    model_config = ConfigDict(extra="forbid")
    originals: StrictBool | MISSING = MISSING
    processor: StoredProcessorSelection | MISSING = MISSING
    prompt_filters: StrictStr | MISSING = Field(default=MISSING, max_length=20000)
    prompt_theses: StrictStr | MISSING = Field(default=MISSING, max_length=20000)
    theses: StrictBool | MISSING = MISSING


class TelegramClientAccess(WireModel):
    model_config = ConfigDict(extra="forbid")
    account_user_id: TelegramUserId
    kind: Literal["client"]
    peer_id: TelegramPeerId
    role: TelegramRole


class TelegramClientAccessRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["client"]
    profile: TelegramRole


class TelegramClientConnectRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramClientAccessRequest
    auto_read: StrictBool = False
    folder: StrictStr = Field(..., max_length=200, min_length=1)
    initial_history: HistorySelection = Field(
        default={"count": "20", "mode": "count"},
        description="Omitted entire field defaults to count 20. Explicit null or an incomplete object is invalid.",
        validate_default=True,
    )
    source_locator: StrictStr = Field(..., max_length=4000, min_length=1)


class TelegramConfirmedAccess(RootModel[TelegramWebAccess | TelegramClientAccess]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: TelegramWebAccess | TelegramClientAccess = Field(
        ...,
        discriminator="kind",
        examples=[
            {
                "kind": "web",
                "preview_url": "https://example.test/post/1",
                "username": "example",
            }
        ],
    )


class TelegramConnectResult(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramConfirmedAccess
    feed_id: FeedId


class TelegramLegacyClientAccess(WireModel):
    model_config = ConfigDict(extra="forbid")
    alias: StrictStr = Field(..., min_length=1)
    kind: Literal["legacy_client"]
    peer_id: TelegramPeerId | None
    source_locator: StrictStr = Field(..., min_length=1)


class TelegramProfileIdentityMismatch(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    message: StrictStr
    reason: Literal["account_identity_mismatch"]
    role: TelegramRole
    state: Literal["identity_mismatch"]
    verified_user_id: TelegramUserId


class TelegramProfileReady(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    role: TelegramRole
    state: Literal["ready"]
    verified_user_id: TelegramUserId


class TelegramProfileResource(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["telegram_profile"]
    role: TelegramRole


class TelegramProfileUnavailable(WireModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: Timestamp
    configuration_ref: ConfigurationRef
    message: StrictStr
    reason: WaitingReason
    role: TelegramRole
    state: Literal["unavailable"]
    verified_user_id: TelegramUserId | None


class TelegramProfileUnconfigured(WireModel):
    model_config = ConfigDict(extra="forbid")
    role: TelegramRole
    state: Literal["unconfigured"]


class TelegramReadState(WireModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: TelegramMessageId | None
    error: StrictStr | None
    feed_id: FeedId
    initial: TelegramMessageId | None
    pending: StrictInt = Field(..., ge=0, le=9007199254740991)
    waiting: WaitingReason | None


class TelegramWebConnectRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramWebAccessRequest | MISSING = MISSING
    folder: StrictStr = Field(..., max_length=200, min_length=1)
    initial_history: HistorySelection = Field(
        default={"count": "20", "mode": "count"},
        description="Omitted entire field defaults to count 20. Explicit null or an incomplete object is invalid.",
        validate_default=True,
    )
    source_locator: StrictStr = Field(..., max_length=4000, min_length=1)


class ThesesPoint(WireModel):
    model_config = ConfigDict(extra="forbid")
    references: list[PublicationReference]
    text: StrictStr


class UnfinishedProcessingAttempt(
    RootModel[
        ProcessingQueued
        | ProcessingRunning
        | ProcessingWaiting
        | ProcessingCancelled
        | ProcessingDisabled
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        ProcessingQueued
        | ProcessingRunning
        | ProcessingWaiting
        | ProcessingCancelled
        | ProcessingDisabled
    ) = Field(..., discriminator="state", examples=[{"state": "queued"}])


class VersionUnavailableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["version_unavailable"]
    command_id: CommandId | None
    details: VersionUnavailableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class WorkCollecting(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId | None
    phase: Literal["collecting"]
    progress: WorkProgress


class WorkDeleting(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId | None
    phase: Literal["deleting"]
    progress: WorkProgress


class WorkProcessing(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["filter", "theses"]
    phase: Literal["processing"]
    progress: WorkProgress


class YouTubePatchRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    address: StrictStr | MISSING = Field(default=MISSING, max_length=4096)
    credentials: CredentialsChange | MISSING = MISSING
    mode: Literal["direct", "proxy"] | MISSING = MISSING


class AccessImmutableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["access_immutable"]
    command_id: CommandId | None
    details: AccessImmutableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class AccountIdentityMismatchError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["account_identity_mismatch"]
    command_id: CommandId | None
    details: AccountIdentityMismatchErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class AddFeedRequest(WireModel):
    model_config = ConfigDict(extra="forbid")
    folder: StrictStr = Field(default="Без папки", max_length=200, min_length=1)
    initial_history: HistorySelection = Field(
        default={"count": "20", "mode": "count"},
        description="Omitted entire field defaults to count 20. Explicit null or an incomplete object is invalid.",
        validate_default=True,
    )
    title: StrictStr = Field(..., max_length=200, min_length=1)
    url: FeedConnectionUrl
    verification: StrictStr = Field(..., max_length=100, min_length=1)


class AffectedResource(
    RootModel[
        MaterialResource
        | FeedResource
        | FolderResource
        | ProblemResource
        | CommandResource
        | ProcessorResource
        | TelegramProfileResource
        | RuntimeResource
        | SettingsResource
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        MaterialResource
        | FeedResource
        | FolderResource
        | ProblemResource
        | CommandResource
        | ProcessorResource
        | TelegramProfileResource
        | RuntimeResource
        | SettingsResource
    ) = Field(
        ...,
        discriminator="kind",
        examples=[{"kind": "material", "material_id": "example"}],
    )


class AvailablePublicationContent(WireModel):
    model_config = ConfigDict(extra="forbid")
    attachments: list[Attachment]
    availability: Literal["available"]
    changed: StrictBool
    completeness: ContentCompleteness
    description: StrictStr = Field(
        ...,
        description="Server-produced publication text after accepted filtering; clients do not reapply business filtering rules.",
    )
    display_text: StrictStr = Field(
        ...,
        description="Server-produced publication text after accepted filtering; clients do not reapply business filtering rules.",
    )
    images: list[MediaUrl]
    loading: StrictBool
    previous_versions: list[PreviousVersionReference]
    revision_id: RevisionId
    source_deleted: StrictBool
    text: StrictStr
    version: StrictInt = Field(..., ge=1, le=9007199254740991)


class ClientSessionExpiredError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["client_session_expired"]
    command_id: CommandId | None
    details: ClientSessionExpiredErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class CodexAvailable(WireModel):
    model_config = ConfigDict(extra="forbid")
    auth: Literal["chatgpt", "api_key"]
    limits_checked: StrictBool
    local: StrictBool
    quota_windows: list[QuotaWindow]
    state: Literal["available"]
    version: StrictStr | None


class CodexUnavailable(WireModel):
    model_config = ConfigDict(extra="forbid")
    auth: Literal["unknown", "missing", "chatgpt", "api_key", "unchecked"]
    limits_checked: StrictBool
    local: StrictBool | None
    message: StrictStr
    quota_windows: list[QuotaWindow]
    reason: Literal[
        "not_installed", "not_authenticated", "quota_exhausted", "unavailable"
    ]
    state: Literal["unavailable"]
    version: StrictStr | None


class CollectionTarget(RootModel[AllCollectionTarget | FeedCollectionTarget]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: AllCollectionTarget | FeedCollectionTarget = Field(
        ..., discriminator="scope", examples=[{"scope": "all"}]
    )


class CollectionTelegramProfileState(
    RootModel[
        CollectionTelegramProfileUnconfigured
        | CollectionTelegramProfileReady
        | CollectionTelegramProfileUnavailable
        | CollectionTelegramProfileIdentityMismatch
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        CollectionTelegramProfileUnconfigured
        | CollectionTelegramProfileReady
        | CollectionTelegramProfileUnavailable
        | CollectionTelegramProfileIdentityMismatch
    ) = Field(
        ...,
        discriminator="state",
        examples=[{"role": "collection", "state": "unconfigured"}],
    )


class CommandCollect(WireModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["collect"]
    target: CollectionTarget


class CommandContext(
    RootModel[
        CommandRenameFolder
        | CommandRemoveFolder
        | CommandSettingsPatch
        | CommandFeedPatch
        | CommandCodexSave
        | CommandProcessorSave
        | CommandYoutubeConfigure
        | CommandTelegramConnect
        | CommandTelegramProfileSave
        | CommandAddFeed
        | CommandRemoveFeed
        | CommandHistoryStart
        | CommandHistoryPause
        | CommandHistoryCancel
        | CommandCollect
        | CommandCompleteFeed
        | CommandCompleteFolder
        | CommandStop
        | CommandPower
        | CommandRegenerate
        | CommandRetryTheses
        | CommandRetryMaterial
        | CommandRestoreFilter
        | CommandRetryFilter
        | CommandViewed
        | CommandCodexCheck
        | CommandProcessorCheck
        | CommandVerifyFeed
        | CommandPreviewCompletion
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        CommandRenameFolder
        | CommandRemoveFolder
        | CommandSettingsPatch
        | CommandFeedPatch
        | CommandCodexSave
        | CommandProcessorSave
        | CommandYoutubeConfigure
        | CommandTelegramConnect
        | CommandTelegramProfileSave
        | CommandAddFeed
        | CommandRemoveFeed
        | CommandHistoryStart
        | CommandHistoryPause
        | CommandHistoryCancel
        | CommandCollect
        | CommandCompleteFeed
        | CommandCompleteFolder
        | CommandStop
        | CommandPower
        | CommandRegenerate
        | CommandRetryTheses
        | CommandRetryMaterial
        | CommandRestoreFilter
        | CommandRetryFilter
        | CommandViewed
        | CommandCodexCheck
        | CommandProcessorCheck
        | CommandVerifyFeed
        | CommandPreviewCompletion
    ) = Field(
        ...,
        discriminator="operation",
        examples=[
            {
                "keys": [],
                "operation": "settings_patch",
                "target": {"owner": "global", "scope": "oko"},
            }
        ],
    )


class CommandContextMismatchError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["command_context_mismatch"]
    command_id: CommandId | None
    details: CommandContextMismatchErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class CommandObservation(WireModel):
    model_config = ConfigDict(extra="forbid")
    context: CommandContext
    id: CommandId
    result: Literal["failed", "unknown"]


class CompleteFolder409Errors(RootModel[CompositionChangedError | RuntimeStoppedError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: CompositionChangedError | RuntimeStoppedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "composition_changed",
                "command_id": None,
                "details": {
                    "preview": {
                        "cards_count": 0,
                        "publications_count": 0,
                        "token": "example",
                    }
                },
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class DeletionInProgressError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["deletion_in_progress"]
    command_id: CommandId | None
    details: DeletionInProgressErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class DiagnosticAccepted(WireModel):
    model_config = ConfigDict(extra="forbid")
    entry: DiagnosticEntry
    state: Literal["accepted"]


class DiagnosticReceipt(
    RootModel[DiagnosticIgnored | DiagnosticAccepted | DiagnosticPersistenceFailure]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: DiagnosticIgnored | DiagnosticAccepted | DiagnosticPersistenceFailure = Field(
        ...,
        discriminator="state",
        examples=[
            {
                "entry": {
                    "count": 1,
                    "error_type": "example",
                    "first_at": "2026-10-15T10:00:00Z",
                    "id": "example",
                    "last_at": "2026-10-15T10:00:00Z",
                    "location": "example",
                    "material_ids": [],
                    "message": "example",
                    "source": "example",
                    "streams": {},
                    "traceback": "example",
                },
                "state": "accepted",
            }
        ],
    )


class EffectiveSettingsValues(WireModel):
    model_config = ConfigDict(extra="forbid")
    originals: EffectiveOriginals
    processor: EffectiveProcessor
    prompt_filters: EffectivePromptFilters
    prompt_theses: EffectivePromptTheses
    theses: EffectiveTheses


class FilterResult(WireModel):
    model_config = ConfigDict(extra="forbid")
    hidden: StrictBool
    provenance: Provenance
    reason: StrictStr
    removed: list[StrictStr]
    restored: StrictBool


class FolderSettingsSnapshot(WireModel):
    model_config = ConfigDict(extra="forbid")
    effective: EffectiveSettingsValues
    overrides: StoredOverrides
    owner: FolderId
    scope: Literal["folder"]


class HistoryCancelled(WireModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["cancelled"]
    progress: HistoryProgress
    run: StrictStr = Field(..., min_length=1)
    selection: HistorySelection
    state: Literal["cancelled"]


class HistoryComplete(WireModel):
    model_config = ConfigDict(extra="forbid")
    progress: HistoryProgress
    run: StrictStr = Field(..., min_length=1)
    selection: HistorySelection
    state: Literal["complete"]


class HistoryDiscovering(WireModel):
    model_config = ConfigDict(extra="forbid")
    progress: HistoryProgress
    run: StrictStr = Field(..., min_length=1)
    selection: HistorySelection
    state: Literal["discovering"]


class HistoryFetching(WireModel):
    model_config = ConfigDict(extra="forbid")
    progress: HistoryProgress
    run: StrictStr = Field(..., min_length=1)
    selection: HistorySelection
    state: Literal["fetching"]


class HistoryNotStarted(WireModel):
    model_config = ConfigDict(extra="forbid")
    progress: HistoryProgress
    selection: HistorySelection
    state: Literal["not_started"]


class HistoryPaused(WireModel):
    model_config = ConfigDict(extra="forbid")
    paused_phase: Literal["not_started", "discovering", "fetching", "waiting"]
    progress: HistoryProgress
    run: StrictStr = Field(..., min_length=1)
    selection: HistorySelection
    state: Literal["paused"]


class HistoryState(
    RootModel[
        HistoryAbsent
        | HistoryNotStarted
        | HistoryDiscovering
        | HistoryFetching
        | HistoryPaused
        | HistoryWaiting
        | HistoryComplete
        | HistoryCancelled
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        HistoryAbsent
        | HistoryNotStarted
        | HistoryDiscovering
        | HistoryFetching
        | HistoryPaused
        | HistoryWaiting
        | HistoryComplete
        | HistoryCancelled
    ) = Field(
        ..., discriminator="state", examples=[{"legacy_since": None, "state": "absent"}]
    )


class OkoSettingsSnapshot(WireModel):
    model_config = ConfigDict(extra="forbid")
    effective: EffectiveSettingsValues
    overrides: StoredOverrides
    owner: Literal["global"]
    scope: Literal["oko"]


class PersonalTelegramProfileState(
    RootModel[
        PersonalTelegramProfileUnconfigured
        | PersonalTelegramProfileReady
        | PersonalTelegramProfileUnavailable
        | PersonalTelegramProfileIdentityMismatch
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        PersonalTelegramProfileUnconfigured
        | PersonalTelegramProfileReady
        | PersonalTelegramProfileUnavailable
        | PersonalTelegramProfileIdentityMismatch
    ) = Field(
        ...,
        discriminator="state",
        examples=[{"role": "personal", "state": "unconfigured"}],
    )


class ProcessorUnavailableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["processor_unavailable"]
    command_id: CommandId | None
    details: ProcessorUnavailableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class ProfileAccountConflictError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["profile_account_conflict"]
    command_id: CommandId | None
    details: ProfileAccountConflictErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class ProfileInUseError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["profile_in_use"]
    command_id: CommandId | None
    details: ProfileInUseErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class ProfileNotConfiguredError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["profile_not_configured"]
    command_id: CommandId | None
    details: ProfileNotConfiguredErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class ProfileUnavailableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["profile_unavailable"]
    command_id: CommandId | None
    details: ProfileUnavailableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class PublicationTheses(WireModel):
    model_config = ConfigDict(extra="forbid")
    points: list[ThesesPoint]
    provenance: Provenance


class RecoveryRetryCollection(WireModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["retry_collection"]
    target: CollectionTarget


class ResourceNotFoundErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    resource: AffectedResource


class RssSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    connection: Literal["ready", "needs_connection", "unavailable", "deleting"] = Field(
        ...,
        description="ready normalizes saved connected; unresolved identity or missing transport remain distinct domain states.",
    )
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["rss"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr
    transport: Literal["rss"] | None


class SourceUnavailableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["source_unavailable"]
    command_id: CommandId | None
    details: SourceUnavailableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class StorageUnavailableError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["storage_unavailable"]
    command_id: CommandId | None
    details: StorageUnavailableErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class StoredCommandContext(RootModel[CommandContext | LegacyCommandContext]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: CommandContext | LegacyCommandContext = Field(
        ..., examples=[{"name": "import_subscriptions", "operation": "legacy"}]
    )


class TelegramAccess(
    RootModel[
        TelegramUnconfiguredAccess
        | TelegramWebAccess
        | TelegramClientAccess
        | TelegramLegacyClientAccess
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        TelegramUnconfiguredAccess
        | TelegramWebAccess
        | TelegramClientAccess
        | TelegramLegacyClientAccess
    ) = Field(
        ...,
        discriminator="kind",
        examples=[
            {
                "kind": "web",
                "preview_url": "https://example.test/post/1",
                "username": "example",
            }
        ],
    )


class TelegramClientSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramClientAccess
    auto_read: StrictBool
    avatar_url: FeedAvatarUrl | MISSING = MISSING
    connection: Literal["ready", "unavailable", "deleting"]
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["telegram"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    read_history: StrictBool
    title: StrictStr


class TelegramConnect503Errors(
    RootModel[
        ProfileUnavailableError | SourceUnavailableError | StorageUnavailableError
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ProfileUnavailableError | SourceUnavailableError | StorageUnavailableError = (
        Field(
            ...,
            discriminator="code",
            examples=[
                {
                    "code": "profile_unavailable",
                    "command_id": None,
                    "details": {"retry_at": None},
                    "diagnostic_id": None,
                    "message": "example",
                }
            ],
        )
    )


class TelegramConnectRequest(
    RootModel[TelegramWebConnectRequest | TelegramClientConnectRequest]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: TelegramWebConnectRequest | TelegramClientConnectRequest = Field(
        ..., examples=[{"folder": "example", "source_locator": "example"}]
    )


class TelegramLegacyClientSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramLegacyClientAccess
    avatar_url: FeedAvatarUrl | MISSING = MISSING
    connection: Literal["needs_profile_confirmation", "deleting"]
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["telegram"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr


class TelegramProfileSave409Errors(
    RootModel[
        ProfileInUseError | ProfileAccountConflictError | AccountIdentityMismatchError
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        ProfileInUseError | ProfileAccountConflictError | AccountIdentityMismatchError
    ) = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "profile_in_use",
                "command_id": None,
                "details": {"role": "personal"},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class TelegramProfileSave503Errors(
    RootModel[ProfileUnavailableError | StorageUnavailableError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ProfileUnavailableError | StorageUnavailableError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "profile_unavailable",
                "command_id": None,
                "details": {"retry_at": None},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class TelegramProfileState(
    RootModel[
        TelegramProfileUnconfigured
        | TelegramProfileReady
        | TelegramProfileUnavailable
        | TelegramProfileIdentityMismatch
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        TelegramProfileUnconfigured
        | TelegramProfileReady
        | TelegramProfileUnavailable
        | TelegramProfileIdentityMismatch
    ) = Field(
        ...,
        discriminator="state",
        examples=[{"role": "personal", "state": "unconfigured"}],
    )


class TelegramProfiles(WireModel):
    model_config = ConfigDict(extra="forbid")
    collection: CollectionTelegramProfileState
    configurations: list[TelegramProfileConfiguration]
    personal: PersonalTelegramProfileState


class TelegramSourceReference(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramAccess
    feed_id: FeedId
    kind: Literal["telegram"]
    message_ids: list[TelegramMessageId]
    source_url: SourceUrl


class TelegramUnconfiguredSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramUnconfiguredAccess
    avatar_url: FeedAvatarUrl | MISSING = MISSING
    connection: Literal["needs_connection", "deleting"]
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["telegram"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr


class TelegramWebSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramWebAccess
    avatar_url: FeedAvatarUrl | MISSING = MISSING
    connection: Literal["ready", "needs_connection", "unavailable", "deleting"]
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["telegram"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr


class ThesesAttemptState(WireModel):
    model_config = ConfigDict(extra="forbid")
    attempt: UnfinishedProcessingAttempt
    diagnostic_message: StrictStr | None
    result: PublicationTheses | None


class ThesesReadyState(WireModel):
    model_config = ConfigDict(extra="forbid")
    attempt: ProcessingReady
    diagnostic_message: StrictStr | None
    result: PublicationTheses


class ThesesState(RootModel[ThesesReadyState | ThesesAttemptState]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ThesesReadyState | ThesesAttemptState = Field(
        ...,
        examples=[
            {
                "attempt": {"state": "ready"},
                "diagnostic_message": None,
                "result": {
                    "points": [],
                    "provenance": {"kind": "unknown", "reason": "legacy"},
                },
            }
        ],
    )


class UnresolvedSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    connection: Literal["ambiguous", "needs_connection", "unavailable", "deleting"] = (
        Field(
            ...,
            description="ready normalizes saved connected; unresolved identity or missing transport remain distinct domain states.",
        )
    )
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["unresolved"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr
    transport: Literal["rss"] | None


class WorkEntry(
    RootModel[WorkIdle | WorkCollecting | WorkProcessing | WorkDeleting | WorkWaiting]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: WorkIdle | WorkCollecting | WorkProcessing | WorkDeleting | WorkWaiting = (
        Field(..., discriminator="phase", examples=[{"phase": "idle"}])
    )


class YoutubeSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    connection: Literal["ready", "needs_connection", "unavailable", "deleting"] = Field(
        ...,
        description="ready normalizes saved connected; unresolved identity or missing transport remain distinct domain states.",
    )
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["youtube"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr
    transport: Literal["youtube"] | None


class AddFeed503Errors(RootModel[SourceUnavailableError | StorageUnavailableError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: SourceUnavailableError | StorageUnavailableError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "source_unavailable",
                "command_id": None,
                "details": {"retry_at": None},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class CodexAvailability(RootModel[CodexAvailable | CodexUnavailable]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: CodexAvailable | CodexUnavailable = Field(
        ...,
        discriminator="state",
        examples=[
            {
                "auth": "chatgpt",
                "limits_checked": False,
                "local": False,
                "quota_windows": [],
                "state": "available",
                "version": None,
            }
        ],
    )


class CodexModels503Errors(
    RootModel[ProcessorUnavailableError | StorageUnavailableError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ProcessorUnavailableError | StorageUnavailableError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "processor_unavailable",
                "command_id": None,
                "details": {"retry_at": None},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class CodexProcessorState(WireModel):
    model_config = ConfigDict(extra="forbid")
    availability: CodexAvailability
    configuration: CodexConfiguration | None


class Collect409Errors(RootModel[RuntimeStoppedError | DeletionInProgressError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: RuntimeStoppedError | DeletionInProgressError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "runtime_stopped",
                "command_id": None,
                "details": None,
                "diagnostic_id": None,
                "message": "example",
            },
            {
                "code": "deletion_in_progress",
                "command_id": None,
                "details": {"feed_id": "example"},
                "diagnostic_id": None,
                "message": "example",
            },
        ],
    )


class CommandReceiptAbandoned(WireModel):
    model_config = ConfigDict(extra="forbid")
    context: StoredCommandContext
    id: CommandId
    outcome: Literal["abandoned"]
    updated_at: Timestamp


class CommandReceiptPending(WireModel):
    model_config = ConfigDict(extra="forbid")
    context: StoredCommandContext
    id: CommandId
    outcome: Literal["pending"]
    updated_at: Timestamp


class CommandReceiptSucceeded(WireModel):
    model_config = ConfigDict(extra="forbid")
    context: StoredCommandContext
    id: CommandId
    outcome: Literal["succeeded"]
    updated_at: Timestamp


class DemoSubscriptionFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    connection: Literal["ready", "unavailable", "deleting"] = Field(
        ...,
        description="ready normalizes saved connected; unresolved identity or missing transport remain distinct domain states.",
    )
    folder_id: FolderId
    history: HistoryState
    id: FeedId
    identity: StrictStr = Field(..., min_length=1)
    importance: Importance
    initial_days: StrictInt = Field(..., ge=1, le=3650)
    kind: Literal["demo"]
    original_urls: list[SourceLocator]
    public_ai: StrictBool | None
    title: StrictStr
    transport: Literal["demo"] | None


class ExistingTelegramFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    access: TelegramAccess
    feed_id: FeedId
    title: StrictStr


class FeedHistoryState(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    history: HistoryState


class FilterAttemptState(WireModel):
    model_config = ConfigDict(extra="forbid")
    attempt: UnfinishedProcessingAttempt
    diagnostic_message: StrictStr | None
    result: FilterResult | None


class FilterReadyState(WireModel):
    model_config = ConfigDict(extra="forbid")
    attempt: ProcessingReady
    diagnostic_message: StrictStr | None
    result: FilterResult


class FilterState(RootModel[FilterReadyState | FilterAttemptState]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: FilterReadyState | FilterAttemptState = Field(
        ...,
        examples=[
            {
                "attempt": {"state": "ready"},
                "diagnostic_message": None,
                "result": {
                    "hidden": False,
                    "provenance": {"kind": "unknown", "reason": "legacy"},
                    "reason": "example",
                    "removed": [],
                    "restored": False,
                },
            }
        ],
    )


class HistoryConflictErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: FeedId
    history: HistoryState


class RecoveryIntent(
    RootModel[
        RecoveryConfigureProcessor
        | RecoveryConfigureTelegramProfile
        | RecoveryChangeSetting
        | RecoveryConnectSource
        | RecoveryInspectMaterials
        | RecoveryRetryMaterial
        | RecoveryRetryTheses
        | RecoveryRetryFilter
        | RecoveryRegenerate
        | RecoveryRetryCollection
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        RecoveryConfigureProcessor
        | RecoveryConfigureTelegramProfile
        | RecoveryChangeSetting
        | RecoveryConnectSource
        | RecoveryInspectMaterials
        | RecoveryRetryMaterial
        | RecoveryRetryTheses
        | RecoveryRetryFilter
        | RecoveryRegenerate
        | RecoveryRetryCollection
    ) = Field(
        ...,
        discriminator="kind",
        examples=[{"kind": "configure_processor", "processor_id": "codex"}],
    )


class ResourceNotFoundError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["resource_not_found"]
    command_id: CommandId | None
    details: ResourceNotFoundErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class RetryingProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    affected: list[AffectedResource]
    code: ProblemCode
    command_id: CommandId | None
    consequence: StrictStr = Field(
        ...,
        description="Current domain consequence, preserved independently of diagnostic details. No page or navigation instruction.",
    )
    created_at: Timestamp
    id: ProblemId
    message: StrictStr
    recovery: list[RecoveryIntent]
    retry_at: Timestamp | None
    state: Literal["retrying"]
    title: StrictStr = Field(
        ...,
        description="Human-readable subject explanation; clients branch on code, not this text.",
    )


class SourceReference(
    RootModel[
        DemoSourceReference
        | RssSourceReference
        | YoutubeSourceReference
        | TelegramSourceReference
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        DemoSourceReference
        | RssSourceReference
        | YoutubeSourceReference
        | TelegramSourceReference
    ) = Field(
        ...,
        discriminator="kind",
        examples=[
            {
                "feed_id": "example",
                "kind": "demo",
                "source_url": "https://example.test/post/1",
            }
        ],
    )


class SubscriptionFeed(
    RootModel[
        TelegramUnconfiguredSubscriptionFeed
        | DemoSubscriptionFeed
        | RssSubscriptionFeed
        | YoutubeSubscriptionFeed
        | UnresolvedSubscriptionFeed
        | TelegramWebSubscriptionFeed
        | TelegramClientSubscriptionFeed
        | TelegramLegacyClientSubscriptionFeed
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        TelegramUnconfiguredSubscriptionFeed
        | DemoSubscriptionFeed
        | RssSubscriptionFeed
        | YoutubeSubscriptionFeed
        | UnresolvedSubscriptionFeed
        | TelegramWebSubscriptionFeed
        | TelegramClientSubscriptionFeed
        | TelegramLegacyClientSubscriptionFeed
    ) = Field(
        ...,
        examples=[
            {
                "connection": "ready",
                "folder_id": "example",
                "history": {"legacy_since": None, "state": "absent"},
                "id": "example",
                "identity": "example",
                "importance": "normal",
                "initial_days": 1,
                "kind": "demo",
                "original_urls": [],
                "public_ai": None,
                "title": "example",
                "transport": None,
            }
        ],
    )


class AvailableReadableMaterial(WireModel):
    model_config = ConfigDict(extra="forbid")
    availability: Literal["available"]
    content: AvailablePublicationContent
    feed_id: FeedId
    feed_title: StrictStr
    filter: FilterState
    folder_id: FolderId
    id: MaterialId
    importance: Importance
    originals: StrictBool
    published_at: Timestamp | None
    snippet: StrictStr
    source: SourceReference
    source_position: SourcePosition | None
    source_url: SourceUrl
    theses: ThesesState
    title: StrictStr


class AvailableReadingCard(WireModel):
    model_config = ConfigDict(extra="forbid")
    attachments: list[Attachment]
    availability: Literal["available"]
    changed: StrictBool
    completeness: ContentCompleteness
    feed_id: FeedId
    feed_title: StrictStr
    filter: FilterState
    folder_id: FolderId
    id: MaterialId
    images: list[MediaUrl]
    importance: Importance
    loading: StrictBool
    originals: StrictBool
    previous_versions: list[PreviousVersionReference]
    published_at: Timestamp | None
    revision_id: RevisionId
    snippet: StrictStr
    source: SourceReference
    source_deleted: StrictBool
    source_position: SourcePosition | None
    source_url: SourceUrl
    theses: ThesesState
    title: StrictStr
    version: StrictInt = Field(..., ge=1, le=9007199254740991)


class CurrentProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    affected: list[AffectedResource]
    code: ProblemCode
    command_id: CommandId | None
    consequence: StrictStr = Field(
        ...,
        description="Current domain consequence, preserved independently of diagnostic details. No page or navigation instruction.",
    )
    created_at: Timestamp
    id: ProblemId
    message: StrictStr
    recovery: list[RecoveryIntent]
    state: Literal["current"]
    title: StrictStr = Field(
        ...,
        description="Human-readable subject explanation; clients branch on code, not this text.",
    )


class DuplicateSourceErrorDetails(WireModel):
    model_config = ConfigDict(extra="forbid")
    existing_feeds: list[ExistingTelegramFeed] = Field(..., min_length=1)


class ExistingFeedVerification(WireModel):
    model_config = ConfigDict(extra="forbid")
    existing_feed: SubscriptionFeed
    state: Literal["existing"]


class FeedSettingsSnapshot(WireModel):
    model_config = ConfigDict(extra="forbid")
    effective: EffectiveSettingsValues
    feed: SubscriptionFeed
    overrides: StoredOverrides
    owner: FeedId
    scope: Literal["feed"]


class FeedVerification(RootModel[NewFeedVerification | ExistingFeedVerification]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: NewFeedVerification | ExistingFeedVerification = Field(
        ...,
        discriminator="state",
        examples=[
            {
                "candidate": {
                    "kind": "rss",
                    "title": "example",
                    "url": "https://example.test/post/1",
                },
                "state": "new",
                "verification": "example",
            }
        ],
    )


class FinishedProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    affected: list[AffectedResource]
    code: ProblemCode
    command_id: CommandId | None
    consequence: StrictStr = Field(
        ...,
        description="Current domain consequence, preserved independently of diagnostic details. No page or navigation instruction.",
    )
    created_at: Timestamp
    finished_at: Timestamp
    id: ProblemId
    message: StrictStr
    outcome: Literal["resolved", "irrelevant"]
    recovery: list[RecoveryIntent]
    state: Literal["finished"]
    title: StrictStr = Field(
        ...,
        description="Human-readable subject explanation; clients branch on code, not this text.",
    )


class HistoryConflictError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["history_conflict"]
    command_id: CommandId | None
    details: HistoryConflictErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class HistoryStart409Errors(RootModel[HistoryConflictError | RuntimeStoppedError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: HistoryConflictError | RuntimeStoppedError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "history_conflict",
                "command_id": None,
                "details": {
                    "feed_id": "example",
                    "history": {"legacy_since": None, "state": "absent"},
                },
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class Image404Errors(RootModel[ResourceNotFoundError | MediaUnavailableError]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ResourceNotFoundError | MediaUnavailableError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "resource_not_found",
                "command_id": None,
                "details": {"resource": {"kind": "material", "material_id": "example"}},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class LegacyCurrentProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    affected: list[AffectedResource]
    code: Literal["legacy_problem"]
    command_id: CommandId | None
    consequence: StrictStr = Field(
        ...,
        description="Current domain consequence, preserved independently of diagnostic details. No page or navigation instruction.",
    )
    created_at: Timestamp
    id: ProblemId
    message: StrictStr
    raw_code: StrictStr = Field(..., min_length=1)
    recovery: list[RecoveryIntent]
    state: Literal["current"]
    title: StrictStr = Field(
        ...,
        description="Human-readable subject explanation; clients branch on code, not this text.",
    )


class LegacyFinishedProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    affected: list[AffectedResource]
    code: Literal["legacy_problem"]
    command_id: CommandId | None
    consequence: StrictStr = Field(
        ...,
        description="Current domain consequence, preserved independently of diagnostic details. No page or navigation instruction.",
    )
    created_at: Timestamp
    finished_at: Timestamp
    id: ProblemId
    message: StrictStr
    outcome: Literal["resolved", "irrelevant"]
    raw_code: StrictStr = Field(..., min_length=1)
    recovery: list[RecoveryIntent]
    state: Literal["finished"]
    title: StrictStr = Field(
        ...,
        description="Human-readable subject explanation; clients branch on code, not this text.",
    )


class LegacyRetryingProblem(WireModel):
    model_config = ConfigDict(extra="forbid")
    affected: list[AffectedResource]
    code: Literal["legacy_problem"]
    command_id: CommandId | None
    consequence: StrictStr = Field(
        ...,
        description="Current domain consequence, preserved independently of diagnostic details. No page or navigation instruction.",
    )
    created_at: Timestamp
    id: ProblemId
    message: StrictStr
    raw_code: StrictStr = Field(..., min_length=1)
    recovery: list[RecoveryIntent]
    retry_at: Timestamp | None
    state: Literal["retrying"]
    title: StrictStr = Field(
        ...,
        description="Human-readable subject explanation; clients branch on code, not this text.",
    )


class NavigationFeed(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., ge=0, le=9007199254740991)
    feed: SubscriptionFeed
    has_unviewed: StrictBool = Field(
        ...,
        description="True while any known publication in this feed is not viewed in Oko, including hidden, loading and unavailable publications. Independent of the visible count and Telegram read state.",
    )


class PendingReadableMaterial(WireModel):
    model_config = ConfigDict(extra="forbid")
    availability: Literal["pending"]
    content: PendingPublicationContent
    feed_id: FeedId
    feed_title: StrictStr
    filter: FilterState
    folder_id: FolderId
    id: MaterialId
    importance: Importance
    originals: StrictBool
    published_at: Timestamp | None
    snippet: StrictStr
    source: SourceReference
    source_position: SourcePosition | None
    source_url: SourceUrl
    theses: ThesesState
    title: StrictStr


class PendingReadingCard(WireModel):
    model_config = ConfigDict(extra="forbid")
    availability: Literal["pending"]
    feed_id: FeedId
    feed_title: StrictStr
    filter: FilterState
    folder_id: FolderId
    id: MaterialId
    importance: Importance
    loading: StrictBool
    originals: StrictBool
    published_at: Timestamp | None
    snippet: StrictStr
    source: SourceReference
    source_position: SourcePosition | None
    source_url: SourceUrl
    theses: ThesesState
    title: StrictStr


class PreviousMedia404Errors(
    RootModel[ResourceNotFoundError | VersionUnavailableError | MediaUnavailableError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ResourceNotFoundError | VersionUnavailableError | MediaUnavailableError = (
        Field(
            ...,
            discriminator="code",
            examples=[
                {
                    "code": "resource_not_found",
                    "command_id": None,
                    "details": {
                        "resource": {"kind": "material", "material_id": "example"}
                    },
                    "diagnostic_id": None,
                    "message": "example",
                }
            ],
        )
    )


class PreviousVersion404Errors(
    RootModel[ResourceNotFoundError | VersionUnavailableError]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: ResourceNotFoundError | VersionUnavailableError = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "resource_not_found",
                "command_id": None,
                "details": {"resource": {"kind": "material", "material_id": "example"}},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class Problem(
    RootModel[
        CurrentProblem
        | LegacyCurrentProblem
        | RetryingProblem
        | LegacyRetryingProblem
        | FinishedProblem
        | LegacyFinishedProblem
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        CurrentProblem
        | LegacyCurrentProblem
        | RetryingProblem
        | LegacyRetryingProblem
        | FinishedProblem
        | LegacyFinishedProblem
    ) = Field(
        ...,
        examples=[
            {
                "affected": [],
                "code": "processor_not_configured",
                "command_id": None,
                "consequence": "The saved publication remains available.",
                "created_at": "2026-10-15T10:00:00Z",
                "id": "example",
                "message": "example",
                "recovery": [],
                "state": "current",
                "title": "Publication processing is waiting",
            }
        ],
    )


class ProblemSnapshot(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., ge=0, le=9007199254740991)
    current: list[
        CurrentProblem | RetryingProblem | LegacyCurrentProblem | LegacyRetryingProblem
    ]
    recent: list[FinishedProblem | LegacyFinishedProblem]
    updated_at: Timestamp


class ReadableMaterial(RootModel[PendingReadableMaterial | AvailableReadableMaterial]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: PendingReadableMaterial | AvailableReadableMaterial = Field(
        ...,
        discriminator="availability",
        examples=[
            {
                "availability": "pending",
                "content": {"availability": "pending", "loading": False},
                "feed_id": "example",
                "feed_title": "example",
                "filter": {
                    "attempt": {"state": "ready"},
                    "diagnostic_message": None,
                    "result": {
                        "hidden": False,
                        "provenance": {"kind": "unknown", "reason": "legacy"},
                        "reason": "example",
                        "removed": [],
                        "restored": False,
                    },
                },
                "folder_id": "example",
                "id": "example",
                "importance": "normal",
                "originals": False,
                "published_at": None,
                "snippet": "example",
                "source": {
                    "feed_id": "example",
                    "kind": "demo",
                    "source_url": "https://example.test/post/1",
                },
                "source_position": None,
                "source_url": "https://example.test/post/1",
                "theses": {
                    "attempt": {"state": "ready"},
                    "diagnostic_message": None,
                    "result": {
                        "points": [],
                        "provenance": {"kind": "unknown", "reason": "legacy"},
                    },
                },
                "title": "example",
            }
        ],
    )


class ReadingCard(RootModel[PendingReadingCard | AvailableReadingCard]):
    model_config = ConfigDict(allow_inf_nan=False)
    root: PendingReadingCard | AvailableReadingCard = Field(
        ...,
        discriminator="availability",
        examples=[
            {
                "availability": "pending",
                "feed_id": "example",
                "feed_title": "example",
                "filter": {
                    "attempt": {"state": "ready"},
                    "diagnostic_message": None,
                    "result": {
                        "hidden": False,
                        "provenance": {"kind": "unknown", "reason": "legacy"},
                        "reason": "example",
                        "removed": [],
                        "restored": False,
                    },
                },
                "folder_id": "example",
                "id": "example",
                "importance": "normal",
                "loading": False,
                "originals": False,
                "published_at": None,
                "snippet": "example",
                "source": {
                    "feed_id": "example",
                    "kind": "demo",
                    "source_url": "https://example.test/post/1",
                },
                "source_position": None,
                "source_url": "https://example.test/post/1",
                "theses": {
                    "attempt": {"state": "ready"},
                    "diagnostic_message": None,
                    "result": {
                        "points": [],
                        "provenance": {"kind": "unknown", "reason": "legacy"},
                    },
                },
                "title": "example",
            }
        ],
    )


class ReadingComposition(WireModel):
    model_config = ConfigDict(extra="forbid")
    count: StrictInt = Field(..., ge=0, le=9007199254740991)
    feeds: list[NavigationFeed]
    folders: list[ReadingFolder]


class RuntimeStatus(WireModel):
    model_config = ConfigDict(extra="forbid")
    active_client_sessions: StrictInt = Field(..., ge=0, le=9007199254740991)
    api_version: StrictStr = Field(..., pattern="^\\d+\\.\\d+\\.\\d+$")
    diagnostic_count: StrictInt = Field(..., ge=0, le=9007199254740991)
    diagnostic_persistence_error: StrictStr | None
    initial_histories: list[FeedHistoryState]
    lifetime: LifetimePolicy
    problems: ProblemSnapshot
    ready_sources: StrictInt = Field(..., ge=0, le=9007199254740991)
    runtime_state: Literal["running", "disabled", "stopping", "stopped", "unavailable"]
    sources: list[SourceState]
    telegram_reads: list[TelegramReadState]
    total_sources: StrictInt = Field(..., ge=0, le=9007199254740991)
    viewed_ids: list[MaterialId]
    work: list[WorkEntry]
    work_allowed: StrictBool
    youtube: YouTubeState


class SubscriptionCatalog(WireModel):
    model_config = ConfigDict(extra="forbid")
    feeds: list[SubscriptionFeed]
    folders: list[SubscriptionFolder]


class DuplicateSourceError(WireModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["duplicate_source"]
    command_id: CommandId | None
    details: DuplicateSourceErrorDetails
    diagnostic_id: DiagnosticId | None
    message: StrictStr


class EffectiveSettingsSnapshot(
    RootModel[OkoSettingsSnapshot | FolderSettingsSnapshot | FeedSettingsSnapshot]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: OkoSettingsSnapshot | FolderSettingsSnapshot | FeedSettingsSnapshot = Field(
        ...,
        discriminator="scope",
        examples=[
            {
                "effective": {
                    "originals": {"origin": {"scope": "default"}, "value": False},
                    "processor": {"origin": {"scope": "default"}, "value": "codex"},
                    "prompt_filters": {
                        "origin": {"scope": "default"},
                        "value": "example",
                    },
                    "prompt_theses": {
                        "origin": {"scope": "default"},
                        "value": "example",
                    },
                    "theses": {"origin": {"scope": "default"}, "value": False},
                },
                "overrides": {},
                "owner": "global",
                "scope": "oko",
            }
        ],
    )


class ErrorEnvelope(
    RootModel[
        RuntimeDisabledError
        | FolderNotEmptyError
        | FolderNameConflictError
        | ActionInvalidError
        | SettingScopeInvalidError
        | SourceConnectionInvalidError
        | ProcessorChoiceInvalidError
        | SessionRequiredError
        | HostNotAllowedError
        | OriginNotAllowedError
        | ResourceNotFoundError
        | MediaUnavailableError
        | VersionUnavailableError
        | RuntimeStoppedError
        | LifetimePolicyIncompatibleError
        | DeletionInProgressError
        | CompositionChangedError
        | VerificationExpiredError
        | HistoryConflictError
        | DuplicateSourceError
        | IdentityUnconfirmedError
        | SourceIdentityConflictError
        | AccessImmutableError
        | ProfileNotConfiguredError
        | AccountIdentityMismatchError
        | ProfileInUseError
        | ProfileAccountConflictError
        | CommandContextMismatchError
        | ApiVersionRemovedError
        | ClientSessionExpiredError
        | RequestInvalidError
        | InternalErrorError
        | StorageUnavailableError
        | ProcessorUnavailableError
        | SourceUnavailableError
        | ProfileUnavailableError
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        RuntimeDisabledError
        | FolderNotEmptyError
        | FolderNameConflictError
        | ActionInvalidError
        | SettingScopeInvalidError
        | SourceConnectionInvalidError
        | ProcessorChoiceInvalidError
        | SessionRequiredError
        | HostNotAllowedError
        | OriginNotAllowedError
        | ResourceNotFoundError
        | MediaUnavailableError
        | VersionUnavailableError
        | RuntimeStoppedError
        | LifetimePolicyIncompatibleError
        | DeletionInProgressError
        | CompositionChangedError
        | VerificationExpiredError
        | HistoryConflictError
        | DuplicateSourceError
        | IdentityUnconfirmedError
        | SourceIdentityConflictError
        | AccessImmutableError
        | ProfileNotConfiguredError
        | AccountIdentityMismatchError
        | ProfileInUseError
        | ProfileAccountConflictError
        | CommandContextMismatchError
        | ApiVersionRemovedError
        | ClientSessionExpiredError
        | RequestInvalidError
        | InternalErrorError
        | StorageUnavailableError
        | ProcessorUnavailableError
        | SourceUnavailableError
        | ProfileUnavailableError
    ) = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "action_invalid",
                "command_id": None,
                "details": {"reason": "example"},
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class TelegramConnect409Errors(
    RootModel[
        DuplicateSourceError
        | IdentityUnconfirmedError
        | SourceIdentityConflictError
        | DeletionInProgressError
        | ProfileNotConfiguredError
        | AccountIdentityMismatchError
        | RuntimeStoppedError
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        DuplicateSourceError
        | IdentityUnconfirmedError
        | SourceIdentityConflictError
        | DeletionInProgressError
        | ProfileNotConfiguredError
        | AccountIdentityMismatchError
        | RuntimeStoppedError
    ) = Field(
        ...,
        discriminator="code",
        examples=[
            {
                "code": "duplicate_source",
                "command_id": None,
                "details": {
                    "existing_feeds": [
                        {
                            "access": {
                                "kind": "web",
                                "preview_url": "https://example.test/post/1",
                                "username": "example",
                            },
                            "feed_id": "example",
                            "title": "example",
                        }
                    ]
                },
                "diagnostic_id": None,
                "message": "example",
            }
        ],
    )


class CommandReceiptFailed(WireModel):
    model_config = ConfigDict(extra="forbid")
    context: StoredCommandContext
    error: ErrorEnvelope
    id: CommandId
    outcome: Literal["failed"]
    updated_at: Timestamp


class CommandReceipt(
    RootModel[
        CommandReceiptPending
        | CommandReceiptSucceeded
        | CommandReceiptFailed
        | CommandReceiptUnknown
        | CommandReceiptAbandoned
    ]
):
    model_config = ConfigDict(allow_inf_nan=False)
    root: (
        CommandReceiptPending
        | CommandReceiptSucceeded
        | CommandReceiptFailed
        | CommandReceiptUnknown
        | CommandReceiptAbandoned
    ) = Field(
        ...,
        discriminator="outcome",
        examples=[
            {
                "context": {
                    "keys": [],
                    "operation": "settings_patch",
                    "target": {"owner": "global", "scope": "oko"},
                },
                "id": "example",
                "outcome": "pending",
                "updated_at": "2026-10-15T10:00:00Z",
            }
        ],
    )
