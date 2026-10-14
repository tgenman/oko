"""Wire model configuration, separate from domain validation and execution."""
from pydantic import BaseModel, ConfigDict
from collections.abc import Callable
from typing import Any, TypeVar

DomainModel = TypeVar('DomainModel', bound=BaseModel)


class WireModel(BaseModel):
    # Primitive fields are generated as Strict* types. Formats such as a JSON
    # date-time must still parse from strings before domain validation.
    model_config = ConfigDict(regex_engine='python-re')


def normalize(domain: type[DomainModel]) -> Callable[[Any], dict[str, Any]]:
    """Retain existing semantic validation before constructing an input DTO."""
    def prepare(value: Any) -> dict[str, Any]:
        return domain.model_validate(value).model_dump(mode='json', by_alias=True, exclude_unset=True)
    return prepare


def to_domain(domain: type[DomainModel], wire: WireModel) -> DomainModel:
    return domain.model_validate(wire.model_dump(mode='json', by_alias=True, exclude_unset=True))
