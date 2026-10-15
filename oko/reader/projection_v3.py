"""Typed projections while domain producers migrate from the reviewed v2 shape."""
from datetime import datetime, timezone

from oko.reader.generated import models as wire
from oko.reader.legacy_generated import models as previous


def timestamp(value: float) -> wire.Timestamp:
    return wire.Timestamp(datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z'))


def affected(value: previous.ProblemObject) -> wire.AffectedResource:
    if value.kind == 'material':
        return wire.AffectedResource(wire.MaterialResource(kind='material', material_id=wire.MaterialId(value.id)))
    if value.kind == 'feed':
        return wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=wire.FeedId(value.id)))
    return wire.AffectedResource(wire.RuntimeResource(kind='runtime'))


def problem(value: previous.Problem) -> wire.CurrentProblem | wire.RetryingProblem | wire.FinishedProblem | wire.LegacyCurrentProblem | wire.LegacyRetryingProblem | wire.LegacyFinishedProblem:
    # Preserve historical identity and facts. Old UI actions are deliberately
    # not interpreted as executable domain recovery commands.
    command = wire.CommandId(value.request_id) if isinstance(value.request_id, str) and value.request_id else None
    if isinstance(value.operation, str) and value.operation.startswith('{'):
        context = wire.CommandContext.model_validate_json(value.operation).root
        resources: list[wire.AffectedResource] = []
        recovery: list[wire.RecoveryIntent] = []
        if isinstance(context, wire.CommandSettingsPatch):
            resources.append(wire.AffectedResource(wire.SettingsResource(kind='settings', target=context.target)))
            if context.keys:
                recovery.append(wire.RecoveryIntent(wire.RecoveryChangeSetting(kind='change_setting', target=context.target, keys=context.keys)))
        elif isinstance(context, (wire.CommandCodexSave, wire.CommandCodexCheck, wire.CommandProcessorSave, wire.CommandProcessorCheck)):
            processor = wire.ProcessorId('codex' if isinstance(context, (wire.CommandCodexSave, wire.CommandCodexCheck)) else 'llamacpp')
            resources.append(wire.AffectedResource(wire.ProcessorResource(kind='processor', processor_id=processor)))
            recovery.append(wire.RecoveryIntent(wire.RecoveryConfigureProcessor(kind='configure_processor', processor_id=processor)))
        elif isinstance(context, wire.CommandFeedPatch):
            resources.append(wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=context.feed_id)))
        data = dict(id=value.id, code='command_outcome_unknown' if value.code=='action-unknown' else 'command_failed',
                    command_id=command, title=value.title, message=value.next_step, consequence=value.consequence,
                    affected=resources, recovery=recovery, created_at=timestamp(value.created_at), state=value.state)
        if value.state == 'finished':
            return wire.FinishedProblem.model_validate(dict(data, finished_at=timestamp(value.finished_at) if value.finished_at else None, outcome=value.outcome))
        if value.state == 'retrying':
            return wire.RetryingProblem.model_validate(dict(data, retry_at=timestamp(value.next_at) if value.next_at else None))
        return wire.CurrentProblem.model_validate(data)
    if value.state == 'finished':
        if value.finished_at is None or value.outcome is None:
            raise ValueError('Finished problem lacks its outcome')
        return wire.LegacyFinishedProblem(code='legacy_problem', raw_code=value.code, id=wire.ProblemId(value.id),
            title=value.title, message=value.next_step, consequence=value.consequence,
            command_id=command, affected=[affected(obj) for obj in value.objects], recovery=[],
            created_at=timestamp(value.created_at), state='finished', finished_at=timestamp(value.finished_at), outcome=value.outcome)
    if value.state == 'retrying':
        return wire.LegacyRetryingProblem(code='legacy_problem', raw_code=value.code, id=wire.ProblemId(value.id),
            title=value.title, message=value.next_step, consequence=value.consequence,
            command_id=command, affected=[affected(obj) for obj in value.objects], recovery=[],
            created_at=timestamp(value.created_at), state='retrying', retry_at=timestamp(value.next_at) if value.next_at else None)
    return wire.LegacyCurrentProblem(code='legacy_problem', raw_code=value.code, id=wire.ProblemId(value.id),
        title=value.title, message=value.next_step, consequence=value.consequence,
        command_id=command, affected=[affected(obj) for obj in value.objects], recovery=[],
        created_at=timestamp(value.created_at), state='current')


def problems(value: previous.ProblemSnapshot) -> wire.ProblemSnapshot:
    current: list[wire.CurrentProblem | wire.RetryingProblem | wire.LegacyCurrentProblem | wire.LegacyRetryingProblem] = []
    recent: list[wire.FinishedProblem | wire.LegacyFinishedProblem] = []
    for entry in value.current:
        projected = problem(entry)
        if isinstance(projected, (wire.FinishedProblem, wire.LegacyFinishedProblem)):
            raise ValueError('Current snapshot contains a finished problem')
        current.append(projected)
    for entry in value.recent:
        projected = problem(entry)
        if not isinstance(projected, (wire.FinishedProblem, wire.LegacyFinishedProblem)):
            raise ValueError('Recent snapshot contains an unfinished problem')
        recent.append(projected)
    return wire.ProblemSnapshot(current=current, recent=recent, count=value.count, updated_at=timestamp(value.updated_at))


def work(jobs: list[previous.Job]) -> list[wire.WorkEntry]:
    result: list[wire.WorkEntry] = []
    for job in jobs:
        if job.status not in ('pending', 'running'):
            continue
        progress = wire.WorkProgress(completed=0, total=wire.Total(job.count))
        if job.kind in ('theses', 'filter'):
            result.append(wire.WorkEntry(wire.WorkProcessing(phase='processing', operation=job.kind, progress=progress)))
        elif job.kind == 'delete':
            result.append(wire.WorkEntry(wire.WorkDeleting(phase='deleting', feed_id=None, progress=progress)))
        elif job.kind == 'read':
            result.append(wire.WorkEntry(wire.WorkWaiting(phase='waiting', reason=wire.WaitingReason('profile_unavailable'), retry_at=None)))
        else:
            result.append(wire.WorkEntry(wire.WorkCollecting(phase='collecting', feed_id=None, progress=progress)))
    return result or [wire.WorkEntry(wire.WorkIdle(phase='idle'))]


def youtube(value: previous.YouTube | None) -> wire.YouTubeState:
    if value is None:
        return wire.YouTubeState(mode='direct', address='', has_password=False, has_username=False,
            admission='ready', retry_at=None, strikes=0, reason=None)
    return wire.YouTubeState(mode=value.mode, address=value.address, has_password=value.has_password, has_username=value.has_username,
        admission='paused' if value.paused else 'waiting' if value.next_at else 'ready',
        retry_at=timestamp(value.next_at) if value.next_at else None, strikes=value.strikes,
        reason=wire.WaitingReason('youtube_rate_limited') if value.reason else None)


def history(value: previous.InitialHistoryStatus) -> wire.FeedHistoryState:
    if value.mode == 'count':
        count = value.count_text or (str(value.count) if value.count is not None else None)
        if count is None:
            raise ValueError('Count selection lacks exact count')
        selection = wire.HistorySelection(wire.HistoryCountSelection(mode='count', count=wire.PositiveDecimal(count)))
    else:
        if value.date is None:
            raise ValueError('Date selection lacks date')
        selection = wire.HistorySelection(wire.HistoryDateSelection(mode='date', date=wire.LocalDate(value.date), timezone=wire.IanaTimezone(value.timezone)))
    progress = wire.HistoryProgress(loaded=value.loaded, viewed=value.viewed, incomplete=value.incomplete,
        available=value.available, retrying=value.retrying, unavailable=value.unavailable,
        requests=value.requests if isinstance(value.requests, int) else 0,
        errors=[wire.HistoryItemError(material_id=wire.MaterialId(error.item), code='source_unavailable', message=error.message) for error in value.errors],
        scan_complete=value.scan_complete, manifest_complete=value.frozen,
        warnings=value.warnings if isinstance(value.warnings, list) else [],
        discovery_problem=wire.HistoryDiscoveryProblem(code=wire.WaitingReason('source_unavailable'), message=value.discovery_error,
            retry_at=timestamp(value.discovery_retry_at) if isinstance(value.discovery_retry_at, float) and value.discovery_retry_at else None)
            if isinstance(value.discovery_error, str) and value.discovery_error else None)
    state: wire.HistoryNotStarted | wire.HistoryCancelled | wire.HistoryComplete | wire.HistoryPaused | wire.HistoryWaiting | wire.HistoryFetching | wire.HistoryDiscovering
    if not isinstance(value.run, str) or not value.run:
        state = wire.HistoryNotStarted(state='not_started', selection=selection, progress=progress)
    elif value.state == 'cancelled':
        state = wire.HistoryCancelled(state='cancelled', outcome='cancelled', run=value.run, selection=selection, progress=progress)
    elif value.state == 'done':
        state = wire.HistoryComplete(state='complete', run=value.run, selection=selection, progress=progress)
    elif value.paused:
        state = wire.HistoryPaused(state='paused', paused_phase='fetching' if value.frozen else 'discovering', run=value.run, selection=selection, progress=progress)
    elif value.state == 'waiting':
        state = wire.HistoryWaiting(state='waiting', reason=wire.WaitingReason('retry_scheduled'),
            retry_at=timestamp(value.discovery_retry_at) if isinstance(value.discovery_retry_at, float) and value.discovery_retry_at else None,
            run=value.run, selection=selection, progress=progress)
    elif value.frozen:
        state = wire.HistoryFetching(state='fetching', run=value.run, selection=selection, progress=progress)
    else:
        state = wire.HistoryDiscovering(state='discovering', run=value.run, selection=selection, progress=progress)
    return wire.FeedHistoryState(feed_id=wire.FeedId(value.feed_id), history=wire.HistoryState(state))


def subscription(value: previous.SubscriptionFeed) -> wire.SubscriptionFeed:
    data = value.model_dump(exclude_unset=True)
    data['history'] = history(previous.InitialHistoryStatus.model_validate(dict(value.initial_history.model_dump(), feed_id=value.id))).history.model_dump(mode='json') if isinstance(value.initial_history, previous.History) else wire.HistoryState(wire.HistoryAbsent(state='absent', legacy_since=timestamp(value.legacy_since) if isinstance(value.legacy_since, float) else None)).model_dump(mode='json')
    data.pop('initial_history', None)
    data.pop('legacy_since', None)
    data['connection'] = 'ready' if data.get('connection') == 'connected' else data.get('connection', 'unavailable')
    if value.kind == 'telegram':
        if value.transport is None or value.transport == 'telegram' and not isinstance(value.telegram_account, str):
            data['connection'] = 'deleting' if data['connection'] == 'deleting' else 'needs_connection'
            data['access'] = wire.TelegramUnconfiguredAccess(kind='unconfigured', source_locator=value.identity if isinstance(value.identity, str) else value.id).model_dump(mode='json')
        elif value.transport == 'telegram':
            data['connection'] = 'deleting' if data['connection'] == 'deleting' else 'needs_profile_confirmation'
            data['access'] = wire.TelegramLegacyClientAccess(kind='legacy_client', alias=value.telegram_account if isinstance(value.telegram_account, str) else '', peer_id=wire.TelegramPeerId(str(value.telegram_peer)) if isinstance(value.telegram_peer, int) else None, source_locator=value.identity if isinstance(value.identity, str) else value.id).model_dump(mode='json')
        else:
            username = value.telegram_username if isinstance(value.telegram_username, str) else str(data['identity']).removeprefix('telegram:').removeprefix('@')
            data['access'] = wire.TelegramWebAccess(kind='web', username=username, preview_url=wire.SourceUrl('https://t.me/s/' + username)).model_dump(mode='json')
        data.pop('transport', None)
    for key in ('telegram_account', 'telegram_peer', 'telegram_topic', 'telegram_username', 'read_history', 'auto_read'):
        data.pop(key, None)
    return wire.SubscriptionFeed.model_validate(data)


def codex(value: previous.CodexState) -> wire.CodexProcessorState:
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


def diagnostic(value: previous.DiagnosticEntry) -> wire.DiagnosticEntry:
    data = value.model_dump()
    data.setdefault('material_ids', [])
    for old, new in (('first_time', 'first_at'), ('last_time', 'last_at')):
        parsed = datetime.fromisoformat(data.pop(old))
        data[new] = (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)).isoformat()
    return wire.DiagnosticEntry.model_validate(data)
