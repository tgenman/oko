"""Translate collection failures without changing external adapter contracts."""
from datetime import datetime,timedelta,timezone

import httpx

from oko.collection.adapters import AdapterErrorKind,AdapterException,AdapterIssue,ExternalReference
from oko.collection.contracts import SourceError,SourceDeleted,SourceDeferred


def failure(error: SourceError, publication: ExternalReference | None = None,
            media_key: str | None = None, scope: str = 'source') -> AdapterException:
    kind=AdapterErrorKind.temporary if error.retryable else AdapterErrorKind.permanent
    wait_scope,retry_at=None,None
    if isinstance(error,SourceDeleted):
        kind=AdapterErrorKind.unavailable
    cause=error.__cause__
    if isinstance(error,SourceDeferred) or isinstance(cause,httpx.HTTPStatusError) and cause.response.status_code==429:
        kind=AdapterErrorKind.source_wait
        wait_scope=getattr(error,'scope',scope)
        retry_at=datetime.now(timezone.utc)+timedelta(seconds=error.delay)
    return AdapterException(AdapterIssue(error.code or 'source_unavailable',error.label,kind,publication,media_key,wait_scope,retry_at))
