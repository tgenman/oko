"""Wire model configuration, separate from domain validation and execution."""
from datetime import date, datetime, timezone
import re
from typing import Annotated

from pydantic import AfterValidator, AnyUrl, BaseModel, BeforeValidator, ConfigDict, PlainSerializer, Strict, TypeAdapter
from collections.abc import Callable
from typing import Any, TypeVar

DomainModel = TypeVar('DomainModel', bound=BaseModel)


class WireModel(BaseModel):
    # Primitive fields are generated as Strict* types. Formats such as a JSON
    # date-time must still parse from strings before domain validation.
    model_config = ConfigDict(regex_engine='python-re', allow_inf_nan=False)


def normalize(domain: type[DomainModel]) -> Callable[[Any], dict[str, Any]]:
    """Retain existing semantic validation before constructing an input DTO."""
    def prepare(value: Any) -> dict[str, Any]:
        return domain.model_validate(value).model_dump(mode='json', by_alias=True, exclude_unset=True)
    return prepare


def to_domain(domain: type[DomainModel], wire: WireModel) -> DomainModel:
    return domain.model_validate(wire.model_dump(mode='json', by_alias=True, exclude_unset=True))



def timestamp_text(value: str) -> str:
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})', value):
        raise ValueError('Expected RFC3339 timestamp with timezone')
    datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value


def timestamp_utc(value: str) -> str:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def date_text(value: str) -> str:
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('Expected ISO calendar date')
    date.fromisoformat(value)
    return value


_uri = TypeAdapter(AnyUrl)


def uri_text(value: str) -> str:
    _uri.validate_python(value)
    return value


def boolean_constant(value: object) -> object:
    if type(value) is not bool:
        raise ValueError('Expected JSON boolean')
    return value


def integer_constant(value: object) -> object:
    if type(value) is not int:
        raise ValueError('Expected JSON integer')
    return value


# Formatted JSON values stay strings. These annotations compose inside nullable
# unions and arrays and apply constraints to the wire text, before any domain
# conversion. They are shared generator primitives, not a second API schema.
TimestampValue = Annotated[str, Strict(), AfterValidator(timestamp_text), PlainSerializer(timestamp_utc, return_type=str, when_used='json')]
DateValue = Annotated[str, Strict(), AfterValidator(date_text)]
UriValue = Annotated[str, Strict(), AfterValidator(uri_text)]
