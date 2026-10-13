"""Caption outcomes and explicit proxy configuration; no request scheduling."""
import math
from typing import Any
from urllib.parse import quote, urlsplit

from oko.collection.contracts import SourceError


class CaptionsBlocked(SourceError):
    def __init__(self, retry_after: float = 0) -> None:
        super().__init__('YouTube ограничил получение субтитров')
        self.retry_after = retry_after if math.isfinite(retry_after) and retry_after > 0 else 0


class CaptionsDeferred(Exception):
    """Admission was postponed; no external attempt occurred."""
    diagnostic_skip = True


def proxy_settings(body: Any, previous: dict[str, str]) -> dict[str, str]:
    """Validate without echoing user input (including credentials) in errors."""
    invalid = 'Некорректные настройки прокси: нужен HTTP/HTTPS-адрес без учётных данных, пути и параметров'
    if not isinstance(body, dict) or set(body) - {'mode', 'address', 'username', 'password', 'clear_credentials'}:
        raise ValueError(invalid)
    if any(not isinstance(v, str) or len(v) > 4096 or any(ord(c) < 32 for c in v)
           for k, v in body.items() if k != 'clear_credentials'):
        raise ValueError(invalid)
    if 'clear_credentials' in body and not isinstance(body['clear_credentials'], bool):
        raise ValueError(invalid)
    config = {**previous, **{k: v for k, v in body.items() if k != 'clear_credentials'}}
    if config['mode'] not in ('direct', 'proxy'):
        raise ValueError(invalid)
    address = config['address'].strip()
    try:
        url = urlsplit(address)
        valid = (url.scheme in ('http', 'https') and bool(url.hostname) and url.username is None
                 and url.password is None and url.path in ('', '/') and not url.query and not url.fragment
                 and not any(c.isspace() for c in address) and (url.port is None or url.port > 0))
    except ValueError:
        valid = False
    if address and not valid or config['mode'] == 'proxy' and not address:
        raise ValueError(invalid)
    config['address'] = address.rstrip('/')
    if body.get('clear_credentials'):
        config['username'] = config['password'] = ''
    return config


def proxy_url(config: dict[str, str]) -> str | None:
    if config['mode'] != 'proxy':
        return None
    url = urlsplit(config['address'])
    auth = quote(config['username'], safe='') + ':' + quote(config['password'], safe='') + '@' if config['username'] or config['password'] else ''
    return f'{url.scheme}://{auth}{url.netloc}'
