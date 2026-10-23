"""Temporary projection of the adapter API into existing reader storage (#90).

Only this layer knows legacy message IDs, staging and history envelopes. The
external adapter sees URL references and an open output stream.
"""
import asyncio
from dataclasses import replace
import hashlib
import json
from typing import Any, Literal, cast

from oko.collection.adapters import AdapterException, AdapterErrorKind, PublicationContent
from oko.collection.contracts import (
    DownloadStage, StagedPayload, SourceError, SourceManifest, Material,
    Publication, FetchResources, FetchResult, DiscoveryResult, ReplyPreview, Attachment,
)
from oko.collection.telegram.adapter import TelegramWebAdapter, reference, publication_reference
from oko.collection.telegram.contracts import HistoryEnvelope, HistorySelection, ExpectedMedia
from oko.collection.telegram.media import Downloader
from oko.collection.telegram.source import TelegramSource
from oko.collection.telegram.telegraph import enrich


def source_error(error: AdapterException) -> SourceError:
    if isinstance(error.__cause__, SourceError):
        error.__cause__.adapter_issue = error.issue
        return error.__cause__
    result = SourceError(error.issue.message, code=error.issue.code,
                       retryable=error.issue.kind.value in ('temporary', 'source_wait'))
    result.adapter_issue = error.issue
    return result


def material(value: PublicationContent, previous: Material | None = None) -> Material:
    extra = value.content['extra']['telegram']
    manifest = SourceManifest.model_validate_json(json.dumps(extra['manifest']))
    retained_attachments: tuple[Attachment, ...] = ()
    if previous and previous.source_manifest and manifest.articles:
        old_articles = {a.url:a for a in previous.source_manifest.articles}
        old_resources = {r.key:r for r in previous.source_manifest.resources}
        articles = []
        resources = list(manifest.resources)
        retained_names = set()
        retained_keys = set()
        for article in manifest.articles:
            old = old_articles.get(article.url)
            if article.text is None and old and old.text is not None:
                article = old.model_copy(update={'reason':article.reason})
                for key in old.resource_keys:
                    if key in old_resources:
                        resource = old_resources[key]
                        resources.append(resource)
                        retained_names.add(resource.name)
                        retained_keys.add(key)
            articles.append(article)
        retained_attachments = tuple(a for a in previous.attachments if a.name in retained_names)
        manifest = manifest.model_copy(update={'articles':tuple(articles),'resources':tuple(resources)})
        if previous.adapter_content:
            present = {r.key for r in value.media}
            value = replace(value,media=value.media+tuple(r for r in previous.adapter_content.media if r.key in retained_keys and r.key not in present))
    if manifest.preview_error and previous and previous.source_manifest and previous.source_manifest.preview_fragment:
        manifest = manifest.model_copy(update={'preview_fragment': previous.source_manifest.preview_fragment})
    reply = manifest.post.get('reply_preview')
    title, text = value.metadata.title, str(manifest.post['text'])
    reason = 'Вложения ещё не загружены' if value.media else ''
    if manifest.post_web_unavailable and text.strip():
        preview = manifest.preview_fragment or ''
        headline = next((line.strip(' \t\r\u2063\u200b\ufeff') for line in preview.splitlines() if any(char.isalnum() for char in line)), '')
        title = (headline if len(headline) <= 140 else headline[:140].rstrip()+'…') or 'Фрагмент предпросмотра Telegram недоступен'
        text = '\n\n'.join(part for part in (text, preview) if part)
        reason = ('Сохранён фрагмент предпросмотра; полная публикация недоступна для веб-просмотра'
                  if preview else 'Фрагмент предпросмотра недоступен; откройте пост в Telegram')
    if manifest.preview_fragment is None and manifest.articles:
        sections = [text]
        for article in manifest.articles:
            if article.text is not None:
                sections.append('---\n\n# '+(article.title or 'Telegraph')+'\n\nИсходник: '+article.url+'\n\n'+article.text)
            else:
                sections.append('Telegraph: '+article.url+'\nСтатья ещё не сохранена.')
        text = '\n\n'.join(sections)
        reason = '; '.join(value for value in dict.fromkeys([reason,*[article.reason for article in manifest.articles]]) if value)
    return Material(title, text, False, reason,
                    members=tuple(extra['members']), description=extra['description'],
                    attachments=retained_attachments,
                    source_manifest=manifest,
                    reply_preview=ReplyPreview.model_validate(reply) if reply else None, adapter_content=value)


def legacy(value: PublicationContent) -> Publication:
    result = material(value) if value.content else None
    identity = value.metadata.reference.url.rstrip('/').split('/')[-1]
    return Publication(identity, value.metadata.reference.url,
        value.metadata.published_at.timestamp() if value.metadata.published_at else 0,
        result, result.members if result else (int(identity),), published_known=value.metadata.published_at is not None,
        adapter_reference=value.metadata.reference)


class _Downloads(Downloader):
    def __init__(self, adapter: TelegramWebAdapter, value: PublicationContent, previous: Material | None = None) -> None:
        super().__init__(adapter.transport)
        self.adapter, self.value = adapter, value
        names = {r['key']:r['name'] for r in value.content['extra']['telegram']['manifest']['resources']}
        if previous and previous.source_manifest:
            names.update({r.key:r.name for r in previous.source_manifest.resources})
        self.by_name = {names[r.key]:r for r in value.media}

    async def download(self, url: str, stage: DownloadStage, name: str) -> StagedPayload:
        if not stage.admitted():
            raise asyncio.CancelledError
        media = self.by_name[name]
        if media.locator != url:
            raise ValueError('Legacy media locator does not match the adapter descriptor')
        target = stage.path / ('incoming-'+name)
        try:
            with target.open('wb') as output:
                await self.adapter.download_media(self.value.metadata.reference, media, output)
            if not target.stat().st_size:
                raise SourceError('Пустое вложение')
            if not stage.admitted():
                raise asyncio.CancelledError
            digest = hashlib.sha256()
            with target.open('rb') as saved:
                while part := saved.read(1024*1024):
                    digest.update(part)
            return StagedPayload(target, target.stat().st_size, digest.hexdigest())
        except BaseException as error:
            target.unlink(missing_ok=True)
            if isinstance(error, AdapterException):
                raise source_error(error)
            raise


class TelegramWebSource(TelegramSource):
    """Use the external API while keeping old storage/HTTP presentation working."""
    independent_media = False

    def publication_adapter(self) -> TelegramWebAdapter:
        if not hasattr(self, '_publication_adapter'):
            self._publication_adapter = TelegramWebAdapter(self.http_transport,linked_descriptions=self.independent_media)
        return self._publication_adapter

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        # Existing in-progress cursors and date selection finish on their old
        # bounded algorithm. New count runs use the agreed adapter API.
        previous = HistoryEnvelope.decode(cursor, adapter='web') if cursor else None
        if ((previous and 'adapter_cursor' not in previous.position)
                or not self.initial_history or self.initial_history.get('mode') != 'count'):
            previous_result = await super().discover(cursor)
            return replace(previous_result, publications=tuple(replace(p, adapter_reference=publication_reference(p.url)) for p in previous_result.publications))
        assert self.username
        api = self.publication_adapter()
        feed = reference('https://t.me/s/'+self.username)
        state: dict[str, Any] = dict(previous.position) if previous else {}
        observations: tuple[Publication, ...] = ()
        warnings: tuple[str, ...] = ()
        try:
            if previous and previous.selection.scan_complete:
                # Preserve the existing restriction-reclassification behavior
                # until the storage migration: observe only the public page
                # already used by a normal refresh, never enroll older items.
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
                state = dict(adapter_cursor=metadata.latest_cursor, locating=True, since=0)
                complete = not metadata.latest_cursor
                result: tuple[Publication, ...] = ()
            elif previous and self.initial_history.get('paused') and not previous.selection.scan_complete:
                selection = previous.selection.model_copy(update={'paused': True, 'enrolled_ids': ()})
                return DiscoveryResult((), previous.model_copy(update={'selection': selection}).encode(), self.observation())
            else:
                token = state['adapter_cursor']
                if state.get('locating'):
                    token = await api.cursor_before(feed, token, self.initial_history['count']-1)
                    state['locating'] = False
                page = await api.discover(feed, token, 50)
                state['adapter_cursor'] = page.next_cursor
                result = tuple(legacy(value) for value in page.publications)
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
                               continuation=.1 if not complete else None)

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        stage = resources.staging
        if stage and not stage.admitted():
            raise asyncio.CancelledError
        api = self.publication_adapter()
        try:
            value = await api.fetch(reference(publication.url))
        except AdapterException as error:
            raise source_error(error)
        if value.needs_fetch:
            raise SourceError('Не удалось разобрать публикацию Telegram', code='malformed_publication', retryable=False)
        teaser = material(value, publication.teaser)
        if self.independent_media:
            # The canonical store saves descriptions and schedules each file.
            # Linked-article collection is separated in #94.
            # A failed file cannot stop retries of unfinished article metadata.
            issues = [issue for issue in value.issues if issue.media_key is None and issue.kind!=AdapterErrorKind.notice]
            priority = {AdapterErrorKind.source_wait:3,AdapterErrorKind.temporary:2}
            issue = max(issues,key=lambda issue:(priority.get(issue.kind,1),issue.retry_at.timestamp() if issue.retry_at else 0),default=None)
            partial_error = source_error(AdapterException(issue)) if issue else None
            return FetchResult(replace(teaser, full=not value.media and not teaser.reason,collection_error=partial_error), self.observation())
        manifest = teaser.source_manifest
        assert manifest
        if manifest.post_web_unavailable and str(manifest.post['text']).strip():
            return FetchResult(await enrich(teaser, publication.teaser, stage, self.http_transport), self.observation())
        by_key = {item.key: item for item in value.media}
        expected = tuple(ExpectedMedia(r.key, 'photo' if r.kind=='image' else
            'document' if r.kind=='file' else cast(Literal['photo', 'video', 'audio', 'document', 'unsupported'], r.kind), by_key[r.key].locator if r.key in by_key else None,
            r.name, r.mime, reason=r.reason, external=r.external) for r in manifest.resources)
        collected = await _Downloads(api, value, publication.teaser).collect(expected, manifest.resources, publication.teaser, stage)
        partial = bool(collected.error and collected.error.code == 'media-unavailable'
                       and (teaser.text.strip() or collected.attachments))
        manifest = manifest.model_copy(update={'resources': collected.resources, 'download_partial': partial})
        reasons = list(collected.reasons)
        warnings = [r.reason for r in collected.resources if r.reason]
        old = publication.teaser.source_manifest if publication.teaser else None
        if old and old.post != manifest.post:
            reasons.extend(warnings)
        result = Material(teaser.title, teaser.text,
            not partial and not manifest.post_web_unavailable and not reasons and not any(r.status=='external' for r in collected.resources),
            '; '.join(dict.fromkeys([*reasons, *warnings])), members=teaser.members,
            attachments=collected.attachments, description=teaser.description,
            source_manifest=manifest, collection_error=None if partial else collected.error, reply_preview=teaser.reply_preview,
            adapter_content=value)
        if stage and not stage.admitted():
            raise asyncio.CancelledError
        return FetchResult(await enrich(result, publication.teaser, stage, self.http_transport), self.observation())
