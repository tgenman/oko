"""Typed command observations over the existing persisted action journal."""
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, Request

from oko.reader.generated import models as wire
from oko.reader.runtime import Runtime
from oko.reader.runtime import ActionError
from oko.storage.problems import CommandContextMismatch
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from oko.reader.api_v3 import ApiError


def context_record(context: wire.CommandContext) -> dict[str, object]:
    context = canonical_context(context)
    value = context.root
    identity = context.model_dump_json()
    action: dict[str, str] | None
    objects: list[dict[str, str]]
    if isinstance(value, wire.CommandSettingsPatch):
        target = value.target.root
        keys = [key.root for key in value.keys]
        action = {'kind': 'settings', 'page': 'field/' + keys[0] if len(keys) == 1 else 'home', 'scope': target.scope, 'id': target.owner if isinstance(target.owner, str) else target.owner.root}
        objects = [{'kind': 'settings', 'id': action['id']}]
    elif isinstance(value, wire.CommandFeedPatch):
        action = {'kind': 'feed-settings', 'id': value.feed_id.root}
        objects = [{'kind':'feed', 'id':value.feed_id.root}]
    elif isinstance(value, (wire.CommandCodexSave, wire.CommandCodexCheck, wire.CommandProcessorSave, wire.CommandProcessorCheck)):
        action = {'kind': 'settings', 'page': 'codex' if isinstance(value, (wire.CommandCodexSave, wire.CommandCodexCheck)) else 'llamacpp'}
        objects = [{'kind': 'settings', 'id': 'global'}]
    elif isinstance(value, (wire.CommandRegenerate, wire.CommandRetryTheses, wire.CommandRetryMaterial, wire.CommandRestoreFilter, wire.CommandRetryFilter, wire.CommandViewed)):
        action = {'kind':'material', 'id':value.material_id.root}
        objects = [{'kind':'material', 'id':value.material_id.root}]
    else:
        action = None
        objects = []
    return dict(identity=identity, title='Не удалось выполнить действие', objects=objects, action=action,
                discardable=isinstance(value, (wire.CommandSettingsPatch, wire.CommandCodexSave, wire.CommandProcessorSave)), command_context=context.model_dump(mode='json'))


@asynccontextmanager
async def track(runtime: Runtime, request: Request, context: wire.CommandContext) -> AsyncIterator[None]:
    from oko.reader.api_v3 import ApiError
    identity = request.state.command_id
    context = canonical_context(context)
    check_context(runtime, identity, context)
    prior = runtime.require_store().problems.command_record(identity)
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


def check_context(runtime: Runtime, identity: str, context: wire.CommandContext) -> None:
    row = runtime.require_store().problems.command_record(identity)
    old = (wire.CommandContext.model_validate_json(row.context_json) if row.context_json else legacy_context(row.identity)) if row else None
    if row is not None and (old is None or canonical_context(old) != canonical_context(context)):
        raise mismatch(identity)


def receipt(runtime: Runtime, identity: str) -> wire.CommandReceipt:
    from oko.reader.api_v3 import timestamp
    row = runtime.require_store().problems.command_record(identity)
    command_id = wire.CommandId(identity)
    context = (wire.CommandContext.model_validate_json(row.context_json) if row.context_json else legacy_context(row.identity)) if row else None
    if row is None or row.result == 'unknown':
        return wire.CommandReceipt(wire.CommandReceiptUnknown(id=command_id, outcome='unknown'))
    if context is None:
        name = {'POST:/api/import':'import_subscriptions','POST:/api/import/preview':'import_preview'}.get(row.identity.rsplit(':', 1)[0], 'unrecognized_operation')
        stored_context = wire.StoredCommandContext.model_validate({'operation':'legacy','name':name})
    else:
        stored_context = wire.StoredCommandContext.model_validate(context.model_dump(mode='json'))
    updated = timestamp(row.updated)
    if row.result == 'succeeded':
        return wire.CommandReceipt(wire.CommandReceiptSucceeded(id=command_id, context=stored_context, outcome='succeeded', updated_at=updated))
    if row.result == 'abandoned':
        return wire.CommandReceipt(wire.CommandReceiptAbandoned(id=command_id, context=stored_context, outcome='abandoned', updated_at=updated))
    if row.result == 'failed':
        error = wire.ErrorEnvelope.model_validate_json(row.error_json) if row.error_json else wire.ErrorEnvelope(wire.ActionInvalidError(code='action_invalid', message='Действие не выполнено', details=wire.ActionInvalidErrorDetails(reason='command_failed'), diagnostic_id=None, command_id=command_id))
        return wire.CommandReceipt(wire.CommandReceiptFailed(id=command_id, context=stored_context, outcome='failed', updated_at=updated, error=error))
    return wire.CommandReceipt(wire.CommandReceiptPending(id=command_id, context=stored_context, outcome='pending', updated_at=updated))


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(dependencies=[Depends(no_query)])
    @router.post('/api/v3/problems/{identity}/abandon', response_model=wire.CommandAcknowledgement)
    async def abandon(request: Request, identity: str) -> wire.CommandAcknowledgement:
        await runtime.abandon_problem(identity)
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @router.get('/api/v3/commands/{identity}', response_model=wire.CommandReceipt)
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
        record = runtime.require_store().problems.command_record(body.id.root)
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
    if stopped:
        return ApiError(409, wire.ErrorEnvelope(wire.RuntimeStoppedError(code='runtime_stopped', message='Око остановлено', details=None, diagnostic_id=None, command_id=command_id)))
    if isinstance(context.root, wire.CommandCodexSave) and error.kind == 'invalid':
        return ApiError(400, wire.ErrorEnvelope(wire.ProcessorChoiceInvalidError(code='processor_choice_invalid', message=str(error.detail), details=wire.ProcessorChoiceInvalidErrorDetails(reason='unsupported_choice'), diagnostic_id=None, command_id=command_id)))
    if isinstance(context.root, (wire.CommandCodexSave, wire.CommandCodexCheck, wire.CommandProcessorCheck)):
        return ApiError(503, wire.ErrorEnvelope(wire.ProcessorUnavailableError(code='processor_unavailable', message=str(error.detail), details=wire.ProcessorUnavailableErrorDetails(retry_at=None), diagnostic_id=None, command_id=command_id)))
    return ApiError(400, wire.ErrorEnvelope(wire.ActionInvalidError(code='action_invalid', message=str(error.detail), details=wire.ActionInvalidErrorDetails(reason='invalid_state'), diagnostic_id=None, command_id=command_id)))


def legacy_context(identity: str) -> wire.CommandContext | None:
    """Read-only projection of the v1/v2 journal. New routes supply typed context."""
    method, separator, rest = identity.partition(':')
    if not separator:
        return None
    path, _, field = rest.partition(':')
    parts = path.strip('/').split('/')
    data: dict[str, object]
    if len(parts) == 4 and parts[:2] == ['api', 'settings']:
        data = {'operation': 'settings_patch', 'target': {'scope': parts[2], 'owner': parts[3]}, 'keys': [field] if field else []}
    elif parts[:2] == ['api', 'processors'] and len(parts) >= 3:
        data = {'operation': ('codex' if parts[2] == 'codex' else 'processor') + ('_save' if method == 'PUT' else '_check')}
    elif path == '/api/stop':
        data = {'operation': 'stop'}
    elif path == '/api/collect':
        data = {'operation': 'collect', 'target': {'scope': 'all'}}
    elif path == '/api/youtube':
        data = {'operation': 'youtube_configure'}
    elif parts[:2] == ['api', 'feeds'] and len(parts) == 3:
        data = {'operation': 'remove_feed' if method == 'DELETE' else 'feed_patch', 'feed_id': parts[2]}
        if method != 'DELETE':
            data['fields'] = [field] if field else []
    elif parts[:2] == ['api', 'materials'] and len(parts) >= 4:
        operation = {'viewed':'viewed','regenerate':'regenerate','retry':'retry_material','restore':'restore_filter'}.get(parts[3])
        if operation is None:
            return None
        data = {'operation':operation, 'material_id':parts[2]}
    else:
        return None
    from pydantic import ValidationError
    try:
        return wire.CommandContext.model_validate(data)
    except ValidationError:
        return None


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
