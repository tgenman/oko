"""Unified publication projection and HTTP conditional delivery."""
import hashlib
import json
from collections.abc import Callable
from typing import Any
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from oko.execution import read_locked
from oko.storage.reading import reading_snapshot
from oko.application.reader import ReaderApplication
from oko.reader.api_v3 import no_query
from oko.reader.generated import models as wire


def register(app: FastAPI, runtime: ReaderApplication,
             material: Callable[[str], wire.ReadableMaterial]) -> None:
    @app.get('/api/v3/publication/{item}', dependencies=[Depends(no_query)])
    @read_locked(runtime.execution)
    async def publication(item: str, request: Request) -> Response:
        from oko.reader.reading_v3 import identity
        identity(item)
        storage=runtime.queries.versions.identity
        expected=request.headers.get('X-Oko-Storage-Id')
        if expected is not None and expected!=storage:
            return JSONResponse(dict(code='storage_changed',message='Storage changed',details=None,diagnostic_id=None,command_id=None),status_code=409)
        with reading_snapshot(), runtime.queries.inputs({item}):
            value=material(item).model_dump(mode='json')
        content=value.get('content',{})
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
