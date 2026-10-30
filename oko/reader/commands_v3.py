from oko.execution import read_locked
"""Typed command observations over the existing persisted action journal."""
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from oko.reader.http_schemas import SchemaRoute

from fastapi import APIRouter, Depends, FastAPI, Request

from oko.reader.generated import models as wire
from oko.application.reader import ReaderApplication
from oko.execution import ActionError
from oko.storage.problems import CommandContextMismatch
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from oko.reader.api_v3 import ApiError


def context_record(context: wire.CommandContext) -> dict[str, object]:
    context = canonical_context(context)
    value = context.root
    identity = context.model_dump_json()
    objects: list[dict[str, str]] = []
    if isinstance(value, wire.CommandSettingsPatch):
        target = value.target.root
        objects = [{'kind': 'settings', 'id': target.owner if isinstance(target.owner, str) else target.owner.root}]
    elif isinstance(value, (wire.CommandCompleteFeed, wire.CommandFeedPatch, wire.CommandRemoveFeed, wire.CommandHistoryPause, wire.CommandHistoryStart, wire.CommandHistoryCancel)):
        objects = [{'kind':'feed', 'id':value.feed_id.root}]
    elif isinstance(value, (wire.CommandCompleteFolder, wire.CommandRenameFolder, wire.CommandRemoveFolder)):
        objects = [{'kind':'folder', 'id':value.folder_id.root}]
    elif isinstance(value, (wire.CommandCodexSave, wire.CommandCodexCheck, wire.CommandProcessorSave, wire.CommandProcessorCheck)):
        objects = [{'kind': 'settings', 'id': 'global'}]
    elif isinstance(value, (wire.CommandRegenerate, wire.CommandRetryTheses, wire.CommandRetryMaterial, wire.CommandRetryFilter, wire.CommandViewed)):
        objects = [{'kind':'material', 'id':value.material_id.root}]
    return dict(identity=identity, title='Не удалось выполнить действие', objects=objects, action=None,
                discardable=isinstance(value, (wire.CommandSettingsPatch, wire.CommandCodexSave, wire.CommandProcessorSave)), command_context=context.model_dump(mode='json'))


@asynccontextmanager
async def track(runtime: ReaderApplication, request: Request, context: wire.CommandContext) -> AsyncIterator[None]:
    from oko.reader.api_v3 import ApiError
    identity = request.state.command_id
    context = canonical_context(context)
    check_context(runtime, identity, context)
    prior = runtime.queries.command_record(identity)
    try:
        await runtime.action_started(identity, context_record(context), restart=True, expected_identity=prior.identity if prior else None)
    except CommandContextMismatch:
        raise mismatch(identity) from None
    try:
        yield
    except ActionError as error:
        failure = action_error(error, context, identity, stopped=runtime.stopped)
        await runtime.action_failed(identity, failure.value.model_dump_json())
        await runtime.action_finished(identity, False)
        raise failure from None
    except ApiError as error:
        await runtime.action_failed(identity, error.value.model_dump_json())
        await runtime.action_finished(identity, False)
        raise
    except Exception:
        await runtime.action_finished(identity, False)
        raise
    else:
        await runtime.action_finished(identity, True)


def stored_context(value: str | None) -> wire.CommandContext | None:
    if value is None:
        return None
    try:
        return wire.CommandContext.model_validate_json(value)
    except ValueError:
        return None


def stored_error(value: str | None) -> wire.ErrorEnvelope | None:
    if value is None:
        return None
    try:
        return wire.ErrorEnvelope.model_validate_json(value)
    except (ValueError,TypeError):
        return None


def check_context(runtime: ReaderApplication, identity: str, context: wire.CommandContext) -> None:
    row = runtime.queries.command_record(identity)
    old = stored_context(row.context_json) if row else None
    if row is not None and (old is None or canonical_context(old) != canonical_context(context) or row.error_json is not None and stored_error(row.error_json) is None):
        raise mismatch(identity)


def receipt(runtime: ReaderApplication, identity: str) -> wire.CommandReceipt:
    from oko.reader.api_v3 import timestamp
    row = runtime.queries.command_record(identity)
    command_id = wire.CommandId(identity)
    context = stored_context(row.context_json) if row else None
    if row is None or row.result=='unknown' or context is None:
        return wire.CommandReceipt(wire.CommandReceiptUnknown(id=command_id,outcome='unknown'))
    saved_context=wire.StoredCommandContext.model_validate(context.model_dump(mode='json'))
    updated = timestamp(row.updated)
    if row.result == 'succeeded':
        return wire.CommandReceipt(wire.CommandReceiptSucceeded(id=command_id, context=saved_context, outcome='succeeded', updated_at=updated))
    if row.result == 'abandoned':
        return wire.CommandReceipt(wire.CommandReceiptAbandoned(id=command_id, context=saved_context, outcome='abandoned', updated_at=updated))
    if row.result == 'failed':
        error = stored_error(row.error_json) if row.error_json is not None else wire.ErrorEnvelope(wire.ActionInvalidError(code='action_invalid', message='Действие не выполнено', details=wire.ActionInvalidErrorDetails(reason='command_failed'), diagnostic_id=None, command_id=command_id))
        if error is None:
            return wire.CommandReceipt(wire.CommandReceiptUnknown(id=command_id,outcome='unknown'))
        return wire.CommandReceipt(wire.CommandReceiptFailed(id=command_id, context=saved_context, outcome='failed', updated_at=updated, error=error))
    return wire.CommandReceipt(wire.CommandReceiptPending(id=command_id, context=saved_context, outcome='pending', updated_at=updated))


def register(app: FastAPI, runtime: ReaderApplication) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(route_class=SchemaRoute, dependencies=[Depends(no_query)])
    @router.post('/api/v3/problems/{identity}/abandon', response_model=wire.CommandAcknowledgement)
    async def abandon(request: Request, identity: str) -> wire.CommandAcknowledgement:
        def safe_context(value: object, saved_identity: str, error: object) -> bool:
            try:
                parsed=wire.CommandContext.model_validate(value)
                saved=wire.CommandContext.model_validate_json(saved_identity)
            except (ValueError,TypeError):
                return False
            if error is not None and (not isinstance(error,str) or stored_error(error) is None):
                return False
            return isinstance(parsed.root,(wire.CommandSettingsPatch,wire.CommandCodexSave,wire.CommandProcessorSave)) and canonical_context(parsed)==canonical_context(saved)
        await runtime.abandon_problem(identity,safe_context)
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @router.get('/api/v3/commands/{identity}', response_model=wire.CommandReceipt)
    @read_locked(runtime.execution)
    async def get_receipt(identity: str) -> wire.CommandReceipt:
        from oko.reader.api_v3 import ApiError, invalid
        try:
            wire.CommandId(identity)
        except ValueError:
            raise ApiError(422, invalid('path', 'identity')) from None
        return receipt(runtime, identity)

    @router.post('/api/v3/command-observations', response_model=wire.CommandReceipt)
    async def observe(body: wire.CommandObservation) -> wire.CommandReceipt:
        check_context(runtime, body.id.root, body.context)
        record = runtime.queries.command_record(body.id.root)
        if record and record.result in ('succeeded', 'failed', 'abandoned'):
            return receipt(runtime, body.id.root)
        context = context_record(body.context)
        if record:
            context['identity'] = record.identity
        try:
            await runtime.report_action(body.id.root, context, body.result)
        except CommandContextMismatch:
            raise mismatch(body.id.root) from None
        return receipt(runtime, body.id.root)

    app.include_router(router)


def action_error(error: ActionError, context: wire.CommandContext, identity: str, *, stopped: bool = False) -> 'ApiError':
    from oko.reader.api_v3 import ApiError
    command_id = wire.CommandId(identity)
    if error.code == 'runtime_disabled':
        from oko.reader.api_v3 import disabled_error
        return disabled_error(identity)
    if stopped:
        return ApiError(409, wire.ErrorEnvelope(wire.RuntimeStoppedError(code='runtime_stopped', message='Око остановлено', details=None, diagnostic_id=None, command_id=command_id)))
    value = context.root
    if isinstance(value, (wire.CommandRenameFolder, wire.CommandRemoveFolder)):
        if error.kind == 'missing':
            resource = wire.AffectedResource(wire.FolderResource(kind='folder', folder_id=value.folder_id))
            return ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message=str(error.detail), details=wire.ResourceNotFoundErrorDetails(resource=resource), diagnostic_id=None, command_id=command_id)))
        if error.code == 'folder_not_empty':
            return ApiError(409, wire.ErrorEnvelope(wire.FolderNotEmptyError(code='folder_not_empty', message=str(error.detail), details=wire.FolderNotEmptyErrorDetails(folder_id=value.folder_id), diagnostic_id=None, command_id=command_id)))
        if error.code == 'folder_name_conflict':
            return ApiError(409, wire.ErrorEnvelope(wire.FolderNameConflictError(code='folder_name_conflict', message=str(error.detail), details=wire.FolderNameConflictErrorDetails(folder_id=value.folder_id), diagnostic_id=None, command_id=command_id)))
    if error.kind == 'missing' and isinstance(value, wire.CommandCompleteFeed):
        resource = wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=value.feed_id))
        return ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message=str(error.detail), details=wire.ResourceNotFoundErrorDetails(resource=resource), diagnostic_id=None, command_id=command_id)))
    if error.kind == 'missing' and isinstance(value, (wire.CommandViewed, wire.CommandRegenerate, wire.CommandRetryMaterial, wire.CommandRetryTheses, wire.CommandRetryFilter, wire.CommandCompleteFolder)):
        resource = wire.AffectedResource(wire.FolderResource(kind='folder', folder_id=value.folder_id) if isinstance(value, wire.CommandCompleteFolder) else wire.MaterialResource(kind='material', material_id=value.material_id))
        return ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message=str(error.detail), details=wire.ResourceNotFoundErrorDetails(resource=resource), diagnostic_id=None, command_id=command_id)))
    if isinstance(value, wire.CommandAddFeed) and error.code == 'verification_expired':
        return ApiError(409, wire.ErrorEnvelope(wire.VerificationExpiredError(code='verification_expired', message=str(error.detail), details=None, diagnostic_id=None, command_id=command_id)))
    if isinstance(value, (wire.CommandAddFeed, wire.CommandYoutubeConfigure)) and error.kind == 'invalid':
        return ApiError(400, wire.ErrorEnvelope(wire.SourceConnectionInvalidError(code='source_connection_invalid', message=str(error.detail), details=wire.SourceConnectionInvalidErrorDetails(reason='invalid_configuration'), diagnostic_id=None, command_id=command_id)))
    if error.kind == 'unavailable':
        return ApiError(503, wire.ErrorEnvelope(wire.StorageUnavailableError(code='storage_unavailable', message=str(error.detail), details=wire.StorageUnavailableErrorDetails(retry_at=None), diagnostic_id=None, command_id=command_id)))
    if isinstance(context.root, wire.CommandCodexSave) and error.kind == 'invalid':
        return ApiError(400, wire.ErrorEnvelope(wire.ProcessorChoiceInvalidError(code='processor_choice_invalid', message=str(error.detail), details=wire.ProcessorChoiceInvalidErrorDetails(reason='unsupported_choice'), diagnostic_id=None, command_id=command_id)))
    if isinstance(context.root, (wire.CommandCodexSave, wire.CommandCodexCheck, wire.CommandProcessorCheck)):
        return ApiError(503, wire.ErrorEnvelope(wire.ProcessorUnavailableError(code='processor_unavailable', message=str(error.detail), details=wire.ProcessorUnavailableErrorDetails(retry_at=None), diagnostic_id=None, command_id=command_id)))
    return ApiError(400, wire.ErrorEnvelope(wire.ActionInvalidError(code='action_invalid', message=str(error.detail), details=wire.ActionInvalidErrorDetails(reason='invalid_state'), diagnostic_id=None, command_id=command_id)))




def canonical_context(context: wire.CommandContext) -> wire.CommandContext:
    value = context.root
    if isinstance(value, wire.CommandSettingsPatch):
        return wire.CommandContext(value.model_copy(update={'keys': sorted(value.keys, key=lambda key: key.root)}))
    if isinstance(value, wire.CommandFeedPatch):
        return wire.CommandContext(value.model_copy(update={'fields': sorted(value.fields, key=lambda field: field.root)}))
    return context


def mismatch(identity: str) -> 'ApiError':
    from oko.reader.api_v3 import ApiError
    return ApiError(409, wire.ErrorEnvelope(wire.CommandContextMismatchError(code='command_context_mismatch', message='Контекст команды не совпадает', diagnostic_id=None,
        command_id=wire.CommandId(identity), details=wire.CommandContextMismatchErrorDetails(command_id=wire.CommandId(identity)))))
