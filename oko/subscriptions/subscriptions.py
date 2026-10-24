"""Persistent subscription identity and field-by-field effective settings."""
import hashlib
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit, parse_qs
from oko.subscriptions.history import InitialHistory
from pydantic import BaseModel, ConfigDict, Field, field_validator

class FeedURL(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=4000)


class AddFeed(FeedURL):
    initial_history: InitialHistory = Field(default_factory=InitialHistory)
    verification: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    folder: str = Field(default='Без папки', min_length=1, max_length=200)


class Overrides(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    feed_refresh_minutes: int | None = Field(default=None, ge=1, le=1440)
    telegram_http_rps: float | None = Field(default=None, ge=.1, le=10, allow_inf_nan=False)
    retry_initial_seconds: int | None = Field(default=None, ge=1, le=86400)
    retry_max_seconds: int | None = Field(default=None, ge=1, le=86400)
    originals: bool | None = None
    theses: bool | None = None
    prompt_theses: str | None = Field(default=None, max_length=20000)
    prompt_filters: str | None = Field(default=None, max_length=20000)
    processor: str | None = Field(default=None, min_length=1, max_length=200)


class FeedPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator('title', mode='before')
    @classmethod
    def trim_title(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    model_config = ConfigDict(extra='forbid', strict=True)
    folder_id: str | None = None
    new_folder: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    importance: Literal['normal', 'elevated', 'high'] | None = None
    initial_days: int | None = Field(default=None, ge=1, le=3650)
    public_ai: bool | None = None
    read_history: bool | None = None
    auto_read: bool | None = None
    transport: Literal['telegram', 'rss', 'youtube'] | None = None


DEFAULT_FEED_REFRESH_MINUTES = 15
GLOBAL_SERVICE_SETTINGS = frozenset(('feed_refresh_minutes', 'telegram_http_rps', 'retry_initial_seconds', 'retry_max_seconds'))


DEFAULTS: dict[str, Any] = dict(
    originals=True, theses=False, prompt_theses='Выдели основные тезисы публикации.',
    prompt_filters='', processor='codex')


def key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def identify(url: str) -> tuple[str, str, str]:
    """Only exact recognisable routes merge; ambiguous routes remain unconnected."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Нужен HTTP(S)-адрес без учётных данных')
    host = parsed.hostname.lower()
    route = re.fullmatch(r'/telegram/channel/([A-Za-z][A-Za-z0-9_]{3,31})(?:/([^/]+))?/?', parsed.path)
    if route and route[2]:
        display_option = r'show(?:LinkPreview|ViaBot|ReplyTo|FwdFrom(?:Author)?|InlineButtons|MediaTag(?:InTitle|AsEmoji)|HashtagAsHyperlink)=(?:0|1|true|false)'
        if not re.fullmatch(display_option + r'(?:&' + display_option + r')*', route[2]):
            route = None
    direct = re.fullmatch(r'/([A-Za-z][A-Za-z0-9_]{3,31})/?', parsed.path) if host in ('t.me', 'telegram.me') else None
    if (route or direct) and not parsed.query:
        match = route or direct
        assert match is not None
        return 'telegram:' + match[1].lower(), 'telegram', 'needs_connection'
    if parsed.path.startswith('/telegram/') or host in ('t.me', 'telegram.me'):
        return 'unresolved:' + url, 'unresolved', 'ambiguous'
    channel = parse_qs(parsed.query).get('channel_id', [])
    if host in ('www.youtube.com', 'youtube.com') and parsed.path == '/feeds/videos.xml' and len(channel) == 1:
        return 'youtube:' + channel[0], 'youtube', 'needs_connection'
    canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ''))
    return 'rss:' + canonical, 'rss', 'needs_connection'


class FolderConflict(ValueError):
    def __init__(self, code: Literal['folder_not_empty', 'folder_name_conflict'], message: str) -> None:
        self.code = code
        super().__init__(message)
