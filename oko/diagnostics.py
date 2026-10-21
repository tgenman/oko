"""Bounded local exception groups, independent of material storage."""
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback
from typing import Any
import uuid
from anyio import EndOfStream, WouldBlock

from pydantic import BaseModel, ConfigDict, Field, field_validator
from oko.collection.contracts import SourceDeleted


class BrowserError(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: str = Field(min_length=1, max_length=500)
    message: str = Field(max_length=4000)
    traceback: str = ''
    error_type: str = Field(default='Error', max_length=200)
    location: str = Field(default='', max_length=1000)
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100)
    occurred_at: datetime | None = None
    first_at: datetime | None = None
    count: int = Field(default=1, ge=1, le=1_000_000)
    stream_id: str = Field(default='', max_length=100)
    sequence: int = Field(default=1, ge=1, le=9_007_199_254_740_991)
    material_ids: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator('occurred_at', 'first_at', mode='before')
    @classmethod
    def browser_timestamp(cls, value: Any) -> Any:
        # JSON carries ISO strings; keep strict validation for all other types.
        return datetime.fromisoformat(value) if isinstance(value, str) else value


def redact(value: str, secret: str = '') -> str:
    value = value.replace(secret, '[session]') if secret else value
    sensitive = r'(?:signature|sig|signed|policy|key-pair-id|expires|x-amz-[a-z0-9-]+|x-goog-[a-z0-9-]+|awsaccesskeyid|googleaccessid|auth|cookie|set-cookie|session|sessionid|session_id|jwt|credential)'
    value = re.sub(r'(?i)([?&#]' + sensitive + r'=)[^\s&#\"\']+', r'\1[hidden]', value)
    value = re.sub(r'''(?im)(["']?(?:cookie|set-cookie)["']?\s*[:=]\s*)[^\r\n]+''', r'\1[hidden]', value)
    value = re.sub(r'''(?is)(["']?(?:password|api[_-]?key|access[_-]?token|token|secret|authorization|session_string|string_session)["']?\s*[:=]\s*)(["'])(?:\\.|(?!\2).)*\2''', r'\1\2[hidden]\2', value)
    value = re.sub(r'(?i)((?:Bearer|Basic)\s+)[^\s"\']+', r'\1[hidden]', value)
    value = re.sub(r'(?i)([?&#](?:token|api_key|access_token|password|secret)=)[^\s&#"\']+', r'\1[hidden]', value)
    value = re.sub(r'''(?i)(["']?(?:password|api[_-]?key|access[_-]?token|token|secret|authorization|session_string|string_session)["']?\s*[:=]\s*["']?)[^\s,;"'{}]+''', r'\1[hidden]', value)
    return re.sub(r'(https?://)[^/\s@]+@', r'\1[hidden]@', value)


def exception_details(error: BaseException) -> dict[str, str]:
    frames = traceback.extract_tb(error.__traceback__)
    return dict(traceback=redact(''.join(traceback.format_exception(error))), error_type=type(error).__name__,
                message=redact(str(error)), location=f'{frames[-1].filename}:{frames[-1].lineno}:{frames[-1].name}' if frames else type(error).__name__)


class Journal:
    def __init__(self, root: Path, secret: str) -> None:
        self.path = root / 'debug.json'
        self.secret = secret
        self.records: list[dict[str, Any]] = []
        self.forgotten_materials: set[str] = set()
        self.persistence_error = ''
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                if not isinstance(data, list):
                    raise ValueError('Некорректный журнал диагностики')
                for row in data[-100:]:
                    if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ('source', 'message', 'traceback')):
                        raise ValueError('Некорректная группа диагностики')
                    # Upgrade the prior ungrouped local draft without discarding errors.
                    timestamp = str(row.get('last_time') or row.get('time') or self.now())
                    self._add(row['source'], row['message'], row['traceback'], row.get('error_type', 'Error'),
                              row.get('location', ''), timestamp,
                              count=int(row.get('count', 1)), first=row.get('first_time', timestamp),
                              streams=row.get('streams', {f'delivery:{identity}': 1 for identity in row.get('deliveries', [])}),
                              material_ids=row.get('material_ids'))
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.capture(error, 'Загрузка журнала')

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def clean(self, value: str) -> str:
        return redact(value, self.secret)

    def _add(self, source: str, message: str, stack: str, error_type: str, location: str,
             timestamp: str, *, count: int = 1, first: str | None = None,
             streams: dict[str, int] | None = None, stream_id: str = '', sequence: int = 0,
             material_ids: list[str] | None = None) -> str:
        if self.forgotten_materials.intersection(material_ids or []):
            return ''
        source, message, stack = self.clean(source), self.clean(message), self.clean(stack)
        stream_id = self.clean(stream_id)
        streams = {self.clean(sender): latest for sender, latest in (streams or {}).items()}
        error_type, location = self.clean(error_type)[:200], self.clean(location)[:1000]
        key = hashlib.sha256(json.dumps([source, error_type, message, location], ensure_ascii=False).encode()).hexdigest()[:32]
        old = next((row for row in self.records if row['id'] == key), None)
        if old:
            if stream_id and sequence <= old['streams'].get(stream_id, 0):
                return key
            old['count'] += count
            old['last_time'] = max(old['last_time'], timestamp)
            old['first_time'] = min(old['first_time'], first or timestamp)
            old['traceback'] = stack
            if stream_id:
                old['streams'][stream_id] = sequence
            for sender, latest in (streams or {}).items():
                old['streams'][sender] = max(old['streams'].get(sender, 0), latest)
        else:
            self.records.append(dict(id=key, source=source, message=message, traceback=stack,
                                     error_type=error_type, location=location, count=count,
                                     first_time=first or timestamp, last_time=timestamp,
                                     streams={**(streams or {}), **({stream_id: sequence} if stream_id else {})}))
        if material_ids is not None:
            entry = next(row for row in self.records if row['id'] == key)
            entry['material_ids'] = sorted(set(entry.get('material_ids', []) + material_ids))
        self.records.sort(key=lambda row: row['last_time'])
        self.records = self.records[-100:]
        return key

    def add(self, source: str, message: str, stack: str, *, error_type: str = 'Error',
            location: str = '', event_id: str | None = None, timestamp: str | None = None,
            count: int = 1, first: str | None = None, stream_id: str = '', sequence: int = 0,
            material_ids: list[str] | None = None) -> str:
        if not stream_id and event_id:
            stream_id, sequence = 'delivery:' + event_id, 1
        key = self._add(source, message, stack, error_type, location, timestamp or self.now(), count=count, first=first, stream_id=stream_id, sequence=sequence,
                        material_ids=material_ids if material_ids is not None else list(diagnostic_materials.get()))
        self.flush()
        return key

    def flush(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.records, ensure_ascii=False))
            temporary.replace(self.path)
            self.persistence_error = ''
        except OSError as error:
            self.persistence_error = self.clean(str(error))

    def clear(self) -> bool:
        previous = self.records
        self.records = []
        self.flush()
        if self.persistence_error:
            self.records = previous
            return False
        return True

    def forget_materials(self, members: set[str]) -> None:
        self.forgotten_materials.update(members)
        # Legacy entries have no scope: retaining them could retain viewed content.
        self.records = [row for row in self.records if 'material_ids' in row and not members.intersection(row['material_ids'])]
        self.flush()

    def capture(self, error: BaseException, source: str) -> str | None:
        if isinstance(error, (asyncio.CancelledError, SourceDeleted)) or getattr(error, 'diagnostic_skip', False):
            return None
        chain: list[BaseException] = []
        current: BaseException | None = error
        while current is not None and all(current is not seen for seen in chain):
            if isinstance(current, (asyncio.CancelledError, EndOfStream, WouldBlock)) and chain:
                break
            chain.append(current)
            recorded = getattr(current, '_oko_diagnostic_group', None)
            if recorded and getattr(current, '_oko_diagnostic_scope', None) == capture_scope.get():
                return str(recorded)
            current = current.__cause__ or current.__context__
        try:
            root = chain[-1]
            parts = [''.join(traceback.format_exception(type(item), item, item.__traceback__, chain=False)) for item in chain]
            for item in chain:
                remote = getattr(item, 'remote_traceback', '')
                if remote:
                    parts.append('Worker traceback:\n' + str(remote))
            frames = traceback.extract_tb(root.__traceback__)
            location = f'{frames[-1].filename}:{frames[-1].lineno}:{frames[-1].name}' if frames else type(root).__name__
            error_type = getattr(root, 'remote_type', '') or type(root).__name__
            message = getattr(root, 'remote_message', '') or str(root) or str(error) or error_type
            remote_location = getattr(root, 'remote_location', '')
            key = self.add(diagnostic_source.get() or source, message, '\nCaused by:\n'.join(parts),
                           error_type=error_type, location=remote_location or location)
            for item in chain:
                setattr(item, '_oko_diagnostic_group', key)
                setattr(item, '_oko_diagnostic_scope', capture_scope.get())
            return key
        except Exception:
            # Exception formatting must not break the operation that failed.
            self.persistence_error = 'Не удалось записать исключение в журнал'
            return None

    def entries(self) -> list[dict[str, Any]]:
        if self.persistence_error:
            self.flush()
        return [dict(row) for row in reversed(self.records)]


active_journal: ContextVar[Journal | None] = ContextVar('oko_debug_journal', default=None)
diagnostic_source: ContextVar[str] = ContextVar('oko_debug_source', default='')
diagnostic_materials: ContextVar[tuple[str, ...]] = ContextVar('oko_debug_materials', default=())
capture_scope: ContextVar[str] = ContextVar('oko_debug_scope', default='')


def record_caught(source: str) -> None:
    journal, error = active_journal.get(), sys.exception()
    if error is not None and not isinstance(error, (SourceDeleted, asyncio.CancelledError)):
        # Partial adapters deliberately catch errors. Retain their original
        # exception for the owning collection attempt as well as the journal.
        from oko.collection.request_observation import active_capture
        from oko.collection.contracts import SourceError
        if capture := active_capture.get():
            current = error
            seen: set[int] = set()
            while current.__cause__ is not None and id(current) not in seen:
                seen.add(id(current))
                current = current.__cause__
            if capture.error is None or not isinstance(current, SourceError):
                details = exception_details(current)
                capture.error = dict(details, occurred_at=datetime.now(timezone.utc).timestamp(), origin='server')
    if journal is not None and error is not None:
        journal.capture(error, source)
