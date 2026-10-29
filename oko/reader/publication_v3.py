"""Unified publication projection and HTTP conditional delivery."""
import hashlib
import json
from typing import Any
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from oko.execution import read_locked
from oko.storage.reading import reading_snapshot
from oko.application.reader import ReaderApplication
from oko.reader.api_v3 import no_query
from oko.reader.generated import models as wire
from oko.reader import snapshots as facts, projection_v3 as project
from oko.reader.reading_v3 import identity, missing, completeness, saved_filter, theses_parts


def register(app: FastAPI, runtime: ReaderApplication) -> None:
    @app.get('/api/v3/publication/{item}', dependencies=[Depends(no_query)])
    @read_locked(runtime.execution)
    async def publication(item: str, request: Request) -> Response:
        identity(item)
        storage=runtime.queries.versions.identity
        expected=request.headers.get('X-Oko-Storage-Id')
        if expected is not None and expected!=storage:
            return JSONResponse(dict(code='storage_changed',message='Storage changed',details=None,diagnostic_id=None,command_id=None),status_code=409)
        with reading_snapshot(), runtime.queries.inputs({item}):
            current = runtime.queries
            raw = current.material(item)
            if raw is None:
                raise missing(wire.AffectedResource(wire.MaterialResource(kind='material', material_id=identity(item))))
            material = facts.Material.model_validate(raw)
            raw_feed, binding = current.feed(material.feed_id)
            owner = project.subscription(facts.SubscriptionFeed.model_validate(raw_feed), binding)
            fid, url = wire.FeedId(material.feed_id), wire.SourceUrl(material.url)
            if isinstance(owner.root, (wire.TelegramWebSubscriptionFeed, wire.TelegramClientSubscriptionFeed, wire.TelegramUnconfiguredSubscriptionFeed)):
                source = wire.SourceReference(wire.TelegramSourceReference(kind='telegram', feed_id=fid, source_url=url, access=wire.TelegramAccess(owner.root.access), message_ids=[wire.TelegramMessageId(str(n)) for n in material.members] if isinstance(material.members, list) else []))
            elif owner.root.kind == 'youtube':
                source = wire.SourceReference(wire.YoutubeSourceReference(kind='youtube', feed_id=fid, source_url=url, captions=material.captions if isinstance(material.captions, str) else 'not_requested'))
            elif owner.root.kind == 'demo':
                source = wire.SourceReference(wire.DemoSourceReference(kind='demo', feed_id=fid, source_url=url))
            else:
                source = wire.SourceReference(wire.RssSourceReference(kind='rss', feed_id=fid, source_url=url))
            filtered = saved_filter(runtime, item, material.filter if isinstance(material.filter, facts.Filter) else None)
            summarized = theses_parts(material.theses if isinstance(material.theses, facts.Summary) else None,
                material.provenance if isinstance(material.provenance, (facts.CodexProvenance, facts.LocalProvenance, facts.UnknownProvenance)) else None,
                material.ai if isinstance(material.ai, facts.AI) else None, runtime.processing_wait(item, 'theses'))
            value: dict[str, Any] = dict(id=material.id, title=material.title, feed_id=material.feed_id,
                published_at=project.timestamp(material.published).root if material.published_known is not False else None,
                source_url=material.url, source_position=material.source_order if isinstance(material.source_order, int) else None,
                source=source.model_dump(mode='json'), originals=material.originals is not False,
                filter=filtered.model_dump(mode='json'), theses=summarized.model_dump(mode='json'))
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
                    loading=material.loading is True, changed=material.changed is True,
                    source_deleted=material.source_deleted is True, revision_id=material.version)
        original=content.get('text','')
        filtered=value['filter']
        summarized=value['theses']
        def state(part: dict[str, Any]) -> str:
            attempt=part['attempt']['state']
            if attempt=='running':return 'processing'
            if part.get('diagnostic_message') and attempt!='ready':return 'error'
            return {'disabled':'disabled','queued':'pending','cancelled':'pending','waiting':'pending','running':'processing','ready':'ready'}[attempt]
        f=filtered.get('result')
        summary=summarized.get('result')
        attachments=content.get('attachments',[])
        images=content.get('images',[])
        media=[dict(id=url.rsplit('/',1)[-1],kind='image',url=url,mime=None,role='image') for url in images]
        media.extend(dict(id=a['url'].rsplit('/',1)[-1],kind=a['kind'],url=a['url'],mime=a['mime'],role='attachment') for a in attachments)
        body=dict(id=value['id'],title=value['title'],feed_id=value['feed_id'],published_at=value['published_at'],
            source_url=value['source_url'],text=original,source=value['source'],source_position=value['source_position'],
            originals=value['originals'],availability=value['availability'],
            description=content.get('description',''),revision_id=content.get('revision_id'),
            completeness=content.get('completeness'),loading=content.get('loading',False),
            changed=content.get('changed',False),source_deleted=content.get('source_deleted',False),
            linked_content=content.get('linked_content'),reply_preview=content.get('reply_preview'),
            post_web_unavailable=content.get('post_web_unavailable',False),external_attachments=content.get('external_attachments',[]),
            filter=dict(state=state(filtered),result=dict(hidden=f['hidden'],reason=f['reason'],text=content.get('display_text',original),removed=f['removed'],provenance=f['provenance']) if f else None,error=filtered.get('diagnostic_message') if state(filtered)=='error' else None),
            theses=dict(state=state(summarized),result=dict(points=[p['text'] for p in summary['points']],references=[p['references'] for p in summary['points']],provenance=summary['provenance']) if summary else None,error=summarized.get('diagnostic_message') if state(summarized)=='error' else None),
            media=media)
        body=wire.PublicationResponse.model_validate(body).model_dump(mode='json')
        encoded=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        etag='"'+hashlib.sha256(storage.encode()+b'\0'+encoded).hexdigest()+'"'
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers={'ETag':etag})
        return JSONResponse(body,headers={'ETag':etag})
