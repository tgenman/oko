"""Masked diagnostics, including explicit acknowledgement of forgotten content."""
from fastapi import APIRouter, Depends, FastAPI

from oko.diagnostics import BrowserError, Journal
from oko.reader.generated import models as wire
from oko.reader.legacy_generated import models as previous
from oko.reader.projection_v3 import diagnostic
from oko.reader.runtime import Runtime


def register(app: FastAPI, runtime: Runtime, journal: Journal) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(dependencies=[Depends(no_query)])
    @router.get('/api/v3/diagnostics', response_model=wire.DiagnosticJournal)
    async def entries() -> wire.DiagnosticJournal:
        return wire.DiagnosticJournal(groups=[diagnostic(previous.DiagnosticEntry.model_validate(entry)) for entry in journal.entries()], persistence_error=journal.persistence_error or None)

    @router.delete('/api/v3/diagnostics', response_model=wire.DiagnosticClearResult)
    async def clear() -> wire.DiagnosticClearResult:
        if journal.clear():
            return wire.DiagnosticClearResult(wire.DiagnosticClearSuccess(state='cleared'))
        return wire.DiagnosticClearResult(wire.DiagnosticClearFailure(state='persistence_failure', message=journal.persistence_error))

    @router.post('/api/v3/diagnostics', response_model=wire.DiagnosticReceipt)
    async def report(body: wire.ClientDiagnosticEvent) -> wire.DiagnosticReceipt:
        value = BrowserError.model_validate(body.model_dump(mode='json', exclude_unset=True))
        if any(runtime.material_was_viewed(identity) for identity in value.material_ids):
            return wire.DiagnosticReceipt(wire.DiagnosticIgnored(state='ignored', reason='material_viewed'))
        group = journal.add(value.source, value.message, value.traceback, error_type=value.error_type,
                            location=value.location, event_id=value.event_id,
                            timestamp=value.occurred_at.isoformat() if value.occurred_at else None,
                            count=value.count, first=value.first_at.isoformat() if value.first_at else None,
                            stream_id=value.stream_id, sequence=value.sequence, material_ids=value.material_ids)
        if journal.persistence_error:
            return wire.DiagnosticReceipt(wire.DiagnosticPersistenceFailure(state='persistence_failure', message=journal.persistence_error))
        if not group:
            return wire.DiagnosticReceipt(wire.DiagnosticIgnored(state='ignored', reason='material_viewed'))
        entry = next(entry for entry in journal.entries() if entry['id'] == group)
        return wire.DiagnosticReceipt(wire.DiagnosticAccepted(state='accepted', entry=diagnostic(previous.DiagnosticEntry.model_validate(entry))))

    app.include_router(router)
