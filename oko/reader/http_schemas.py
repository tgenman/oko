"""Compile response contracts once; bind execution and serialization options per app."""
from dataclasses import dataclass
from functools import lru_cache
from threading import RLock
from typing import Annotated, Any, cast, get_args, get_origin

from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler, TypeAdapter
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema
from pydantic_core.core_schema import SerializationInfo, ValidationInfo


@dataclass(frozen=True)
class _ResponseSchema:
    adapter: TypeAdapter[Any]

    def validate(self, value: Any, info: ValidationInfo) -> Any:
        return self.adapter.validate_python(value, from_attributes=True, context=info.context)

    def serialize(self, value: Any, info: SerializationInfo) -> Any:
        # pydantic-core's recursive filter annotations are wider than TypeAdapter's.
        return self.adapter.dump_python(value, mode='json' if info.mode_is_json() else 'python', include=cast(Any, info.include),
            exclude=cast(Any, info.exclude), by_alias=info.by_alias, exclude_unset=info.exclude_unset,
            exclude_defaults=info.exclude_defaults, exclude_none=info.exclude_none,
            round_trip=info.round_trip, context=info.context, serialize_as_any=info.serialize_as_any)

    def __get_pydantic_core_schema__(self, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        # The outer FastAPI field stays small. The complete contract, including
        # strict validators and serializers, belongs to the prepared adapter.
        return core_schema.with_info_plain_validator_function(self.validate,
            serialization=core_schema.plain_serializer_function_ser_schema(self.serialize, info_arg=True))

    def __get_pydantic_json_schema__(self, schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        schema = self.adapter.core_schema
        if schema['type'] == 'model' and 'ref' in schema:
            return handler(core_schema.definitions_schema(
                core_schema.definition_reference_schema(schema['ref']), [schema]))
        return handler(schema)


_lock = RLock()


@lru_cache(maxsize=256)
def _prepared(model: Any) -> Any:
    return Annotated[Any, _ResponseSchema(TypeAdapter(model))]


def response_schema(model: Any) -> Any:
    if get_origin(model) is Annotated and any(isinstance(value, _ResponseSchema) for value in get_args(model)[1:]):
        return model
    try:
        hash(model)
    except TypeError:
        return model  # Preserve FastAPI behavior for arbitrary unhashable metadata.
    # lru_cache alone permits duplicate first builds in concurrent app factories.
    with _lock:
        return _prepared(model)


class SchemaRoute(APIRoute):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        model = kwargs.get('response_model')
        if model is not None and not isinstance(model, DefaultPlaceholder):
            kwargs['response_model'] = response_schema(model)
        super().__init__(*args, **kwargs)
