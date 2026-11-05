"""Telegram adapter contracts; no SDK, HTML, storage or process ownership here.

This foundation is adopted by bindings/history in #18–20. Source keeps discover/fetch with complete per-call results. Store may decode selection, but must not interpret position.
"""
from dataclasses import dataclass
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from oko.collection.contracts import SourceError, ExternalMedia

Adapter = Literal['web']
Lane = Literal['common']


@dataclass(frozen=True)
class TelegramBinding:
    cursor_key: str
    lane: Lane


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
class TelegramPost:
    id: str
    url: str
    published: float | None
    members: tuple[int, ...]
    title: str
    text: str
    description: str = ''
    reply_preview: dict[str, Any] | None = None
    restricted: bool = False
    malformed: bool = False
    media: tuple[ExpectedMedia, ...] = ()


@dataclass(frozen=True)
class TelegramSourceInfo:
    title: str
    username: str | None
    peer_id: int | None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelegramPage:
    source: TelegramSourceInfo
    publications: tuple[TelegramPost, ...]
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



    def encode(self) -> str:
        return self.model_dump_json()

    @classmethod
    def decode(cls, cursor: str, *, adapter: Adapter) -> 'HistoryEnvelope':
        try:
            value = json.loads(cursor)
            if not isinstance(value, dict):
                raise ValueError('object expected')
            result = cls.model_validate_json(json.dumps(value))
            if result.adapter != adapter:
                raise ValueError('adapter mismatch')
            return result
        except (ValueError, TypeError, ValidationError):
            raise SourceError('Несовместимый курсор Telegram', code='telegram-cursor-incompatible') from None
