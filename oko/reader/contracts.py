"""Reader wire contracts. No defaults: absent keys stay absent on the wire.

Run ``python -m oko.reader.contracts`` to export draft-07 compatible schemas for
browser checks, without starting Runtime or connecting external adapters.
"""
import json
from typing import Literal

from pydantic import ConfigDict, TypeAdapter, with_config
from typing_extensions import NotRequired, TypedDict


@with_config(ConfigDict(strict=True, extra='forbid'))
class Wire(TypedDict):
    pass


JobState = Literal['pending', 'running', 'done', 'cancelled']
Importance = Literal['normal', 'elevated', 'high']


class Folder(Wire):
    id: str
    title: str
    count: int
    position: NotRequired[int]


class HistoryError(Wire):
    item: str
    message: str


class History(Wire, total=False):
    run: str
    mode: Literal['count', 'date']
    count: int | None
    count_text: str | None
    date: str | None
    timezone: str
    since: float | None
    loaded: int
    viewed: int
    incomplete: int
    available: int
    paused: bool
    frozen: bool
    retrying: int
    unavailable: int
    errors: list[HistoryError]
    scan_complete: bool
    state: Literal['pending', 'loading', 'done', 'selecting', 'paused', 'waiting', 'cancelled']
    warnings: list[str]
    discovery_error: str | None
    discovery_retry_at: float | None
    waiting: bool
    requests: int
    cancelled: bool
    resume: int
    feed_id: str


class Feed(Wire):
    id: str
    title: str
    folder_id: str
    kind: Literal['demo', 'rss', 'telegram', 'youtube', 'unresolved']
    count: int
    identity: NotRequired[str]
    original_urls: NotRequired[list[str]]
    connection: NotRequired[str]
    transport: NotRequired[Literal['demo', 'rss', 'telegram', 'youtube'] | None]
    importance: NotRequired[Importance]
    initial_days: NotRequired[int]
    public_ai: NotRequired[bool | None]
    read_history: NotRequired[bool | None]
    auto_read: NotRequired[bool | None]
    telegram_peer: NotRequired[int | None]
    telegram_account: NotRequired[str | None]
    telegram_username: NotRequired[str | None]
    telegram_topic: NotRequired[int | None]
    initial_history: NotRequired[History | None]
    legacy_since: NotRequired[float | None]


class Navigation(Wire):
    folders: list[Folder]
    feeds: list[Feed]
    count: int


class Reference(Wire):
    id: str
    url: str


class Point(Wire):
    text: str
    sources: list[Reference]


class Summary(Wire):
    points: list[Point]


class Event(Summary):
    members: list[str]
    title: str
    updates: list[Point]
    conflicts: list[Point]


class Provenance(Wire, total=False):
    model: str | None
    processor: str
    reasoning_effort: str
    fast: bool


class Filter(Wire, total=False):
    status: Literal['off', 'pending', 'running', 'done', 'cancelled']
    hidden: bool
    restored: bool
    reason: str
    removed: list[str]
    error: str | None


class AI(Wire):
    status: JobState
    error: str | None


class Attachment(Wire):
    url: str
    kind: Literal['image', 'video', 'audio', 'circle', 'file']
    mime: str


class Previous(Wire):
    url: str
    number: NotRequired[int]
    version: NotRequired[str]


class Content(Wire):
    id: str
    feed_id: str
    url: str
    published: float
    title: str
    complete: bool
    published_known: NotRequired[bool]
    source_order: NotRequired[int | None]
    version: NotRequired[str]
    images: NotRequired[list[str]]
    attachments: NotRequired[list[Attachment]]
    full: NotRequired[bool]
    reason: NotRequired[str]
    captions: NotRequired[Literal['available', 'not_requested', 'unavailable', 'error'] | None]
    loading: NotRequired[bool]
    changed: NotRequired[bool]
    previous: NotRequired[list[Previous]]
    source_deleted: NotRequired[bool]
    originals: NotRequired[bool]
    filter: NotRequired[Filter]
    theses: NotRequired[Summary | None]
    provenance: NotRequired[Provenance | None]
    ai: NotRequired[AI | None]
    event: NotRequired[Event]
    digest: NotRequired[Summary]
    batch: NotRequired[str]
    folder_id: NotRequired[str]
    importance: NotRequired[Importance]
    members: NotRequired[list[int]]


class CardSource(Reference):
    feed_id: str
    title: str


class Card(Content):
    feed_title: NotRequired[str]
    importance: NotRequired[Importance]
    snippet: NotRequired[str]
    sources: NotRequired[list[CardSource]]


class Material(Content):
    text: str
    description: NotRequired[str]
    display_text: NotRequired[str]
    sources: NotRequired[list['Material']]


class Version(Wire):
    title: str
    text: str
    full: bool
    reason: str
    images: list[str]
    attachments: list[Attachment]


class Job(Wire):
    kind: Literal['discover', 'fetch', 'theses', 'filter', 'events', 'digest', 'delete', 'read']
    status: JobState
    error: str | None
    count: int


class SourceStatus(Wire):
    feed_id: str
    title: str
    error: NotRequired[str | None]
    gap: NotRequired[str | None]
    changes: NotRequired[str | None]


class TelegramRead(Wire):
    feed_id: str
    initial: int | None
    confirmed: int
    pending: int
    waiting: str
    error: str


class ReviewRestart(Wire):
    id: str
    error: str


class YouTube(Wire, total=False):
    strikes: int
    reason: str
    mode: Literal['direct', 'proxy']
    address: str
    has_password: bool
    has_username: bool
    paused: bool
    next_at: float
    requests: int
    error: str | None
    waiting: str | None


class Status(Wire):
    stage: str
    errors: list[str]
    sources: list[SourceStatus]
    ready_sources: int
    total_sources: int
    jobs: NotRequired[list[Job]]
    deleting: NotRequired[bool]
    youtube: NotRequired[YouTube | None]
    initial_histories: NotRequired[list[History]]
    telegram_reads: NotRequired[list[TelegramRead]]
    error: NotRequired[str | None]
    stopped: NotRequired[bool]
    live_tabs: NotRequired[int]
    review_restarts: NotRequired[list[ReviewRestart]]
    debug_count: NotRequired[int]
    debug_persistence_error: NotRequired[str]


CONTRACTS: dict[str, TypeAdapter[object]] = {
    'navigation': TypeAdapter(Navigation),
    'cards': TypeAdapter(list[Card]),
    'material': TypeAdapter(Material),
    'version': TypeAdapter(Version),
    'status': TypeAdapter(Status),
}


def schemas() -> dict[str, object]:
    result: dict[str, object] = {}
    for name, adapter in CONTRACTS.items():
        schema = adapter.json_schema(ref_template='#/definitions/{model}')
        if '$defs' in schema:
            schema['definitions'] = schema.pop('$defs')
        schema['$schema'] = 'http://json-schema.org/draft-07/schema#'
        result[name] = schema
    return result


if __name__ == '__main__':
    print(json.dumps(schemas(), ensure_ascii=False))
