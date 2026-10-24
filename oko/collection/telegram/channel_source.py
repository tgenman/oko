"""Telegram channel delivery and read metadata over the external adapter API.

This module owns neither storage nor media downloads. Runtime accepts its
transport packets; Publication and MediaFile in SQLite own the saved state.
"""
import asyncio
from dataclasses import replace
from copy import deepcopy
import json
from typing import Any

from oko.collection.adapters import AdapterException,AdapterErrorKind,PublicationContent
from oko.collection.adapter_errors import source_error
from oko.collection.contracts import SourceError, SourceManifest, Material, Publication, FetchResult, DiscoveryResult, ReplyPreview
from oko.collection.telegram.adapter import TelegramWebAdapter,reference,publication_reference,finish_window
from oko.collection.telegram.contracts import HistoryEnvelope,HistorySelection
from oko.collection.telegram.source import TelegramSource


def material(value: PublicationContent, previous: Material | None = None) -> Material:
    extra = value.content['extra']['telegram']
    manifest = SourceManifest.model_validate_json(json.dumps(extra['manifest']))
    observed_preview=manifest.preview_fragment
    if manifest.preview_error and previous and previous.source_manifest and previous.source_manifest.preview_fragment:
        manifest = manifest.model_copy(update={'preview_fragment': previous.source_manifest.preview_fragment})
    reply = manifest.post.get('reply_preview')
    title, text = value.metadata.title, str(manifest.post['text'])
    reason = 'Вложения ещё не загружены' if value.media else '; '.join(issue.message for issue in value.issues if issue.kind!=AdapterErrorKind.notice)
    if manifest.post_web_unavailable and text.strip():
        preview = manifest.preview_fragment or ''
        headline = next((line.strip(' \t\r\u2063\u200b\ufeff') for line in preview.splitlines() if any(char.isalnum() for char in line)), '')
        title = (headline if len(headline) <= 140 else headline[:140].rstrip()+'…') or 'Фрагмент предпросмотра Telegram недоступен'
        text = '\n\n'.join(part for part in (text, preview) if part)
        reason = ('Сохранён фрагмент предпросмотра; полная публикация недоступна для веб-просмотра'
                  if preview else 'Фрагмент предпросмотра недоступен; откройте пост в Telegram')
    if manifest.preview_fragment!=observed_preview:
        content=deepcopy(value.content)
        content['extra']['telegram']['manifest']=manifest.model_dump(mode='json')
        bodies=[block for block in content['blocks'] if block['type']=='text' and block.get('role')=='body']
        if len(bodies)==1:
            bodies[0]['text']=text
        elif not bodies and text:
            content['blocks'].insert(0,dict(type='text',role='body',text=text))
        value=replace(value,content=content)
    return Material(title, text, False, reason,
                    members=tuple(extra['members']), description=extra['description'],
                    source_manifest=manifest,
                    reply_preview=ReplyPreview.model_validate(reply) if reply else None, adapter_content=value)


def publication(value: PublicationContent) -> Publication:
    result = material(value) if value.content else None
    identity = value.metadata.reference.url.rstrip('/').split('/')[-1]
    return Publication(identity, value.metadata.reference.url,
        value.metadata.published_at.timestamp() if value.metadata.published_at else 0,
        result, result.members if result else (int(identity),), published_known=value.metadata.published_at is not None,
        adapter_reference=value.metadata.reference)


class TelegramWebSource(TelegramSource):
    """Collect descriptions through the common API; Store schedules every file."""
    independent_media = True

    def publication_adapter(self) -> TelegramWebAdapter:
        if not hasattr(self, '_publication_adapter'):
            self._publication_adapter = TelegramWebAdapter(self.http_transport,linked_descriptions=True)
        return self._publication_adapter

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        previous=HistoryEnvelope.decode(cursor,adapter='web') if cursor else None
        assert self.username
        api = self.publication_adapter()
        feed = reference('https://t.me/s/'+self.username)
        run=(self.initial_history or {}).get('run','initial')
        if previous and previous.position.get('run','initial')!=run:
            previous=None
        state: dict[str, Any] = dict(previous.position) if previous else {}
        observations: tuple[Publication, ...] = ()
        warnings: tuple[str, ...] = ()
        try:
            if (self.initial_history or {}).get('cancelled') and (previous is None or not previous.selection.scan_complete):
                if previous is None:
                    metadata=await api.resolve_feed(feed.url)
                    state=dict(adapter_cursor=finish_window(metadata.latest_cursor),run=run,locating=False)
                    return DiscoveryResult((),HistoryEnvelope(adapter='web',selection=HistorySelection(since=0,scan_complete=True),position=state).encode(),self.observation())
                state['adapter_cursor']=finish_window(state.get('adapter_cursor',''))
                selection=previous.selection.model_copy(update={'scan_complete':True,'paused':False,'enrolled_ids':()})
                return DiscoveryResult((),previous.model_copy(update={'selection':selection,'position':state}).encode(),self.observation())
            if previous and previous.selection.scan_complete:
                # Recheck restrictions in the observed public window without
                # enrolling older publications.
                from oko.collection.telegram.web import WebTelegram
                assert isinstance(self.client, WebTelegram)
                upper = state.get('observed_after', 0)
                await self.client.page('', 0, upper, 0)
                observations = tuple(replace(p, observed_only=True, adapter_reference=publication_reference(p.url)) for p in self.client.observed)
                self.identity_info = self.client.identity_info
            if 'adapter_cursor' not in state:
                metadata = await api.resolve_feed(feed.url)
                # Persist the boundary before scanning back N publications. A
                # failure on an older page cannot shift the window on retry.
                state = dict(adapter_cursor=metadata.latest_cursor, locating=(self.initial_history or {}).get('mode')=='count', since=0,run=run)
                complete = not metadata.latest_cursor
                result: tuple[Publication, ...] = ()
            elif previous and (self.initial_history or {}).get('paused') and not previous.selection.scan_complete:
                selection = previous.selection.model_copy(update={'paused': True, 'enrolled_ids': ()})
                return DiscoveryResult((), previous.model_copy(update={'selection': selection}).encode(), self.observation())
            else:
                token = state['adapter_cursor']
                if state.get('locating'):
                    count=(self.initial_history or {}).get('count')
                    if not isinstance(count,int):
                        raise SourceError('Начните новую загрузку по количеству; старая дата сохранена без подмены',retryable=False)
                    token = await api.cursor_before(feed, token, count-1)
                    state['locating'] = False
                page = await api.discover(feed, token, 50)
                state['adapter_cursor'] = page.next_cursor
                result = tuple(publication(value) for value in page.publications)
                state['observed_after'] = max([state.get('observed_after', 0)] + [max(p.members) for p in result])
                warnings = tuple(dict.fromkeys(issue.message for issue in page.issues))
                self.gap = '; '.join(warnings) or None
                complete = not page.has_more
        except AdapterException as error:
            raise source_error(error)
        was_complete = previous.selection.scan_complete if previous else False
        enrolled = tuple(p.id for p in result) if not was_complete else ()
        selection = HistorySelection(since=0, enrolled_ids=enrolled,
            scan_complete=complete, warnings=warnings, oldest=min((min(p.members) for p in result), default=None))
        envelope = HistoryEnvelope(adapter='web', selection=selection, position=state)
        return DiscoveryResult(result + observations, envelope.encode(), self.observation(),
                               continuation=0 if not complete else None)

    async def fetch(self, publication: Publication) -> FetchResult:
        api = self.publication_adapter()
        try:
            value = await api.fetch(reference(publication.url))
        except AdapterException as error:
            raise source_error(error)
        if value.needs_fetch:
            raise SourceError('Не удалось разобрать публикацию Telegram', code='malformed_publication', retryable=False)
        teaser = material(value, publication.teaser)
        # A failed file cannot stop retries of unfinished article metadata.
        issues = [issue for issue in value.issues if issue.kind!=AdapterErrorKind.notice]
        priority = {AdapterErrorKind.source_wait:3,AdapterErrorKind.temporary:2}
        issue = max(issues,key=lambda issue:(priority.get(issue.kind,1),issue.retry_at.timestamp() if issue.retry_at else 0),default=None)
        partial_error = source_error(AdapterException(issue)) if issue else None
        return FetchResult(replace(teaser, full=not value.media and not teaser.reason,collection_error=partial_error), self.observation())
