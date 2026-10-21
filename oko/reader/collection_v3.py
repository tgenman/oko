"""Read-only collection progress boundary. Diagnostics are loaded per source."""
from collections import Counter
from typing import Any
from fastapi import APIRouter, Depends, FastAPI
from oko.reader.generated import models as wire
from oko.reader.runtime import Runtime, ActionError
from oko.storage.collection_status import timestamp


def source(value: dict[str, Any]) -> dict[str, Any]:
    row = {key: val for key, val in value.items() if key not in ('operations', 'last_error')}
    if row['operation']:
        row['operation'] = dict(row['operation'], last_error=None)
    history = row['history']
    row['history'] = f"История · загружено {history.get('loaded', 0)} · просмотрено {history.get('viewed', 0)}" if history else None
    return row


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(dependencies=[Depends(no_query)])

    def snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        value = runtime.collection_snapshot()
        rows = value.pop('sources')
        value['observed_at'] = timestamp(value['observed_at'])
        return value, rows

    @router.get('/api/v3/collection-status', response_model=wire.CollectionSnapshot)
    async def status() -> wire.CollectionSnapshot:
        meta, rows = snapshot()
        counts = Counter(row['state'] for row in rows)
        return wire.CollectionSnapshot.model_validate(dict(**meta, sources=[source(row) for row in rows],
            counts={state: counts[state] for state in ('collecting','queued','waiting','error','paused','complete','never','deleting','unavailable')}))

    @router.get('/api/v3/collection-status/{feed_id}', response_model=wire.CollectionDetails)
    async def details(feed_id: str) -> wire.CollectionDetails:
        meta, rows = snapshot()
        row = next((r for r in rows if r['feed_id'] == feed_id), None)
        if row is None:
            from oko.reader.reading_v3 import missing
            raise missing(wire.AffectedResource(wire.FeedResource(kind='feed', feed_id=wire.FeedId(feed_id))))
        last_error = row['last_error']
        if last_error and any(op['last_error'] and op['last_error']['attempt_id']==last_error['attempt_id'] for op in row['operations']):
            last_error = None
        return wire.CollectionDetails.model_validate(dict(**meta, source=source(row), operations=row['operations'], last_error=last_error))

    app.include_router(router)
