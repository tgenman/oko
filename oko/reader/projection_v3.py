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


def problem(value: previous.Problem) -> wire.LegacyCurrentProblem | wire.LegacyRetryingProblem | wire.LegacyFinishedProblem:
    # Preserve historical identity and facts. Old UI actions are deliberately
    # not interpreted as executable domain recovery commands.
    command = wire.CommandId(value.request_id) if isinstance(value.request_id, str) and value.request_id else None
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
        if isinstance(projected, wire.LegacyFinishedProblem):
            raise ValueError('Current snapshot contains a finished problem')
        current.append(projected)
    for entry in value.recent:
        projected = problem(entry)
        if not isinstance(projected, wire.LegacyFinishedProblem):
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
