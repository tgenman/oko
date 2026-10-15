"""Settings boundary: one typed operation for all inheritance levels."""
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request

from oko.reader.generated import models as wire
from oko.reader.legacy_generated import models as previous
from oko.reader.runtime import Runtime
from oko.subscriptions.subscriptions import Overrides


def snapshot(scope: Literal['oko', 'folder', 'feed'], owner: str, value: previous.EffectiveSettings) -> wire.EffectiveSettingsSnapshot:
    def origin(level: str, identity: str | None) -> wire.SettingOrigin:
        return wire.SettingOrigin.model_validate({'scope': level, **({'owner': identity} if level != 'default' else {})})

    def processor(value: str) -> wire.StoredProcessorSelection:
        try:
            return wire.StoredProcessorSelection.model_validate(value)
        except ValueError:
            return wire.StoredProcessorSelection(wire.LegacyProcessorSelection(kind='legacy', value=value))

    effective = value.effective
    values = wire.EffectiveSettingsValues(
        originals=wire.EffectiveOriginals(value=effective.originals.value, origin=origin(effective.originals.scope, effective.originals.owner)),
        theses=wire.EffectiveTheses(value=effective.theses.value, origin=origin(effective.theses.scope, effective.theses.owner)),
        prompt_theses=wire.EffectivePromptTheses(value=effective.prompt_theses.value, origin=origin(effective.prompt_theses.scope, effective.prompt_theses.owner)),
        prompt_filters=wire.EffectivePromptFilters(value=effective.prompt_filters.value, origin=origin(effective.prompt_filters.scope, effective.prompt_filters.owner)),
        processor=wire.EffectiveProcessor(value=processor(effective.processor.value), origin=origin(effective.processor.scope, effective.processor.owner)),
    )
    saved = value.overrides.model_dump(exclude_unset=True)
    if isinstance(saved.get('processor'), str):
        saved['processor'] = processor(saved['processor']).model_dump(mode='json')
    overrides = wire.StoredOverrides.model_validate(saved)
    if scope == 'oko':
        return wire.EffectiveSettingsSnapshot(wire.OkoSettingsSnapshot(scope='oko', owner='global', effective=values, overrides=overrides))
    if scope == 'folder':
        return wire.EffectiveSettingsSnapshot(wire.FolderSettingsSnapshot(scope='folder', owner=wire.FolderId(owner), effective=values, overrides=overrides))
    from oko.reader.projection_v3 import subscription
    if not isinstance(value.feed, previous.SubscriptionFeed):
        raise ValueError('Feed settings require a feed')
    return wire.EffectiveSettingsSnapshot(wire.FeedSettingsSnapshot(scope='feed', owner=wire.FeedId(owner), effective=values, overrides=overrides, feed=subscription(value.feed)))


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(dependencies=[Depends(no_query)])
    from oko.reader.api_v3 import ApiError

    def target(scope: Literal['oko', 'folder', 'feed'], owner: str) -> wire.SettingsTarget:
        if scope == 'oko' and owner != 'global':
            raise ApiError(400, wire.ErrorEnvelope(wire.SettingScopeInvalidError(
                code='setting_scope_invalid', message='Для Око используется global', diagnostic_id=None, command_id=None,
                details=wire.SettingScopeInvalidErrorDetails(reason='oko_owner_must_be_global'))))
        try:
            result = wire.SettingsTarget.model_validate({'scope': scope, 'owner': owner})
        except ValueError:
            from oko.reader.api_v3 import invalid
            raise ApiError(422, invalid('path', 'owner')) from None
        try:
            runtime.require_store().subscriptions.check_owner(scope, owner)
        except LookupError:
            raise ApiError(404, wire.ErrorEnvelope(wire.ResourceNotFoundError(code='resource_not_found', message='Настройки не найдены', diagnostic_id=None, command_id=None,
                details=wire.ResourceNotFoundErrorDetails(resource=wire.AffectedResource(wire.SettingsResource(kind='settings', target=result)))))) from None
        return result

    @router.get('/api/v3/settings/{scope}/{owner}', response_model=wire.EffectiveSettingsSnapshot)
    async def settings(scope: Literal['oko', 'folder', 'feed'], owner: str) -> wire.EffectiveSettingsSnapshot:
        target(scope, owner)
        return snapshot(scope, owner, previous.EffectiveSettings.model_validate(runtime.require_store().subscriptions.effective(scope, owner)))

    @router.patch('/api/v3/settings/{scope}/{owner}', response_model=wire.EffectiveSettingsSnapshot)
    async def patch(request: Request, scope: Literal['oko', 'folder', 'feed'], owner: str, body: wire.SettingsPatch) -> wire.EffectiveSettingsSnapshot:
        from oko.reader.commands_v3 import track
        context = wire.CommandContext(wire.CommandSettingsPatch(operation='settings_patch', target=target(scope, owner), keys=[wire.SettingKey.model_validate(key) for key in sorted(body.model_fields_set)]))
        async with track(runtime, request, context):
            result = await runtime.update_settings(scope, owner, Overrides.model_validate(body.model_dump(exclude_unset=True)))
            return snapshot(scope, owner, previous.EffectiveSettings.model_validate(result))

    async def immutable_access(request: Request, identity: str) -> None:
        # Check immutable bindings before request DTO rejection; no write occurs.
        try:
            payload = await request.json()
        except ValueError:
            return
        if not isinstance(payload, dict) or not set(payload).intersection(('access', 'account', 'peer', 'profile', 'telegram_account', 'telegram_peer', 'role')):
            return
        feeds = runtime.require_store().subscriptions.feeds(identity)
        if feeds and feeds[0]['kind'] == 'telegram':
            raise ApiError(409, wire.ErrorEnvelope(wire.AccessImmutableError(code='access_immutable', message='Привязку Telegram нельзя менять настройкой фида', details=wire.AccessImmutableErrorDetails(feed_id=wire.FeedId(identity)), diagnostic_id=None, command_id=wire.CommandId(request.state.command_id))))

    @router.patch('/api/v3/feeds/{identity}', response_model=wire.SubscriptionFeed, dependencies=[Depends(immutable_access)])
    async def patch_feed(request: Request, identity: str, body: wire.FeedPatchRequest) -> wire.SubscriptionFeed:
        from oko.reader.commands_v3 import track
        from oko.reader.projection_v3 import subscription
        from oko.subscriptions.subscriptions import FeedPatch
        target('feed', identity)
        feed = previous.SubscriptionFeed.model_validate(runtime.require_store().subscriptions.feeds(identity)[0])
        context = wire.CommandContext(wire.CommandFeedPatch(operation='feed_patch', feed_id=wire.FeedId(identity), fields=[wire.FeedField.model_validate(key) for key in sorted(body.model_fields_set)]))
        async with track(runtime, request, context):
            if feed.kind == 'telegram' and 'transport' in body.model_fields_set:
                raise ApiError(409, wire.ErrorEnvelope(wire.AccessImmutableError(code='access_immutable', message='Способ доступа Telegram нельзя менять настройкой фида', details=wire.AccessImmutableErrorDetails(feed_id=wire.FeedId(identity)), diagnostic_id=None, command_id=wire.CommandId(request.state.command_id))))
            if feed.kind == 'telegram' and feed.transport != 'telegram' and body.model_fields_set.intersection(('auto_read', 'read_history')):
                raise ApiError(400, wire.ErrorEnvelope(wire.ActionInvalidError(code='action_invalid', message='Веб-доступ не поддерживает отметки Telegram', details=wire.ActionInvalidErrorDetails(reason='telegram_web_read_unavailable'), diagnostic_id=None, command_id=wire.CommandId(request.state.command_id))))
            result = await runtime.patch_feed(identity, FeedPatch.model_validate(body.model_dump(exclude_unset=True)))
            return subscription(previous.SubscriptionFeed.model_validate(result))

    app.include_router(router)
