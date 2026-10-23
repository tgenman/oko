"""Telegram adapter contracts; no SDK, HTML, storage or process ownership here.

This foundation is adopted by bindings/history in #18–20. Source keeps discover/fetch with complete per-call results. Store may decode selection, but must not interpret position.
"""
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from oko.collection.contracts import Publication, Source, SourceError, ExternalMedia

Adapter = Literal['web', 'client']
Profile = Literal['personal', 'collection']
Lane = Literal['common', 'personal', 'collection']


@dataclass(frozen=True)
class ClientReadBinding:
    account: str
    user_id: int
    peer_id: int
    generation: int
    lower_bound: int


@dataclass(frozen=True)
class TelegramBinding:
    source: Source
    cursor_key: str
    lane: Lane
    read: ClientReadBinding | None = None


@dataclass(frozen=True)
class ExpectedMedia:
    id: str
    kind: Literal['photo', 'video', 'audio', 'document', 'unsupported']
    url: str | None
    name: str
    mime: str | None = None
    size: int | None = None
    reason: str | None = None
    external: ExternalMedia | None = None
    original_name: str = ''


@dataclass(frozen=True)
class TelegramPublication:
    publication: Publication
    text: str
    media: tuple[ExpectedMedia, ...] = ()
    # Internal read scopes only; not separate user-visible feeds.
    topic_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class TelegramSourceInfo:
    title: str
    username: str | None
    peer_id: int | None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelegramPage:
    source: TelegramSourceInfo
    publications: tuple[TelegramPublication, ...]
    position: dict[str, JsonValue]
    more: bool
    # None means the adapter has not established an archive boundary yet.
    upper: int | None = None
    oldest: int | None = None
    warnings: tuple[str, ...] = ()


class HistorySelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)
    since: int | float | None = None
    enrolled_ids: tuple[str, ...] = ()
    scan_complete: bool = False
    oldest: int | None = None
    paused: bool = False
    warnings: tuple[str, ...] = ()
    error: str | None = None
    retry_at: float | None = Field(default=None, ge=0)


class HistoryEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    format_version: Literal[1] = 1
    adapter: Adapter
    selection: HistorySelection
    position: dict[str, JsonValue]

    def progress_state(self) -> dict[str, JsonValue]:
        """Combine source-owned progress with its opaque adapter position for one bounded step."""
        return dict(self.position, since=self.selection.since, history_enroll=list(self.selection.enrolled_ids),
                    scan_complete=self.selection.scan_complete, oldest=self.selection.oldest,
                    paused=self.selection.paused, warnings=list(self.selection.warnings),
                    history_error=self.selection.error, history_retry_at=self.selection.retry_at or 0)

    @classmethod
    def from_progress(cls, state: dict[str, JsonValue], *, adapter: Adapter) -> 'HistoryEnvelope':
        value = dict(state)
        enrollment = value.pop('history_enroll', [])
        enrollment = value.pop('history_ids', enrollment)
        selection = dict(since=value.pop('since',None), enrolled_ids=enrollment,
            scan_complete=value.pop('scan_complete', True), oldest=value.pop('oldest', None),
            paused=value.pop('paused', False), warnings=value.pop('warnings', []),
            error=value.pop('history_error', None), retry_at=value.pop('history_retry_at', None))
        return cls.model_validate_json(json.dumps(dict(format_version=1,adapter=adapter,selection=selection,position=value)))

    def encode(self) -> str:
        return self.model_dump_json()

    @classmethod
    def decode(cls, cursor: str, *, adapter: Adapter) -> 'HistoryEnvelope':
        try:
            value = json.loads(cursor)
            if not isinstance(value, dict):
                raise ValueError('object expected')
            if 'format_version' not in value:
                if adapter != 'client' or not {'after', 'since'} <= value.keys():
                    raise ValueError('not a legacy client cursor')
                # Legacy client fields migrate through the same source-owned
                # progress encoder; web never accepts an unversioned cursor.
                return cls.from_progress(value,adapter='client')
            if isinstance(value.get('selection'),dict) and isinstance(value.get('position'),dict) and 'since' in value['position']:
                floor = value['position'].pop('since')
                known = value['selection'].get('since')
                if known is not None and known != floor:
                    raise ValueError('conflicting lower boundary')
                value['selection']['since'] = floor
            result = cls.model_validate_json(json.dumps(value))
            if result.adapter != adapter:
                raise ValueError('adapter mismatch')
            return result
        except (ValueError, TypeError, ValidationError):
            raise SourceError('Несовместимый курсор Telegram', code='telegram-cursor-incompatible') from None
