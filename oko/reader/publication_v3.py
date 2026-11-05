"""Unified publication projection and HTTP conditional delivery."""
import hashlib
import json
from typing import Any
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from oko.execution import read_locked
from oko.storage.reading import reading_snapshot
from oko.storage.transaction import read_snapshot
from oko.application.reader import ReaderApplication
from oko.reader.api_v3 import no_query
from oko.reader.generated import models as wire
from oko.reader import snapshots as facts, projection_v3 as project
from pydantic import TypeAdapter
from oko.reader.reading_v3 import identity, missing, completeness, provenance


def register(app: FastAPI, runtime: ReaderApplication) -> None:
    @app.get('/api/v3/publication/{item}', dependencies=[Depends(no_query)])
    @read_locked(runtime.execution)
    async def publication(item: str, request: Request) -> Response:
        identity(item)
        storage=runtime.queries.versions.identity
        expected=request.headers.get('X-Oko-Storage-Id')
        if expected is not None and expected!=storage:
            return JSONResponse(dict(code='storage_changed',message='Storage changed',details=None,diagnostic_id=None,command_id=None),status_code=409)
        with read_snapshot(runtime.require_store().db), reading_snapshot(), runtime.queries.inputs({item}):
            current = runtime.queries
            raw = current.material(item)
            if raw is None:
                raise missing(wire.AffectedResource(wire.MaterialResource(kind='material', material_id=identity(item))))
            material = facts.Material.model_validate(raw)
            raw_feed, binding = current.feed(material.feed_id)
            owner = project.subscription(facts.SubscriptionFeed.model_validate(raw_feed), binding)
            fid, url = wire.FeedId(material.feed_id), wire.SourceUrl(material.url)
            if isinstance(owner.root, (wire.TelegramWebSubscriptionFeed, wire.TelegramUnconfiguredSubscriptionFeed)):
                source = wire.SourceReference(wire.TelegramSourceReference(kind='telegram', feed_id=fid, source_url=url, access=wire.TelegramAccess(owner.root.access), message_ids=[wire.TelegramMessageId(str(n)) for n in material.members] if isinstance(material.members, list) else []))
            elif owner.root.kind == 'youtube':
                source = wire.SourceReference(wire.YoutubeSourceReference(kind='youtube', feed_id=fid, source_url=url, captions=material.captions if isinstance(material.captions, str) else 'not_requested'))
            elif owner.root.kind == 'unresolved':
                source = wire.SourceReference(wire.UnresolvedSourceReference(kind='unresolved', feed_id=fid, source_url=url))
            else:
                source = wire.SourceReference(wire.RssSourceReference(kind='rss', feed_id=fid, source_url=url))
            filtered = material.filter if isinstance(material.filter, facts.Filter) else None
            summary = material.theses if isinstance(material.theses, facts.Summary) else None
            ai = material.ai if isinstance(material.ai, facts.AI) else None
            saved_origin = current.provenance(item, 'filter') if filtered and filtered.status == 'done' else None
            origin_type = facts.CodexProvenance | facts.LocalProvenance | facts.UnknownProvenance
            filter_origin = TypeAdapter(origin_type).validate_python(saved_origin) if saved_origin else None
            summary_origin = material.provenance if isinstance(material.provenance, (facts.CodexProvenance, facts.LocalProvenance, facts.UnknownProvenance)) else None
            def state(status: str, message: str | None, ready: bool) -> str:
                if ready: return 'ready'
                if status == 'running': return 'processing'
                if message: return 'error'
                return 'disabled' if status == 'off' else 'pending'
            filter_message = filtered.error if filtered and isinstance(filtered.error, str) else None
            filter_ready = bool(filtered and filtered.status == 'done')
            filter_status = state(filtered.status if filtered else 'off', filter_message, filter_ready)
            summary_status = state(ai.status if ai else 'off', ai.error if ai else None, bool(ai and ai.status == 'done' and summary))
            filter_result = dict(hidden=filtered.hidden, reason=filtered.reason, removed=filtered.removed, provenance=provenance(filter_origin).model_dump(mode='json')) if filtered and filter_ready else None
            summary_result = dict(points=[point.text for point in summary.points], references=[[dict(material_id=ref.id, source_url=ref.url) for ref in point.sources] for point in summary.points], provenance=provenance(summary_origin).model_dump(mode='json')) if summary else None
            value: dict[str, Any] = dict(id=material.id, title=material.title, feed_id=material.feed_id,
                published_at=project.timestamp(material.published).root if material.published_known is not False else None,
                source_url=material.url, source=source.model_dump(mode='json'),
                filter=dict(state=filter_status, result=filter_result, error=filter_message if filter_status=='error' else None),
                theses=dict(state=summary_status, result=summary_result, error=ai.error if ai and summary_status=='error' else None))
            value['availability'] = 'available' if material.complete else 'pending'
            content: dict[str, Any] = {'loading': material.loading is not False}
            if material.complete:
                if not isinstance(material.version, str) or not isinstance(material.full, bool) or not isinstance(material.reason, str):
                    raise ValueError('Saved material lacks revision or completeness')
                content = dict(text=material.text,
                    display_text=material.display_text if isinstance(material.display_text, str) else material.text,
                    description=material.description if isinstance(material.description, str) else '',
                    linked_content=material.linked_content.model_dump(mode='json') if material.linked_content else None,
                    reply_preview=material.reply_preview.model_dump(mode='json') if material.reply_preview else None,
                    post_web_unavailable=material.post_web_unavailable,
                    external_attachments=[attachment.model_dump(mode='json') for attachment in material.external_attachments],
                    images=material.images if isinstance(material.images, list) else [],
                    attachments=[attachment.model_dump(mode='json') for attachment in material.attachments] if isinstance(material.attachments, list) else [],
                    completeness=completeness(material.full, material.reason, material.external_only, material.post_web_unavailable).model_dump(mode='json'),
                    loading=material.loading is True, revision_id=material.version)
            unified=current.unified_publication(item)
        original=content.get('text','')
        filtered=value['filter']
        summarized=value['theses']
        f=filtered['result']
        summary=summarized['result']
        media=unified.pop('media')
        body=dict(id=value['id'],title=value['title'],feed_id=value['feed_id'],published_at=value['published_at'],
            source_url=value['source_url'],text=original,source=value['source'],availability=value['availability'],
            description=content.get('description',''),revision_id=content.get('revision_id'),
            completeness=content.get('completeness'),loading=content.get('loading',False),
            linked_content=content.get('linked_content'),reply_preview=content.get('reply_preview'),
            post_web_unavailable=content.get('post_web_unavailable',False),external_attachments=content.get('external_attachments',[]),
            filter=dict(state=filtered['state'],result=dict(**f,text=content.get('display_text',original)) if f else None,error=filtered['error']),
            theses=summarized,
            media=media,**unified)
        body=wire.PublicationResponse.model_validate(body).model_dump(mode='json')
        encoded=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        etag='"'+hashlib.sha256(storage.encode()+b'\0'+encoded).hexdigest()+'"'
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers={'ETag':etag})
        return JSONResponse(body,headers={'ETag':etag})
