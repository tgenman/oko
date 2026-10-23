"""Telegram binding/profile HTTP actions over Runtime, with canonical typed errors."""
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Literal

from oko.reader.http_schemas import SchemaRoute

from fastapi import APIRouter, Depends, FastAPI, Request

from oko.reader.generated import models as wire
from oko.reader.runtime import Runtime, ActionError
from oko.reader.commands_v3 import track
from oko.reader.connections_v3 import selection
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.telegram import TelegramConflict


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import ApiError, no_query
    router = APIRouter(route_class=SchemaRoute, dependencies=[Depends(no_query)])

    @asynccontextmanager
    async def operation(request: Request, context: dict[str, str]) -> AsyncIterator[None]:
        async with track(runtime, request, wire.CommandContext.model_validate(context)):
            try:
                yield
            except TelegramConflict as error:
                raise ApiError(error.status, wire.ErrorEnvelope.model_validate(dict(code=error.code, message=str(error), details=error.details,
                    diagnostic_id=None, command_id=request.state.command_id))) from None
            except ActionError as error:
                if runtime.stopped or error.kind == 'unavailable' or error.code == 'runtime_disabled':
                    raise
                code = 'profile_unavailable' if context['operation'] == 'telegram_profile_save' else 'source_unavailable'
                raise ApiError(503, wire.ErrorEnvelope.model_validate(dict(code=code, message=str(error.detail), details=dict(retry_at=None),
                    diagnostic_id=None, command_id=request.state.command_id))) from None
            except ValueError:
                raise ApiError(400, wire.ErrorEnvelope(wire.SourceConnectionInvalidError(code='source_connection_invalid',
                    message='Неверный адрес Telegram', details=wire.SourceConnectionInvalidErrorDetails(reason='invalid_source'),
                    diagnostic_id=None, command_id=wire.CommandId(request.state.command_id)))) from None

    @router.get('/api/v3/telegram/profiles', response_model=wire.TelegramProfiles)
    async def profiles() -> wire.TelegramProfiles:
        return wire.TelegramProfiles.model_validate(await runtime.telegram_profiles())

    @router.put('/api/v3/telegram/profiles/{role}', response_model=wire.TelegramProfileState)
    async def save(request: Request, role: Literal['personal', 'collection'], body: wire.TelegramProfileRequest) -> wire.TelegramProfileState:
        async with operation(request, dict(operation='telegram_profile_save', role=role)):
            return wire.TelegramProfileState.model_validate(await runtime.save_telegram_profile(role, body.configuration_ref.root))

    @router.post('/api/v3/telegram/connect', response_model=wire.TelegramConnectResult)
    async def connect(request: Request, body: wire.TelegramConnectRequest) -> wire.TelegramConnectResult:
        value = body.root
        history = selection(value.initial_history) if isinstance(value.initial_history, wire.HistoryCountSelection) else InitialHistory()
        role = value.access.profile.root if isinstance(value, wire.TelegramClientConnectRequest) else None
        auto_read = value.auto_read if isinstance(value, wire.TelegramClientConnectRequest) and 'auto_read' in value.model_fields_set else None
        feed_id=value.feed_id.root if isinstance(value.feed_id,wire.FeedId) else None
        async with operation(request, dict(operation='telegram_connect')):
            result = await runtime.connect_telegram_binding(value.source_locator, value.folder, history, role, auto_read,feed_id)
            binding = runtime.require_store().subscriptions.telegram.binding(result['id'])
            return wire.TelegramConnectResult(feed_id=wire.FeedId(result['id']), access=wire.TelegramConfirmedAccess.model_validate(binding))

    @router.delete('/api/v3/feeds/{identity}', response_model=wire.RemoveFeedResult)
    async def remove(request: Request, identity: str) -> wire.RemoveFeedResult:
        async with operation(request, dict(operation='remove_feed', feed_id=identity)):
            try:
                await runtime.remove_feed(identity)
            except LookupError:
                raise ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message='Фид не найден',
                    details=wire.ResourceNotFoundErrorDetails(resource=wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=wire.FeedId(identity)))), diagnostic_id=None, command_id=wire.CommandId(request.state.command_id)))) from None
            return wire.RemoveFeedResult(feed_id=wire.FeedId(identity), state='deleting')

    app.include_router(router)
