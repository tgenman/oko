"""Typed reading projection and HTTP adapter over the existing reading lifecycle."""
from oko.storage.reading import reading_snapshot
from pathlib import Path
from collections.abc import Callable
from typing import Literal, TypedDict

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.responses import Response

from oko.reader import projection_v3 as project
from oko.reader.api_v3 import ApiError, invalid, no_query, positive_version, scalar_boolean
from oko.reader.commands_v3 import track
from oko.reader.generated import models as wire
from oko.reader import snapshots as facts
from oko.reader.runtime import Runtime, ActionError
from oko.reviews.coordination import ProcessingWait


def media_url(value: str) -> wire.MediaUrl:
    return wire.MediaUrl(value)


def completeness(full: bool, reason: str, external_only: bool = False) -> wire.ContentCompleteness:
    return wire.ContentCompleteness(wire.CompleteContent(state='complete') if full else wire.IncompleteContent(state='incomplete', reason='external_attachment' if external_only else 'legacy', message=reason))


def provenance(value: facts.UnknownProvenance | facts.CodexProvenance | facts.LocalProvenance | None) -> wire.Provenance:
    if isinstance(value, facts.CodexProvenance):
        return wire.Provenance(wire.CodexResultProvenance(kind='codex', model=value.model, reasoning_effort=value.reasoning_effort, fast=value.fast))
    if isinstance(value, facts.LocalProvenance) and value.processor == 'llamacpp':
        return wire.Provenance(wire.LocalResultProvenance(kind='local', processor_id='llamacpp', model=wire.LocalModel(wire.KnownLocalModel(state='known', name=value.model) if value.model else wire.UnknownLocalModel(state='unknown'))))
    return wire.Provenance(wire.UnknownResultProvenance(kind='unknown', reason='legacy'))


def attempt(status: str, error: str | None, waiting: ProcessingWait | None = None) -> wire.UnfinishedProcessingAttempt:
    if status == 'running':
        return wire.UnfinishedProcessingAttempt(wire.ProcessingRunning(state='running'))
    if status == 'cancelled':
        return wire.UnfinishedProcessingAttempt(wire.ProcessingCancelled(state='cancelled'))
    if error:
        return wire.UnfinishedProcessingAttempt(wire.ProcessingWaiting(state='waiting', reason=wire.WaitingReason(waiting.reason if waiting else 'retry_scheduled'), retry_at=project.timestamp(waiting.retry_at) if waiting and waiting.retry_at is not None else None))
    if status == 'off':
        return wire.UnfinishedProcessingAttempt(wire.ProcessingDisabled(state='disabled'))
    return wire.UnfinishedProcessingAttempt(wire.ProcessingQueued(state='queued'))


def filter_state(value: facts.Filter | None, waiting: ProcessingWait | None) -> wire.FilterState:
    if value is None:
        return wire.FilterState(wire.FilterAttemptState(attempt=attempt('off', None), diagnostic_message=None, result=None))
    message = value.error if isinstance(value.error, str) else None
    result = wire.FilterResult(hidden=value.hidden, removed=value.removed, restored=value.restored, reason=value.reason, provenance=provenance(None)) if value.status == 'done' else None
    if result is not None:
        return wire.FilterState(wire.FilterReadyState(attempt=wire.ProcessingReady(state='ready'), diagnostic_message=message, result=result))
    return wire.FilterState(wire.FilterAttemptState(attempt=attempt(value.status, message, waiting), diagnostic_message=message, result=None))


def theses_state(value: facts.Card, waiting: ProcessingWait | None) -> wire.ThesesState:
    return theses_parts(value.theses if isinstance(value.theses, facts.Summary) else None,
        value.provenance if isinstance(value.provenance, (facts.CodexProvenance, facts.LocalProvenance, facts.UnknownProvenance)) else None,
        value.ai if isinstance(value.ai, facts.AI) else None, waiting)


def theses_parts(summary: facts.Summary | None,
                 origin: facts.CodexProvenance | facts.LocalProvenance | facts.UnknownProvenance | None,
                 ai: facts.AI | None, waiting: ProcessingWait | None) -> wire.ThesesState:
    result = wire.PublicationTheses(points=[wire.ThesesPoint(text=p.text, references=[wire.PublicationReference(material_id=wire.MaterialId(r.id), source_url=wire.SourceUrl(r.url)) for r in p.sources]) for p in summary.points], provenance=provenance(origin)) if summary is not None else None
    status = ai.status if ai is not None else 'off'
    message = ai.error if ai is not None else None
    if status == 'done' and result is not None:
        return wire.ThesesState(wire.ThesesReadyState(attempt=wire.ProcessingReady(state='ready'), diagnostic_message=message, result=result))
    return wire.ThesesState(wire.ThesesAttemptState(attempt=attempt(status, message, waiting), diagnostic_message=message, result=result))


class Common(TypedDict):
    id: wire.MaterialId
    title: str
    feed_id: wire.FeedId
    feed_title: str
    folder_id: wire.FolderId
    importance: wire.Importance
    published_at: wire.Timestamp | None
    source_url: wire.SourceUrl
    source_position: wire.SourcePosition | None
    source: wire.SourceReference
    snippet: str
    originals: bool
    filter: wire.FilterState
    theses: wire.ThesesState


def saved_filter(runtime: Runtime, item: str, value: facts.Filter | None) -> wire.FilterState:
    current=runtime.require_store()
    filtered = filter_state(value, runtime.processing_wait(item, 'filter'))
    if filtered.root.result is not None:
        saved = current.derivative_provenance(current.path(item).parent / 'filter.md', current.filters.signature(item))
        if saved:
            from pydantic import TypeAdapter
            origin: facts.CodexProvenance | facts.LocalProvenance | facts.UnknownProvenance = TypeAdapter(facts.CodexProvenance | facts.LocalProvenance | facts.UnknownProvenance).validate_python(saved)
            filtered.root.result.provenance = provenance(origin)
    return filtered


def common(runtime: Runtime, value: facts.Card, feed: wire.SubscriptionFeed) -> Common:
    current = runtime.require_store()
    source: wire.DemoSourceReference | wire.RssSourceReference | wire.YoutubeSourceReference | wire.TelegramSourceReference
    fid, url = wire.FeedId(value.feed_id), wire.SourceUrl(value.url)
    owner = feed.root
    if isinstance(owner, (wire.TelegramWebSubscriptionFeed, wire.TelegramClientSubscriptionFeed, wire.TelegramLegacyClientSubscriptionFeed, wire.TelegramUnconfiguredSubscriptionFeed)):
        source = wire.TelegramSourceReference(kind='telegram', feed_id=fid, source_url=url, access=wire.TelegramAccess(owner.access), message_ids=[wire.TelegramMessageId(str(n)) for n in value.members] if isinstance(value.members, list) else [])
    elif owner.kind == 'youtube':
        source = wire.YoutubeSourceReference(kind='youtube', feed_id=fid, source_url=url, captions=value.captions if isinstance(value.captions, str) else 'not_requested')
    elif owner.kind == 'demo':
        source = wire.DemoSourceReference(kind='demo', feed_id=fid, source_url=url)
    else:
        source = wire.RssSourceReference(kind='rss', feed_id=fid, source_url=url)
    filtered = saved_filter(runtime,value.id,value.filter if isinstance(value.filter,facts.Filter) else None)
    return Common(id=wire.MaterialId(value.id), title=value.title, feed_id=fid, feed_title=value.feed_title,
        folder_id=wire.FolderId(value.folder_id), importance=wire.Importance(value.importance),
        published_at=project.timestamp(value.published) if value.published_known else None,
        source_url=url, source_position=wire.SourcePosition(value.source_order) if isinstance(value.source_order, int) else None,
        source=wire.SourceReference(source), snippet=value.snippet, originals=value.originals is not False,
        filter=filtered, theses=theses_state(value, runtime.processing_wait(value.id, 'theses')))


def attachments(values: list[facts.Attachment]) -> list[wire.Attachment]:
    return [wire.Attachment(kind=a.kind, mime=a.mime, url=media_url(a.url)) for a in values]


def card(runtime: Runtime, value: facts.Card, feed: wire.SubscriptionFeed, shared: Common | None = None) -> wire.ReadingCard:
    current = runtime.require_store()
    shared = shared if shared is not None else common(runtime, value, feed)
    if not value.complete:
        return wire.ReadingCard(wire.PendingReadingCard(**shared, availability='pending', loading=value.loading is not False))
    if not isinstance(value.version, str) or not isinstance(value.full, bool) or not isinstance(value.reason, str):
        raise ValueError('Saved material lacks revision or completeness')
    return wire.ReadingCard(wire.AvailableReadingCard(**shared, availability='available',
        external_attachments=[wire.ExternalAttachment(**a.model_dump()) for a in value.external_attachments],
        external_attachment_kinds=list(dict.fromkeys(a.kind for a in value.external_attachments)),
        loading=value.loading is True, completeness=completeness(value.full, value.reason, value.external_only),
        revision_id=wire.RevisionId(value.version), version=current.versions.number(value.id), changed=value.changed is True,
        source_deleted=value.source_deleted is True,
        images=[media_url(url) for url in value.images] if isinstance(value.images, list) else [],
        attachments=attachments(value.attachments) if isinstance(value.attachments, list) else [],
        previous_versions=[previous_reference(p) for p in value.previous] if isinstance(value.previous, list) else []))


def previous_reference(value: facts.Previous) -> wire.PreviousVersionReference:
    if not isinstance(value.number, int) or not isinstance(value.version, str):
        raise ValueError('Previous version lacks its identity')
    return wire.PreviousVersionReference(number=value.number, revision_id=wire.RevisionId(value.version), url=media_url(value.url))


def missing(resource: wire.AffectedResource) -> ApiError:
    return ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message='Объект недоступен', details=wire.ResourceNotFoundErrorDetails(resource=resource), command_id=None, diagnostic_id=None)))


def identity(value: str) -> wire.MaterialId:
    try:
        return wire.MaterialId(value)
    except ValueError:
        raise ApiError(422, invalid('path', 'item')) from None


def preview(value: facts.CompletionPreview) -> wire.CompletionPreview:
    return wire.CompletionPreview(cards_count=value.cards, publications_count=value.originals, token=value.token)


def register(app: FastAPI, runtime: Runtime) -> Callable[[str], wire.ReadableMaterial]:
    router = APIRouter(dependencies=[Depends(no_query)])

    def feeds(identity: str | None = None) -> dict[str, wire.SubscriptionFeed]:
        saved = runtime.require_store().subscriptions
        return {v['id']: project.subscription(facts.SubscriptionFeed.model_validate(v), saved.telegram.binding(v['id'])) for v in saved.feeds(identity)}

    def composition() -> wire.ReadingComposition:
        current = runtime.require_store()
        value = facts.Navigation.model_validate(current.reading.navigation())
        owners = feeds()
        return wire.ReadingComposition(count=value.count, folders=[wire.ReadingFolder(id=wire.FolderId(f.id), title=f.title, position=f.position if isinstance(f.position, int) else 0, count=f.count) for f in value.folders], feeds=[wire.NavigationFeed(feed=owners[f.id], count=f.count, has_unviewed=f.has_unviewed) for f in value.feeds])

    @router.get('/api/v3/reading-composition', response_model=wire.ReadingComposition)
    async def navigation() -> wire.ReadingComposition:
        with reading_snapshot():
            return composition()

    @router.get('/api/v3/folders', response_model=list[wire.ReadingFolder])
    async def folders() -> list[wire.ReadingFolder]:
        with reading_snapshot():
            return composition().folders

    @app.get('/api/v3/cards', response_model=list[wire.ReadingCard])
    async def cards(request: Request) -> list[wire.ReadingCard]:
        with reading_snapshot():
            allowed = {'folder', 'feed', 'sort', 'show_hidden'}
            for key in request.query_params:
                if key not in allowed or len(request.query_params.getlist(key)) != 1:
                    raise ApiError(422, invalid('query', key))
            sort = request.query_params.get('sort', 'newest')
            if sort not in ('newest', 'oldest', 'importance'):
                raise ApiError(422, invalid('query', 'sort'))
            folder, feed = request.query_params.get('folder'), request.query_params.get('feed')
            for key, value in (('folder', folder), ('feed', feed)):
                if value is not None:
                    try:
                        wire.FolderId(value) if key == 'folder' else wire.FeedId(value)
                    except ValueError:
                        raise ApiError(422, invalid('query', key)) from None
            current = runtime.require_store()
            values = [facts.Card.model_validate(v) for v in current.reading.cards(scalar_boolean(request, 'show_hidden'), folder, feed)]
            if sort == 'importance':
                values.sort(key=lambda c: (-{'normal':0, 'elevated':1, 'high':2}[c.importance], -c.published, c.id))
            else:
                values.sort(key=lambda c: (not c.published_known, c.published if c.published_known and sort == 'oldest' else -c.published if c.published_known else 0, c.feed_id, (-1 if sort == 'oldest' else 1) * (c.source_order if isinstance(c.source_order, int) else 0), c.id))
            owners = {}
            for owner_id in {value.feed_id for value in values}:
                owners.update(feeds(owner_id))
            return [card(runtime, value, owners[value.feed_id]) for value in values]

    @router.get('/api/v3/materials/{item}', response_model=wire.ReadableMaterial)
    async def material(item: str) -> wire.ReadableMaterial:
        return read_material(item)

    def read_material(item: str) -> wire.ReadableMaterial:
        with reading_snapshot():
            identity(item)
            current = runtime.require_store()
            raw = current.reading.material(item)
            if raw is None:
                raise missing(wire.AffectedResource(wire.MaterialResource(kind='material', material_id=identity(item))))
            value = facts.Material.model_validate(raw)
            owner = feeds(value.feed_id)[value.feed_id]
            from oko.reader.composition import snippet
            data = value.model_dump(exclude_unset=True)
            for key in ('text', 'description', 'display_text', 'reply_preview'):
                data.pop(key, None)
            data.update(feed_title=owner.root.title, folder_id=owner.root.folder_id.root, importance=owner.root.importance.root,
                        snippet=snippet(value.description if isinstance(value.description, str) and value.description else value.display_text if isinstance(value.display_text, str) else value.text), published_known=value.published_known is not False)
            entry = facts.Card.model_validate(data)
            shared = common(runtime, entry, owner)
            projected = card(runtime, entry, owner, shared).root
            if isinstance(projected, wire.PendingReadingCard):
                return wire.ReadableMaterial(wire.PendingReadableMaterial(**shared, availability='pending', content=wire.PendingPublicationContent(availability='pending', loading=projected.loading)))
            content = wire.AvailablePublicationContent(availability='available', text=value.text,
                display_text=value.display_text if isinstance(value.display_text, str) else value.text,
                description=value.description if isinstance(value.description, str) else '',
                reply_preview=wire.ReplyPreview(**value.reply_preview.model_dump()) if value.reply_preview else None,
                external_attachments=projected.external_attachments, images=projected.images, attachments=projected.attachments, completeness=projected.completeness,
                loading=projected.loading, changed=projected.changed, source_deleted=projected.source_deleted,
                revision_id=projected.revision_id, version=projected.version, previous_versions=projected.previous_versions)
            return wire.ReadableMaterial(wire.AvailableReadableMaterial(**shared, availability='available', content=content))

    @router.get('/api/v3/materials/{item}/versions/{number}', response_model=wire.PublicationVersion)
    async def version(item: str, number: str) -> wire.PublicationVersion:
        identity(item)
        n = positive_version(number)
        current = runtime.require_store()
        raw = current.versions.read(item, n)
        if raw is None:
            raise ApiError(404, wire.ErrorEnvelope(wire.VersionUnavailableError(code='version_unavailable', message='Версия недоступна', details=wire.VersionUnavailableErrorDetails(material_id=identity(item), number=n), command_id=None, diagnostic_id=None)))
        value = facts.Version.model_validate(raw)
        revision = current.versions.fingerprint(current.versions.path(item, n).parent)
        return wire.PublicationVersion(number=n, revision_id=wire.RevisionId(revision), title=value.title, text=value.text, reply_preview=wire.ReplyPreview(**value.reply_preview.model_dump()) if value.reply_preview else None,
            external_attachments=[wire.ExternalAttachment(**a.model_dump()) for a in value.external_attachments], completeness=completeness(value.full, value.reason, value.external_only), images=[media_url(url) for url in value.images], attachments=attachments(value.attachments))

    def read_media(item: str, kind: Literal['images','media'], name: str, number: int | None = None) -> Response:
        identity(item)
        current = runtime.require_store()
        raw = current.versions.read(item, number) if number is not None else current.material(item)
        material = facts.Version.model_validate(raw) if number is not None and raw else facts.Material.model_validate(raw) if raw else None
        images = material.images if material and isinstance(material.images, list) else []
        attached = material.attachments if material and isinstance(material.attachments, list) else []
        path = (current.versions.path(item, number) if number is not None else current.path(item)).parent / name
        image = kind == 'images' and any(url.endswith('/' + name) for url in images)
        attachment = next((a for a in attached if a.url.endswith('/' + name)), None) if kind == 'media' else None
        if Path(name).name != name or not (image or attachment) or not path.is_file():
            raise ApiError(404, wire.ErrorEnvelope(wire.MediaUnavailableError(code='media_unavailable', message='Вложение недоступно', details=wire.MediaUnavailableErrorDetails(material_id=identity(item), name=name), command_id=None, diagnostic_id=None)))
        mime = attachment.mime if attachment else {'.png':'image/png','.jpg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'}[path.suffix]
        return Response(path.read_bytes(), media_type=mime, headers={'Content-Disposition':'inline'})

    @router.get('/api/v3/materials/{item}/images/{name}')
    async def image(item: str, name: str) -> Response:
        return read_media(item, 'images', name)

    @router.get('/api/v3/materials/{item}/media/{name}')
    async def attachment(item: str, name: str) -> Response:
        return read_media(item, 'media', name)

    @router.get('/api/v3/materials/{item}/versions/{number}/{kind}/{name}')
    async def version_media(item: str, number: str, kind: Literal['images','media'], name: str) -> Response:
        return read_media(item, kind, name, positive_version(number))

    @router.post('/api/v3/feeds/{feed}/completion/preview', response_model=wire.CompletionPreview)
    async def preview_feed_completion(feed: str) -> wire.CompletionPreview:
        try:
            wire.FeedId(feed)
        except ValueError:
            raise ApiError(422, invalid('path', 'feed')) from None
        try:
            return preview(facts.CompletionPreview.model_validate(await runtime.preview_feed_completion(feed)))
        except ActionError as error:
            if error.kind == 'missing':
                raise missing(wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=wire.FeedId(feed)))) from None
            raise

    @router.post('/api/v3/feeds/{feed}/completion', response_model=wire.CommandAcknowledgement)
    async def complete_feed(request: Request, feed: str, body: wire.CompleteFeedRequest) -> wire.CommandAcknowledgement:
        context = wire.CommandContext(wire.CommandCompleteFeed(operation='complete_feed', feed_id=wire.FeedId(feed)))
        async with track(runtime, request, context):
            try:
                await runtime.complete_feed(feed, body.token)
            except ActionError as error:
                if error.kind == 'conflict':
                    value = facts.CompletionConflict.model_validate(error.detail)
                    raise ApiError(409, wire.ErrorEnvelope(wire.CompositionChangedError(code='composition_changed', message=value.message, details=wire.CompositionChangedErrorDetails(preview=preview(value.preview)), command_id=wire.CommandId(request.state.command_id), diagnostic_id=None))) from None
                raise
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @router.post('/api/v3/folders/{folder}/completion/preview', response_model=wire.CompletionPreview)
    async def preview_completion(folder: str) -> wire.CompletionPreview:
        try:
            wire.FolderId(folder)
        except ValueError:
            raise ApiError(422, invalid('path', 'folder')) from None
        try:
            return preview(facts.CompletionPreview.model_validate(await runtime.preview_completion(folder)))
        except ActionError as error:
            if error.kind == 'missing':
                raise missing(wire.AffectedResource(wire.FolderResource(kind='folder', folder_id=wire.FolderId(folder)))) from None
            raise

    @router.post('/api/v3/folders/{folder}/completion', response_model=wire.CommandAcknowledgement)
    async def complete_folder(request: Request, folder: str, body: wire.CompleteFolderRequest) -> wire.CommandAcknowledgement:
        context = wire.CommandContext(wire.CommandCompleteFolder(operation='complete_folder', folder_id=wire.FolderId(folder)))
        async with track(runtime, request, context):
            try:
                await runtime.complete_folder(folder, body.token)
            except ActionError as error:
                if error.kind == 'conflict':
                    value = facts.CompletionConflict.model_validate(error.detail)
                    raise ApiError(409, wire.ErrorEnvelope(wire.CompositionChangedError(code='composition_changed', message=value.message, details=wire.CompositionChangedErrorDetails(preview=preview(value.preview)), command_id=wire.CommandId(request.state.command_id), diagnostic_id=None))) from None
                raise
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @router.post('/api/v3/collect', response_model=wire.CommandAcknowledgement)
    async def collect(request: Request, body: wire.CollectionTarget = Body(default=wire.CollectionTarget(wire.AllCollectionTarget(scope='all')))) -> wire.CommandAcknowledgement:
        async with track(runtime, request, wire.CommandContext(wire.CommandCollect(operation='collect',target=body))):
            feed = body.root.feed_id.root if isinstance(body.root,wire.FeedCollectionTarget) else None
            folder = body.root.folder_id.root if isinstance(body.root,wire.FolderCollectionTarget) else None
            try:
                await runtime.collect(feed, folder=folder)
            except ActionError as error:
                if folder and error.kind=='missing':
                    raise missing(wire.AffectedResource(wire.FolderResource(kind='folder',folder_id=wire.FolderId(folder)))) from None
                if feed and error.kind=='missing':
                    raise missing(wire.AffectedResource(wire.FeedResource(kind='feed',feed_id=wire.FeedId(feed)))) from None
                if feed and error.code=='deletion_in_progress':
                    raise ApiError(409,wire.ErrorEnvelope(wire.DeletionInProgressError(code='deletion_in_progress',message='Фид удаляется',details=wire.DeletionInProgressErrorDetails(feed_id=wire.FeedId(feed)),command_id=None,diagnostic_id=None))) from None
                raise
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    # Each route supplies its domain intent explicitly; no path/body inference.
    async def execute(request: Request, context: wire.CommandContext) -> wire.CommandAcknowledgement:
        async with track(runtime, request, context):
            value = context.root
            if isinstance(value, wire.CommandViewed):
                await runtime.viewed(value.material_id.root)
            elif isinstance(value, wire.CommandRegenerate):
                await runtime.regenerate(value.material_id.root)
            elif isinstance(value, wire.CommandRetryMaterial):
                await runtime.retry_material(value.material_id.root)
            elif isinstance(value, wire.CommandRetryTheses):
                await runtime.retry_theses(value.material_id.root)
            elif isinstance(value, wire.CommandRetryFilter):
                await runtime.retry_filter(value.material_id.root)
            elif isinstance(value, wire.CommandRestoreFilter):
                await runtime.restore_filter(value.material_id.root)
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @router.post('/api/v3/materials/{item}/viewed', response_model=wire.CommandAcknowledgement)
    async def viewed(request: Request, item: str) -> wire.CommandAcknowledgement:
        return await execute(request, wire.CommandContext(wire.CommandViewed(operation='viewed', material_id=identity(item))))

    @router.post('/api/v3/materials/{item}/regenerate', response_model=wire.CommandAcknowledgement)
    async def regenerate(request: Request, item: str) -> wire.CommandAcknowledgement:
        return await execute(request, wire.CommandContext(wire.CommandRegenerate(operation='regenerate', material_id=identity(item))))

    @router.post('/api/v3/materials/{item}/retry', response_model=wire.CommandAcknowledgement)
    async def retry_material(request: Request, item: str) -> wire.CommandAcknowledgement:
        return await execute(request, wire.CommandContext(wire.CommandRetryMaterial(operation='retry_material', material_id=identity(item))))

    @router.post('/api/v3/materials/{item}/theses/retry', response_model=wire.CommandAcknowledgement)
    async def retry_theses(request: Request, item: str) -> wire.CommandAcknowledgement:
        return await execute(request, wire.CommandContext(wire.CommandRetryTheses(operation='retry_theses', material_id=identity(item))))

    @router.post('/api/v3/materials/{item}/filter/retry', response_model=wire.CommandAcknowledgement)
    async def retry_filter(request: Request, item: str) -> wire.CommandAcknowledgement:
        return await execute(request, wire.CommandContext(wire.CommandRetryFilter(operation='retry_filter', material_id=identity(item))))

    @router.post('/api/v3/materials/{item}/filter/restore', response_model=wire.CommandAcknowledgement)
    async def restore_filter(request: Request, item: str) -> wire.CommandAcknowledgement:
        return await execute(request, wire.CommandContext(wire.CommandRestoreFilter(operation='restore_filter', material_id=identity(item))))

    app.include_router(router)
    return read_material
