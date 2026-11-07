"""Unified publication projection and HTTP conditional delivery."""
import hashlib
import json
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from oko.execution import read_locked
from oko.storage.reading import reading_snapshot
from oko.storage.transaction import read_snapshot
from oko.application.reader import ReaderApplication
from oko.reader.api_v3 import no_query
from oko.reader.generated import models as wire
from oko.reader import projection_v3 as project
from oko.reader.reading_v3 import identity, missing


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
            current=runtime.queries
            row=current.item(item)
            if row is None or row['feed_id'] is None or not current.feed_resources({row['feed_id']}):
                raise missing(wire.AffectedResource(wire.MaterialResource(kind='material',material_id=identity(item))))
            body=dict(id=row['id'],title=row['title'] or '',feed_id=row['feed_id'],
                published_at=project.timestamp(row['published_at']).root if row['published_at'] is not None else None,
                source_url=row['url'],**current.unified_publication(item))
        body=wire.PublicationResponse.model_validate(body).model_dump(mode='json')
        encoded=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        etag='"'+hashlib.sha256(storage.encode()+b'\0'+encoded).hexdigest()+'"'
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers={'ETag':etag})
        return JSONResponse(body,headers={'ETag':etag})
