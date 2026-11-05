"""Immutable processor configuration shared by the domain and adapters."""
import ipaddress
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator
from oko.diagnostics import record_caught


class ProcessorConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    endpoint: str = Field(max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    local_execution: bool = False

    @field_validator('endpoint')
    @classmethod
    def endpoint_valid(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Укажите http(s) адрес сервера без пути и авторизации')
        try:
            parsed.port
        except ValueError:
            record_caught('llamacpp.endpoint_valid')
            raise ValueError('Неверный порт') from None
        return value.rstrip('/')


def loopback(endpoint: str) -> bool:
    host = urlsplit(endpoint).hostname
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        return False
