"""Single-source connections and archive controls over the reader runtime."""
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from oko.reader.http_schemas import SchemaRoute

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response
from pydantic import ValidationError

from oko.reader.generated import models as wire
from oko.reader import snapshots as facts
from oko.reader import projection_v3 as project
from oko.reader.commands_v3 import track
from oko.reader.runtime import Runtime, ActionError
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.subscriptions import AddFeed, FeedURL


def selection(value: wire.HistorySelection) -> InitialHistory:
    from oko.reader.api_v3 import ApiError, invalid
    item = value.root
    try:
        if isinstance(item, wire.HistoryCountSelection):
            return InitialHistory(mode='count', count=int(item.count.root))
        return InitialHistory(mode='date', count=None, date=item.date.root, timezone=item.timezone.root)
    except (ValidationError, ValueError):
        raise ApiError(422, invalid('body', 'initial_history')) from None


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import ApiError, no_query
    router = APIRouter(route_class=SchemaRoute, dependencies=[Depends(no_query)])

    @router.get('/api/v3/subscriptions', response_model=wire.SubscriptionCatalog)
    async def subscriptions() -> wire.SubscriptionCatalog:
        saved = runtime.require_store().subscriptions
        return wire.SubscriptionCatalog(folders=[wire.SubscriptionFolder.model_validate(v) for v in saved.folders()], feeds=[project.subscription(facts.SubscriptionFeed.model_validate(v), saved.telegram.binding(v['id'])) for v in saved.feeds()])

    @router.get('/api/v3/feeds/{feed}/avatar/{revision}')
    async def feed_avatar(feed: str, revision: str) -> Response:
        current = runtime.require_store()
        row = current.db.execute("SELECT a.image,a.mime FROM telegram_avatars a JOIN feeds f ON f.id=a.feed WHERE a.feed=? AND a.digest=? AND f.connection!='deleting'", (feed, revision)).fetchone()
        if not row:
            raise ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message='Аватар недоступен', details=wire.ResourceNotFoundErrorDetails(resource=wire.AffectedResource(wire.FeedResource(kind='feed',feed_id=wire.FeedId(feed)))), command_id=None, diagnostic_id=None)))
        return Response(bytes(row['image']), media_type=row['mime'])

    def folder_id(folder: str) -> wire.FolderId:
        from oko.reader.api_v3 import invalid
        try:
            return wire.FolderId(folder)
        except ValueError:
            raise ApiError(422, invalid('path', 'folder')) from None

    @router.patch('/api/v3/folders/{folder}', response_model=wire.SubscriptionFolder)
    async def rename_folder(request: Request, folder: str, body: wire.RenameFolderRequest) -> wire.SubscriptionFolder:
        context = wire.CommandContext(wire.CommandRenameFolder(operation='rename_folder', folder_id=folder_id(folder)))
        async with track(runtime, request, context):
            return wire.SubscriptionFolder.model_validate(await runtime.rename_folder(folder, body.title))

    @router.delete('/api/v3/folders/{folder}', response_model=wire.CommandAcknowledgement)
    async def remove_folder(request: Request, folder: str) -> wire.CommandAcknowledgement:
        context = wire.CommandContext(wire.CommandRemoveFolder(operation='remove_folder', folder_id=folder_id(folder)))
        async with track(runtime, request, context):
            await runtime.remove_folder(folder)
            return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @router.post('/api/v3/feeds/verify', response_model=wire.FeedVerification)
    async def verify(request: Request, body: wire.FeedUrlRequest) -> wire.FeedVerification:
        async with track(runtime, request, wire.CommandContext(wire.CommandVerifyFeed(operation='verify_feed'))):
            return await verify_source(body)

    async def verify_source(body: wire.FeedUrlRequest) -> wire.FeedVerification:
        try:
            result = facts.FeedVerification.model_validate(await runtime.verify_feed(FeedURL(url=body.url.root)))
        except ActionError as error:
            if error.code == 'runtime_disabled':
                raise
            if runtime.stopped:
                raise ApiError(409, wire.ErrorEnvelope(wire.RuntimeStoppedError(code='runtime_stopped', message='Око остановлено', details=None, diagnostic_id=None, command_id=None))) from None
            if error.code != 'source_unavailable':
                raise ApiError(400, wire.ErrorEnvelope(wire.SourceConnectionInvalidError(code='source_connection_invalid', message=str(error.detail), details=wire.SourceConnectionInvalidErrorDetails(reason='invalid_source'), diagnostic_id=None, command_id=None))) from None
            raise ApiError(503, wire.ErrorEnvelope(wire.SourceUnavailableError(code='source_unavailable', message=str(error.detail), details=wire.SourceUnavailableErrorDetails(retry_at=None), diagnostic_id=None, command_id=None))) from None
        if isinstance(result.existing, facts.SubscriptionFeed):
            return wire.FeedVerification(wire.ExistingFeedVerification(state='existing', existing_feed=project.subscription(result.existing)))
        return wire.FeedVerification(wire.NewFeedVerification(state='new', verification=result.verification,
            candidate=wire.FeedCandidate(url=wire.SourceUrl(result.url), title=result.title, kind=result.kind)))

    @router.post('/api/v3/feeds', response_model=wire.SubscriptionFeed)
    async def add(request: Request, body: wire.AddFeedRequest) -> wire.SubscriptionFeed:
        history = selection(body.initial_history) if isinstance(body.initial_history, wire.HistorySelection) else InitialHistory()
        async with track(runtime, request, wire.CommandContext(wire.CommandAddFeed(operation='add_feed'))):
            result = await runtime.add_feed(AddFeed(url=body.url.root, verification=body.verification, title=body.title, folder=body.folder, initial_history=history))
            return project.subscription(facts.SubscriptionFeed.model_validate(result))

    @router.get('/api/v3/youtube', response_model=wire.YouTubeState)
    async def youtube() -> wire.YouTubeState:
        return project.youtube(facts.YouTube.model_validate(runtime.youtube_status()))

    @router.patch('/api/v3/youtube', response_model=wire.YouTubeState)
    async def configure(request: Request, body: wire.YouTubePatchRequest) -> wire.YouTubeState:
        values: dict[str, str | bool] = {}
        if 'mode' in body.model_fields_set and isinstance(body.mode, str):
            values['mode'] = body.mode
        if 'address' in body.model_fields_set and isinstance(body.address, str):
            values['address'] = body.address
        if isinstance(body.credentials, wire.CredentialsChange):
            credentials = body.credentials.root
            if isinstance(credentials, wire.CredentialsClear):
                values['clear_credentials'] = True
            elif isinstance(credentials, wire.CredentialsReplace):
                values.update(username=credentials.username, password=credentials.password)
        async with track(runtime, request, wire.CommandContext(wire.CommandYoutubeConfigure(operation='youtube_configure'))):
            return project.youtube(facts.YouTube.model_validate(await runtime.configure_youtube(values)))

    def history(identity: str, result: object) -> wire.FeedHistoryState:
        return project.history(facts.InitialHistoryStatus.model_validate(dict(facts.History.model_validate(result).model_dump(), feed_id=identity)))

    @asynccontextmanager
    async def archive(request: Request, identity: str, context: wire.CommandContext) -> AsyncIterator[None]:
        async with track(runtime, request, context):
            try:
                yield
            except ActionError as error:
                if runtime.stopped or error.kind == 'unavailable' or error.code == 'runtime_disabled':
                    raise
                command_id = wire.CommandId(request.state.command_id)
                saved = runtime.require_store().subscriptions.feeds(identity)
                if not saved:
                    raise ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message=str(error.detail), diagnostic_id=None, command_id=command_id,
                        details=wire.ResourceNotFoundErrorDetails(resource=wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=wire.FeedId(identity))))))) from None
                state = project.subscription(facts.SubscriptionFeed.model_validate(saved[0])).root.history
                raise ApiError(409, wire.ErrorEnvelope(wire.HistoryConflictError(code='history_conflict', message=str(error.detail), diagnostic_id=None, command_id=command_id,
                    details=wire.HistoryConflictErrorDetails(feed_id=wire.FeedId(identity), history=state)))) from None

    @router.patch('/api/v3/feeds/{identity}/history', response_model=wire.FeedHistoryState)
    async def pause(request: Request, identity: str, body: wire.HistoryPauseRequest) -> wire.FeedHistoryState:
        async with archive(request, identity, wire.CommandContext(wire.CommandHistoryPause(operation='history_pause', feed_id=wire.FeedId(identity)))):
            return history(identity, await runtime.pause_history(identity, body.paused))

    @router.post('/api/v3/feeds/{identity}/history', response_model=wire.FeedHistoryState)
    async def start(request: Request, identity: str, body: wire.HistorySelection) -> wire.FeedHistoryState:
        selected = selection(body)
        async with archive(request, identity, wire.CommandContext(wire.CommandHistoryStart(operation='history_start', feed_id=wire.FeedId(identity)))):
            return history(identity, await runtime.change_history(identity, selected))

    @router.delete('/api/v3/feeds/{identity}/history', response_model=wire.FeedHistoryState)
    async def cancel(request: Request, identity: str) -> wire.FeedHistoryState:
        async with archive(request, identity, wire.CommandContext(wire.CommandHistoryCancel(operation='history_cancel', feed_id=wire.FeedId(identity)))):
            return history(identity, await runtime.change_history(identity, None))

    app.include_router(router)
