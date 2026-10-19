"""Settings boundary: one typed operation for all inheritance levels."""
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request

from oko.reader.generated import models as wire
from oko.reader import snapshots as facts
from oko.reader.runtime import Runtime
from oko.subscriptions.subscriptions import Overrides


def snapshot(scope: Literal['oko', 'folder', 'feed'], owner: str, value: facts.EffectiveSettings, binding: dict[str, object] | None = None, activity: wire.FeedActivity | None = None) -> wire.EffectiveSettingsSnapshot:
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
    saved.pop('feed_refresh_minutes', None)
    overrides = wire.StoredOverrides.model_validate(saved)
    if scope == 'oko':
        return wire.EffectiveSettingsSnapshot(wire.OkoSettingsSnapshot(scope='oko', owner='global', effective=values, overrides=overrides))
    if scope == 'folder':
        return wire.EffectiveSettingsSnapshot(wire.FolderSettingsSnapshot(scope='folder', owner=wire.FolderId(owner), effective=values, overrides=overrides))
    from oko.reader.projection_v3 import subscription
    if not isinstance(value.feed, facts.SubscriptionFeed):
        raise ValueError('Feed settings require a feed')
    return wire.EffectiveSettingsSnapshot(wire.FeedSettingsSnapshot(scope='feed', owner=wire.FeedId(owner), effective=values, overrides=overrides, feed=subscription(value.feed, binding), activity=activity))


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(dependencies=[Depends(no_query)])
    from oko.reader.api_v3 import ApiError

    def activity_for(scope: str, owner: str) -> wire.FeedActivity | None:
        if scope != 'feed':
            return None
        from oko.reader.projection_v3 import timestamp
        result = runtime.require_store().subscriptions.details.snapshot(owner)
        for field in ('created', 'modified', 'updated', 'marked_read'):
            if result[field] is not None:
                result[field] = timestamp(result[field])
        return wire.FeedActivity.model_validate(result)

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

    @router.get('/api/v3/settings/feed-refresh', response_model=wire.EffectiveFeedRefreshMinutes)
    async def feed_refresh_settings() -> wire.EffectiveFeedRefreshMinutes:
        value = runtime.require_store().subscriptions.effective('oko', 'global')['effective']['feed_refresh_minutes']
        return wire.EffectiveFeedRefreshMinutes.model_validate({
            'value': value['value'], 'origin': {'scope': value['scope'], **({'owner': 'global'} if value['scope'] == 'oko' else {})}})

    @router.get('/api/v3/settings/{scope}/{owner}', response_model=wire.EffectiveSettingsSnapshot)
    async def settings(scope: Literal['oko', 'folder', 'feed'], owner: str) -> wire.EffectiveSettingsSnapshot:
        target(scope, owner)
        return snapshot(scope, owner, facts.EffectiveSettings.model_validate(runtime.require_store().subscriptions.effective(scope, owner)), runtime.require_store().subscriptions.telegram.binding(owner) if scope == 'feed' else None, activity_for(scope, owner))

    @router.patch('/api/v3/settings/{scope}/{owner}', response_model=wire.EffectiveSettingsSnapshot)
    async def patch(request: Request, scope: Literal['oko', 'folder', 'feed'], owner: str, body: wire.SettingsPatch) -> wire.EffectiveSettingsSnapshot:
        from oko.reader.commands_v3 import track
        context = wire.CommandContext(wire.CommandSettingsPatch(operation='settings_patch', target=target(scope, owner), keys=[wire.SettingKey.model_validate(key) for key in sorted(body.model_fields_set - {'feed_refresh_minutes'})]))
        async with track(runtime, request, context):
            if scope != 'oko' and 'feed_refresh_minutes' in body.model_fields_set:
                raise ApiError(400, wire.ErrorEnvelope(wire.SettingScopeInvalidError(
                    code='setting_scope_invalid', message='Интервал обновления задаётся только в Настройках Око',
                    details=wire.SettingScopeInvalidErrorDetails(reason='feed_refresh_is_global_only'),
                    diagnostic_id=None, command_id=wire.CommandId(request.state.command_id))))
            result = await runtime.update_settings(scope, owner, Overrides.model_validate(body.model_dump(exclude_unset=True)))
            return snapshot(scope, owner, facts.EffectiveSettings.model_validate(result), runtime.require_store().subscriptions.telegram.binding(owner) if scope == 'feed' else None, activity_for(scope, owner))

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
        if 'title' in body.model_fields_set and (not isinstance(body.title, str) or not 1 <= len(body.title.strip()) <= 200):
            from oko.reader.api_v3 import invalid
            raise ApiError(422, invalid('body', 'title'))
        feed = facts.SubscriptionFeed.model_validate(runtime.require_store().subscriptions.feeds(identity)[0])
        context = wire.CommandContext(wire.CommandFeedPatch(operation='feed_patch', feed_id=wire.FeedId(identity), fields=[wire.FeedField.model_validate(key) for key in sorted(body.model_fields_set - {'feed_refresh_minutes'})]))
        async with track(runtime, request, context):
            if feed.kind == 'telegram' and 'transport' in body.model_fields_set:
                raise ApiError(409, wire.ErrorEnvelope(wire.AccessImmutableError(code='access_immutable', message='Способ доступа Telegram нельзя менять настройкой фида', details=wire.AccessImmutableErrorDetails(feed_id=wire.FeedId(identity)), diagnostic_id=None, command_id=wire.CommandId(request.state.command_id))))
            if feed.kind == 'telegram' and feed.transport != 'telegram' and body.model_fields_set.intersection(('auto_read', 'read_history')):
                raise ApiError(400, wire.ErrorEnvelope(wire.SourceConnectionInvalidError(code='source_connection_invalid', message='Веб-доступ не поддерживает отметки Telegram', details=wire.SourceConnectionInvalidErrorDetails(reason='telegram_web_read_unavailable'), diagnostic_id=None, command_id=wire.CommandId(request.state.command_id))))
            result = await runtime.patch_feed(identity, FeedPatch.model_validate(body.model_dump(exclude_unset=True)))
            return subscription(facts.SubscriptionFeed.model_validate(result), runtime.require_store().subscriptions.telegram.binding(identity))

    app.include_router(router)
