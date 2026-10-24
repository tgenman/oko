"""Project validated domain snapshots into the canonical v3 HTTP models."""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from oko.storage.store import WorkSummary

from oko.reader.generated import models as wire
from oko.reader import snapshots as facts


def timestamp(value: float) -> wire.Timestamp:
    return wire.Timestamp(datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z'))


def affected(value: facts.ProblemObject) -> wire.AffectedResource:
    if value.kind == 'material':
        return wire.AffectedResource(wire.MaterialResource(kind='material', material_id=wire.MaterialId(value.id)))
    if value.kind == 'feed':
        return wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=wire.FeedId(value.id)))
    if value.kind == 'folder':
        return wire.AffectedResource(wire.FolderResource(kind='folder', folder_id=wire.FolderId(value.id)))
    return wire.AffectedResource(wire.RuntimeResource(kind='runtime'))


class _CommandFacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context: wire.CommandContext
    error: wire.ErrorEnvelope | None = None


def command_recovery(context: wire.CommandContext) -> tuple[list[wire.AffectedResource], list[wire.RecoveryIntent]]:
    value = context.root
    resources: list[wire.AffectedResource] = []
    recovery: list[wire.RecoveryIntent] = []
    if isinstance(value, wire.CommandSettingsPatch):
        resources.append(wire.AffectedResource(wire.SettingsResource(kind='settings', target=value.target)))
        if value.keys:
            recovery.append(wire.RecoveryIntent(wire.RecoveryChangeSetting(kind='change_setting', target=value.target, keys=value.keys)))
    elif isinstance(value, (wire.CommandCodexSave, wire.CommandCodexCheck, wire.CommandProcessorSave, wire.CommandProcessorCheck)):
        processor = wire.ProcessorId('codex' if isinstance(value, (wire.CommandCodexSave, wire.CommandCodexCheck)) else 'llamacpp')
        resources.append(wire.AffectedResource(wire.ProcessorResource(kind='processor', processor_id=processor)))
        recovery.append(wire.RecoveryIntent(wire.RecoveryConfigureProcessor(kind='configure_processor', processor_id=processor)))
    elif isinstance(value, wire.CommandTelegramProfileSave):
        resources.append(wire.AffectedResource(wire.TelegramProfileResource(kind='telegram_profile', role=value.role)))
        recovery.append(wire.RecoveryIntent(wire.RecoveryConfigureTelegramProfile(kind='configure_telegram_profile', role=value.role)))
    elif isinstance(value, wire.CommandTelegramConnect):
        recovery.append(wire.RecoveryIntent(wire.RecoveryConnectSource(kind='connect_source', source_kind='telegram')))
    elif isinstance(value, (wire.CommandAddFeed, wire.CommandVerifyFeed)):
        recovery.append(wire.RecoveryIntent(wire.RecoveryConnectSource(kind='connect_source', source_kind='rss')))
    elif isinstance(value, (wire.CommandFeedPatch, wire.CommandRemoveFeed, wire.CommandHistoryStart, wire.CommandHistoryPause, wire.CommandHistoryCancel)):
        resources.append(wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=value.feed_id)))
    elif isinstance(value, (wire.CommandCompleteFolder, wire.CommandRenameFolder, wire.CommandRemoveFolder)):
        resources.append(wire.AffectedResource(wire.FolderResource(kind='folder', folder_id=value.folder_id)))
    elif isinstance(value, (wire.CommandRetryMaterial, wire.CommandRetryTheses, wire.CommandRetryFilter, wire.CommandRegenerate, wire.CommandViewed)):
        resources.append(wire.AffectedResource(wire.MaterialResource(kind='material', material_id=value.material_id)))
        recovery.append(wire.RecoveryIntent(wire.RecoveryInspectMaterials(kind='inspect_materials', material_ids=[value.material_id])))
    elif isinstance(value, wire.CommandCollect):
        recovery.append(wire.RecoveryIntent(wire.RecoveryRetryCollection(kind='retry_collection', target=value.target)))
    return resources, recovery


def producer_code(raw: str, kind: str) -> str:
    known = {'codex-choice':'processor_not_configured', 'processor-config':'processor_not_configured',
        'ai-permission':'public_ai_not_allowed', 'material-read-failed':'material_read_failed', 'material-save-failed':'material_save_failed', 'source-reconnect':'source_reconnect_required',
        'material-incomplete':'material_incomplete', 'youtube-admission':'youtube_rate_limited',
        'storage-unavailable':'storage_unavailable', 'identity-unconfirmed':'profile_not_configured',
        'profile_not_configured':'profile_not_configured', 'profile_unavailable':'profile_unavailable',
        'account_identity_mismatch':'account_identity_mismatch', 'source_identity_conflict':'source_identity_conflict',
        'source-identity-mismatch':'source_identity_conflict', 'duplicate_source':'duplicate_source'}
    if raw in known:
        return known[raw]
    return {'filter':'processing_failed','theses':'processing_failed','delete':'feed_delete_failed',
            'feed-delete':'feed_delete_failed','read':'telegram_read_failed'}.get(kind,'source_unavailable')


def problem(value: facts.Problem, bindings: dict[str, wire.TelegramAccess] | None = None,
            material_feeds: dict[str, str] | None = None, forgotten: set[str] | None = None) -> wire.CurrentProblem | wire.RetryingProblem | wire.FinishedProblem:
    command = wire.CommandId(value.request_id) if isinstance(value.request_id, str) and value.request_id else None
    context = None
    error = None
    if isinstance(value.operation, str) and value.operation.startswith('{'):
        try:
            context = wire.CommandContext.model_validate_json(value.operation)
        except ValueError:
            try:
                facts = _CommandFacts.model_validate_json(value.operation)
                context, error = facts.context, facts.error.root if facts.error else None
            except ValueError:
                pass  # Historical unknown contexts retain facts, never execute.
    live = isinstance(value.operation,str) and value.operation.startswith('producer:')
    resources = [affected(obj) for obj in value.objects]
    recovery: list[wire.RecoveryIntent] = []
    code = 'command_failed'
    if context is not None:
        resources, recovery = command_recovery(context)
        code = 'command_outcome_unknown' if value.code == 'action-unknown' else 'command_failed'
        if code!='command_outcome_unknown' and isinstance(error,(wire.SourceIdentityConflictError,wire.DuplicateSourceError,wire.AccountIdentityMismatchError,wire.ProfileNotConfiguredError,wire.ProfileUnavailableError,wire.ProfileInUseError,wire.ProfileAccountConflictError)):
            code = error.code
            if isinstance(error,(wire.SourceIdentityConflictError,wire.DuplicateSourceError)):
                recovery = []
                if isinstance(error,wire.DuplicateSourceError):
                    resources = [wire.AffectedResource(wire.FeedResource(kind='feed',feed_id=feed.feed_id)) for feed in error.details.existing_feeds]
                elif error.details.feed_id:
                    resources = [wire.AffectedResource(wire.FeedResource(kind='feed',feed_id=error.details.feed_id))]
            elif isinstance(error,(wire.AccountIdentityMismatchError,wire.ProfileNotConfiguredError,wire.ProfileInUseError)):
                recovery = [wire.RecoveryIntent(wire.RecoveryConfigureTelegramProfile(kind='configure_telegram_profile',role=error.details.role))]
                resources.append(wire.AffectedResource(wire.TelegramProfileResource(kind='telegram_profile',role=error.details.role)))
            elif isinstance(error,wire.ProfileUnavailableError) and isinstance(context.root,wire.CommandTelegramConnect):
                recovery = []
    elif live:
        kind = value.operation.split(':',1)[1] if isinstance(value.operation,str) else 'status'
        code = producer_code(value.code,kind)
        materials = [r.root.material_id for r in resources if isinstance(r.root,wire.MaterialResource)]
        feeds = [r.root.feed_id.root for r in resources if isinstance(r.root,wire.FeedResource)]
        feeds += [(material_feeds or {})[m.root] for m in materials if m.root in (material_feeds or {})]
        if code == 'processor_not_configured':
            processor = wire.ProcessorId('codex' if value.code=='codex-choice' else 'llamacpp')
            resources = [wire.AffectedResource(wire.ProcessorResource(kind='processor',processor_id=processor))]
            recovery.append(wire.RecoveryIntent(wire.RecoveryConfigureProcessor(kind='configure_processor',processor_id=processor)))
        elif code == 'public_ai_not_allowed':
            for feed in sorted(set(feeds)):
                recovery.append(wire.RecoveryIntent(wire.RecoveryChangeSetting(kind='change_setting',
                    target=wire.SettingsTarget(wire.FeedSettingsTarget(scope='feed',owner=wire.FeedId(feed))),keys=[wire.SettingKey('processor')])))
        elif code in ('source_reconnect_required','profile_not_configured','profile_unavailable','account_identity_mismatch'):
            roles = {binding.root.role.root for feed in feeds if (binding := (bindings or {}).get(feed)) and isinstance(binding.root,wire.TelegramClientAccess)}
            for role in sorted(roles):
                recovery.append(wire.RecoveryIntent(wire.RecoveryConfigureTelegramProfile(kind='configure_telegram_profile',role=wire.TelegramRole(role))))
        elif code not in ('source_identity_conflict','duplicate_source','feed_delete_failed','storage_unavailable'):
            if materials:
                intent = 'inspect_materials' if code=='youtube_rate_limited' else 'retry_theses' if kind=='theses' else 'retry_filter' if kind=='filter' else 'retry_material'
                recovery.append(wire.RecoveryIntent.model_validate(dict(kind=intent,material_ids=materials)))
            elif feeds and code=='source_unavailable':
                for feed in sorted(set(feeds)):
                    recovery.append(wire.RecoveryIntent(wire.RecoveryRetryCollection(kind='retry_collection',target=wire.CollectionTarget(wire.FeedCollectionTarget(scope='feed',feed_id=wire.FeedId(feed))))))
    if forgotten:
        resources = [resource for resource in resources if not isinstance(resource.root,wire.MaterialResource) or resource.root.material_id.root not in forgotten]
        retained: list[wire.RecoveryIntent] = []
        for recommendation in recovery:
            target = recommendation.root
            if isinstance(target,(wire.RecoveryInspectMaterials,wire.RecoveryRetryMaterial,wire.RecoveryRetryFilter,wire.RecoveryRetryTheses)):
                ids = [identity for identity in target.material_ids if identity.root not in forgotten]
                if ids:
                    retained.append(wire.RecoveryIntent(target.model_copy(update={'material_ids':ids})))
            else:
                retained.append(recommendation)
        recovery = retained
    if code=='command_outcome_unknown':
        recovery = [intent for intent in recovery if isinstance(intent.root,(wire.RecoveryConfigureProcessor,wire.RecoveryConfigureTelegramProfile,wire.RecoveryChangeSetting,wire.RecoveryConnectSource,wire.RecoveryInspectMaterials))]
    data = dict(id=value.id,code=code,command_id=command,title=value.title,
        message=error.message if error else value.reason if isinstance(value.reason,str) and value.reason else value.next_step,
        consequence=value.consequence,affected=resources,recovery=[] if value.state=='finished' else recovery,
        created_at=timestamp(value.created_at),state=value.state)
    if value.state=='finished':
        data.update(finished_at=timestamp(value.finished_at) if value.finished_at is not None else None,outcome=value.outcome)
    elif value.state=='retrying':
        data['retry_at']=timestamp(value.next_at) if value.next_at else None
    return wire.Problem.model_validate(data).root


def problems(value: facts.ProblemSnapshot, bindings: dict[str, wire.TelegramAccess] | None = None,
             material_feeds: dict[str, str] | None = None, forgotten: set[str] | None = None) -> wire.ProblemSnapshot:
    current: list[wire.CurrentProblem | wire.RetryingProblem] = []
    recent: list[wire.FinishedProblem] = []
    for entry in value.current:
        if not isinstance(entry.operation,str) or not entry.operation.startswith(('producer:','{')):
            continue
        projected = problem(entry,bindings,material_feeds,forgotten)
        if isinstance(projected,wire.FinishedProblem):
            raise ValueError('Current snapshot contains a finished problem')
        current.append(projected)
    for entry in value.recent:
        if not isinstance(entry.operation,str) or not entry.operation.startswith(('producer:','{')):
            continue
        projected = problem(entry,bindings,material_feeds,forgotten)
        if not isinstance(projected,wire.FinishedProblem):
            raise ValueError('Recent snapshot contains an unfinished problem')
        recent.append(projected)
    return wire.ProblemSnapshot(current=current,recent=recent,count=len(current),updated_at=timestamp(value.updated_at))


def work(jobs: list['WorkSummary']) -> list[wire.WorkEntry]:
    result: list[wire.WorkEntry] = []
    for job in jobs:
        progress = wire.WorkProgress(completed=0,total=wire.Total(job.count))
        feed = wire.FeedId(job.feed) if job.feed else None
        if job.status=='pending' and job.error_code:
            code=producer_code(job.error_code,job.kind)
            reason: Literal['processor_not_configured','public_ai_not_allowed','profile_unavailable','retry_scheduled'] = 'processor_not_configured' if code=='processor_not_configured' else 'public_ai_not_allowed' if code=='public_ai_not_allowed' else 'profile_unavailable' if code in ('profile_not_configured','profile_unavailable','account_identity_mismatch') else 'retry_scheduled'
            result.append(wire.WorkEntry(wire.WorkWaiting(phase='waiting',reason=wire.WaitingReason(reason),retry_at=timestamp(job.retry_at) if job.retry_at and reason != 'public_ai_not_allowed' else None)))
        elif job.kind in ('theses','filter'):
            result.append(wire.WorkEntry(wire.WorkProcessing(phase='processing',operation='filter' if job.kind=='filter' else 'theses',progress=progress)))
        elif job.kind in ('delete','feed-delete'):
            result.append(wire.WorkEntry(wire.WorkDeleting(phase='deleting',feed_id=feed,progress=progress)))
        elif job.kind=='read':
            result.append(wire.WorkEntry(wire.WorkWaiting(phase='waiting',reason=wire.WaitingReason('telegram_read_frontier'),retry_at=None)))
        else:
            result.append(wire.WorkEntry(wire.WorkCollecting(phase='collecting',feed_id=feed,progress=progress)))
    return result or [wire.WorkEntry(wire.WorkIdle(phase='idle'))]


def youtube(value: facts.YouTube | None) -> wire.YouTubeState:
    if value is None:
        return wire.YouTubeState(mode='direct', address='', has_password=False, has_username=False,
            admission='ready', retry_at=None, strikes=0, reason=None)
    return wire.YouTubeState(mode=value.mode, address=value.address, has_password=value.has_password, has_username=value.has_username,
        admission='paused' if value.paused else 'waiting' if value.next_at else 'ready',
        retry_at=timestamp(value.next_at) if value.next_at else None, strikes=value.strikes,
        reason=wire.WaitingReason('youtube_rate_limited') if value.reason else None)


def history(value: facts.InitialHistoryStatus) -> wire.FeedHistoryState:
    count=value.count_text or (str(value.count) if value.count is not None else None)
    if count is None:
        raise ValueError('Count selection lacks exact count')
    selection=wire.HistorySelection(wire.HistoryCountSelection(mode='count',count=wire.PositiveDecimal(count)))
    progress = wire.HistoryProgress(loaded=value.loaded, viewed=value.viewed, incomplete=value.incomplete,
        stop_reason=wire.HistoryStopReason.model_validate(value.stop_reason) if isinstance(value.stop_reason, str) else None,
        available=value.available, retrying=value.retrying, unavailable=value.unavailable,
        requests=value.requests if isinstance(value.requests, int) else 0,
        errors=[wire.HistoryItemError(material_id=wire.MaterialId(error.item), code='source_unavailable', message=error.message) for error in value.errors],
        scan_complete=value.scan_complete, manifest_complete=value.frozen,
        warnings=value.warnings if isinstance(value.warnings, list) else [],
        discovery_problem=wire.HistoryDiscoveryProblem(code=wire.WaitingReason('source_unavailable'), message=value.discovery_error,
            retry_at=timestamp(value.discovery_retry_at) if isinstance(value.discovery_retry_at, float) and value.discovery_retry_at else None)
            if isinstance(value.discovery_error, str) and value.discovery_error else None)
    state: wire.HistoryNotStarted | wire.HistoryCancelled | wire.HistoryComplete | wire.HistoryPaused | wire.HistoryWaiting | wire.HistoryFetching | wire.HistoryDiscovering
    run=value.run if isinstance(value.run,str) and value.run else 'initial'
    if value.state == 'pending' and not value.paused and not (isinstance(value.run, str) and value.run):
        state = wire.HistoryNotStarted(state='not_started', selection=selection, progress=progress)
    elif value.state == 'cancelled':
        state = wire.HistoryCancelled(state='cancelled', outcome='cancelled', run=run, selection=selection, progress=progress)
    elif value.state == 'done':
        state = wire.HistoryComplete(state='complete', run=run, selection=selection, progress=progress)
    elif value.paused:
        state = wire.HistoryPaused(state='paused', paused_phase='waiting' if value.waiting is True else 'fetching' if value.frozen else 'discovering' if (isinstance(value.run, str) and value.run) or value.available or (isinstance(value.requests, int) and value.requests) else 'not_started', run=run, selection=selection, progress=progress)
    elif value.state == 'waiting':
        state = wire.HistoryWaiting(state='waiting', reason=wire.WaitingReason('retry_scheduled'),
            retry_at=timestamp(value.discovery_retry_at) if isinstance(value.discovery_retry_at, float) and value.discovery_retry_at else None,
            run=run, selection=selection, progress=progress)
    elif value.frozen:
        state = wire.HistoryFetching(state='fetching', run=run, selection=selection, progress=progress)
    else:
        state = wire.HistoryDiscovering(state='discovering', run=run, selection=selection, progress=progress)
    return wire.FeedHistoryState(feed_id=wire.FeedId(value.feed_id), history=wire.HistoryState(state))


def telegram_access(value: facts.SubscriptionFeed, binding: dict[str, object] | None) -> wire.TelegramAccess:
    """Publication source identity without subscription/history presentation."""
    if binding and binding['kind'] in ('web', 'client'):
        return wire.TelegramAccess.model_validate(binding)
    if binding and binding['kind']=='unconfigured':
        return wire.TelegramAccess.model_validate(binding)
    return wire.TelegramAccess(wire.TelegramUnconfiguredAccess(kind='unconfigured',
        source_locator=value.identity if isinstance(value.identity,str) else value.id))


def subscription(value: facts.SubscriptionFeed, binding: dict[str, object] | None = None) -> wire.SubscriptionFeed:
    data = value.model_dump(exclude_unset=True)
    data['history'] = history(facts.InitialHistoryStatus.model_validate(dict(value.initial_history.model_dump(), feed_id=value.id))).history.model_dump(mode='json') if isinstance(value.initial_history, facts.History) else wire.HistoryState(wire.HistoryAbsent(state='absent')).model_dump(mode='json')
    data.pop('initial_history', None)
    data['connection'] = 'ready' if data.get('connection') == 'connected' else data.get('connection', 'unavailable')
    if value.kind == 'telegram':
        if binding and binding['kind'] in ('web', 'client'):
            data['access'] = telegram_access(value,binding).model_dump(mode='json')
            if binding['kind'] == 'client':
                data['auto_read'], data['read_history'] = bool(value.auto_read), bool(value.read_history)
        elif value.transport == 'telegram' and isinstance(value.telegram_account, str):
            data['connection'] = 'deleting' if data['connection'] == 'deleting' else 'needs_profile_confirmation'
            data['access'] = telegram_access(value,binding).model_dump(mode='json')
        else:
            data['connection'] = 'deleting' if data['connection'] == 'deleting' else 'needs_connection'
            data['access'] = telegram_access(value,binding).model_dump(mode='json')
        data.pop('transport', None)
    for key in ('telegram_account', 'telegram_peer', 'telegram_topic', 'telegram_username', 'read_history', 'auto_read'):
        if key not in ('auto_read', 'read_history') or not binding or binding['kind'] != 'client':
            data.pop(key, None)
    return wire.SubscriptionFeed.model_validate(data)


def codex(value: facts.CodexState) -> wire.CodexProcessorState:
    data = value.model_dump(exclude_unset=True)
    availability = {key: data.get(key) for key in ('local', 'version')}
    availability.update(auth=data.get('auth', 'unchecked'), limits_checked=value.limits_checked,
                        quota_windows=[{'used_percent': row.used_percent, 'resets_at': timestamp(row.resets_at).model_dump(mode='json') if isinstance(row.resets_at, (int, float)) else None} for row in value.quota_windows] if isinstance(value.quota_windows, list) else [])
    if value.available:
        availability['state'] = 'available'
    else:
        availability.update(state='unavailable', reason='not_authenticated' if data.get('auth') == 'missing' else 'unavailable', message=value.reason)
        if availability['auth'] not in ('unknown', 'missing', 'chatgpt', 'api_key', 'unchecked'):
            availability['auth'] = 'unknown'
    return wire.CodexProcessorState(configuration=wire.CodexConfiguration.model_validate(value.config.model_dump()) if value.config else None,
                                    availability=wire.CodexAvailability.model_validate(availability))


def diagnostic(value: facts.DiagnosticEntry) -> wire.DiagnosticEntry:
    data = value.model_dump()
    data.setdefault('material_ids', [])
    for old, new in (('first_time', 'first_at'), ('last_time', 'last_at')):
        parsed = datetime.fromisoformat(data.pop(old))
        data[new] = (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)).isoformat()
    return wire.DiagnosticEntry.model_validate(data)
