"""Worker-owned history selection over a source's FeedAdapter."""
import json
from typing import Any

from oko.collection.adapters import AdapterException,ExternalKey,ExternalReference,Platform,PublicationContent
from oko.collection.adapter_errors import source_error
from oko.collection.contracts import DiscoveryResult,SourceObservation
from oko.collection.rss_adapter import RSSAdapter
from oko.collection.telegram.adapter import TelegramWebAdapter
from oko.collection.youtube.adapter import YouTubeAdapter


async def discover_rss(adapter: RSSAdapter, url: str, selection: dict[str,Any], cursor: str | None) -> DiscoveryResult:
    state: dict[str,Any]=json.loads(cursor) if cursor else {}
    run=selection.get('run','initial')
    changes='RSS: new entries are discovered in the available feed window.'
    try:
        if selection.get('cancelled') and not state.get('initial_complete',False):
            state.update(rss_adapter='',run=run,initial_complete=True,locating=False,scan_complete=True,history_enroll=[])
            return DiscoveryResult((),json.dumps(state),SourceObservation(description=state.get('description')))
        if selection.get('paused') and not state.get('initial_complete',False):
            return DiscoveryResult((),cursor or json.dumps(state),SourceObservation(description=state.get('description')))
        if 'rss_adapter' not in state or state.get('run')!=run:
            metadata=await adapter.resolve_feed(url)
            state=dict(rss_adapter=metadata.latest_cursor,locating=True,description=metadata.description,
                       run=run,count=selection.get('count') or 1)
            return DiscoveryResult((),json.dumps(state),SourceObservation(description=metadata.description),continuation=0)
        description=state.get('description','')
        reference=ExternalReference(ExternalKey(Platform.rss,url),url)
        if state.get('locating'):
            state['rss_adapter']=await adapter.cursor_before(reference,state['rss_adapter'],state['count']-1)
            state['locating']=False
        page=await adapter.discover(reference,state['rss_adapter'],50)
        state['rss_adapter']=page.next_cursor
        first=not state.get('initial_complete',False)
        state['initial_complete']=not page.has_more or not first
        state['scan_complete']=not page.has_more
        description=adapter.descriptions.get(url,description)
        state['description']=description
        codes={issue.code for issue in page.issues}
        state['warnings']=[issue.message for issue in page.issues]
        if first and not page.has_more:
            state['stop_reason']=('request_failed' if 'rss-history-request-failed' in codes else
                'ambiguous_continuation' if 'rss-history-ambiguous' in codes else 'cycle' if 'rss-history-cycle' in codes else 'no_continuation' if 'history-shortfall' in codes else 'count_reached')
        state['history_enroll']=[value.metadata.reference.key.external_id for value in page.publications] if first else []
        warnings=list(dict.fromkeys(issue.message for issue in page.issues))
        return DiscoveryResult(page.publications,json.dumps(state),
            SourceObservation('; '.join(warnings) or None,changes,description=description),
            continuation=0 if page.has_more else None)
    except AdapterException as error:
        raise source_error(error) from error


async def discover_youtube(adapter: YouTubeAdapter, url: str, selection: dict[str,Any], cursor: str | None) -> DiscoveryResult:
    from oko.collection.youtube.adapter import feed_reference
    state: dict[str,Any]=json.loads(cursor) if cursor else {}
    run=selection.get('run','initial')
    changes='YouTube: new videos are discovered in the available Atom window.'
    try:
        if selection.get('cancelled') and not state.get('initial_complete',False):
            state.update(youtube_adapter='',run=run,initial_complete=True,locating=False,scan_complete=True,history_enroll=[])
            return DiscoveryResult((),json.dumps(state))
        if selection.get('paused') and not state.get('initial_complete',False):
            return DiscoveryResult((),cursor or json.dumps(state))
        if 'youtube_adapter' not in state or state.get('run')!=run:
            metadata=await adapter.resolve_feed(url)
            state=dict(youtube_adapter=metadata.latest_cursor,run=run,count=selection.get('count') or 1,locating=True)
            return DiscoveryResult((),json.dumps(state),continuation=0)
        feed=feed_reference(url)
        if state.get('locating'):
            state['youtube_adapter']=await adapter.cursor_before(feed,state['youtube_adapter'],state['count']-1)
            state['locating']=False
            pending=json.loads(state['youtube_adapter'])
            if pending.get('locating') and pending.get('history_error'):
                state.update(scan_complete=False,history_enroll=[],history_waiting=True,history_error=pending['history_error'])
                return DiscoveryResult((),json.dumps(state),continuation=60)
        page=await adapter.discover(feed,state['youtube_adapter'],50)
        state.update(youtube_adapter=page.next_cursor,scan_complete=not page.has_more,
                     history_enroll=[value.metadata.reference.key.external_id for value in page.publications]
                     if not state.get('initial_complete') else [])
        retry=any(issue.kind.value=='temporary' for issue in page.issues)
        state.update(history_waiting=retry,history_error='; '.join(issue.message for issue in page.issues) if retry else None,
                     warnings=[issue.message for issue in page.issues if issue.code=='history-shortfall'])
        if not page.has_more:state['initial_complete']=True
        gap='; '.join(dict.fromkeys(issue.message for issue in page.issues)) or None
        return DiscoveryResult(page.publications,json.dumps(state),SourceObservation(gap,changes),
                               continuation=(60 if retry else 0) if page.has_more else None)
    except AdapterException as error:
        raise source_error(error) from error


async def discover_telegram(adapter: TelegramWebAdapter, username: str, selection: dict[str,Any], cursor: str | None) -> DiscoveryResult:
    from oko.collection.telegram.adapter import reference,finish_window
    from oko.collection.telegram.contracts import HistoryEnvelope,HistorySelection
    from oko.collection.contracts import SourceError
    previous=HistoryEnvelope.decode(cursor,adapter='web') if cursor else None
    feed=reference('https://t.me/s/'+username)
    run=selection.get('run','initial')
    if previous and previous.position.get('run','initial')!=run:previous=None
    state: dict[str,Any]=dict(previous.position) if previous else {}
    state.pop('observed_after',None)
    warnings: tuple[str,...]=()
    observation=SourceObservation(changes='Telegram web: public history and new messages are discovered.')
    try:
        if selection.get('cancelled') and (previous is None or not previous.selection.scan_complete):
            if previous is None:
                metadata=await adapter.resolve_feed(feed.url)
                state=dict(adapter_cursor=finish_window(metadata.latest_cursor),run=run,locating=False)
                return DiscoveryResult((),HistoryEnvelope(adapter='web',selection=HistorySelection(since=0,scan_complete=True),position=state).encode(),observation)
            state['adapter_cursor']=finish_window(state.get('adapter_cursor',''))
            current=previous.selection.model_copy(update={'scan_complete':True,'paused':False,'enrolled_ids':()})
            return DiscoveryResult((),previous.model_copy(update={'selection':current,'position':state}).encode(),observation)
        if 'adapter_cursor' not in state:
            metadata=await adapter.resolve_feed(feed.url)
            state=dict(adapter_cursor=metadata.latest_cursor,locating=selection.get('mode')=='count',since=0,run=run)
            complete=not metadata.latest_cursor
            publications: tuple[PublicationContent,...]=()
        elif previous and selection.get('paused') and not previous.selection.scan_complete:
            current=previous.selection.model_copy(update={'paused':True,'enrolled_ids':()})
            return DiscoveryResult((),previous.model_copy(update={'selection':current}).encode(),observation)
        else:
            token=state['adapter_cursor']
            if state.get('locating'):
                count=selection.get('count')
                if not isinstance(count,int):raise SourceError('Начните новую загрузку по количеству; старая дата сохранена без подмены',retryable=False)
                token=await adapter.cursor_before(feed,token,count-1)
                state['locating']=False
            page=await adapter.discover(feed,token,50)
            state['adapter_cursor']=page.next_cursor
            publications=page.publications
            warnings=tuple(dict.fromkeys(issue.message for issue in page.issues))
            observation=SourceObservation('; '.join(warnings) or None,observation.changes)
            complete=not page.has_more
    except AdapterException as error:
        raise source_error(error) from error
    was_complete=previous.selection.scan_complete if previous else False
    enrolled=tuple(value.metadata.reference.key.external_id for value in publications) if not was_complete else ()
    oldest=min((min(value.content.get('extra',{}).get('telegram',{}).get('members',[]))
                for value in publications if value.content.get('extra',{}).get('telegram',{}).get('members')),default=None)
    current=HistorySelection(since=0,enrolled_ids=enrolled,scan_complete=complete,warnings=warnings,oldest=oldest)
    envelope=HistoryEnvelope(adapter='web',selection=current,position=state)
    return DiscoveryResult(publications,envelope.encode(),observation,continuation=0 if not complete else None)
