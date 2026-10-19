"""Private, bounded protocol shared by the host service and container adapter."""
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from oko.processing.execution import CodexChoice

VERSION = 1
LEASE_SECONDS = 20.0
RENEW_SECONDS = 5.0
MAX_REQUEST_BYTES = 1_048_576
MAX_RESULT_BYTES = 1_048_576
JOB_SECONDS = 180.0
TOMBSTONE_SECONDS = 600.0
MAX_RECORDS = 4096


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: Literal['127.0.0.1'] = '127.0.0.1'
    port: int = Field(default=8767, ge=1, le=65535)
    token_file: Path
    scratch_root: Path
    release: str = Field(min_length=1, max_length=200)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    boot_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    operation: Literal['check', 'catalog', 'complete']
    prompt: str = Field(default='', max_length=MAX_REQUEST_BYTES)
    schema_: dict[str, Any] = Field(default_factory=dict, alias='schema')
    choice: CodexChoice | None = None
    deadline: float

    @field_validator('schema_')
    @classmethod
    def valid_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        from jsonschema import Draft202012Validator, SchemaError  # type: ignore[import-untyped]
        # Validate nesting before the standards validator, which is recursive.
        nodes = 0
        def walk(node: Any, depth: int = 0) -> None:
            nonlocal nodes
            nodes += 1
            if depth > 48 or nodes > 65536:
                raise ValueError('schema structure exceeded limit')
            if isinstance(node, dict):
                for key, child in node.items():
                    if key in ('$ref', '$dynamicRef') and (not isinstance(child, str) or not child.startswith('#')):
                        raise ValueError('external schema references forbidden')
                    walk(child, depth + 1)
            elif isinstance(node, list):
                for child in node:
                    walk(child, depth + 1)
        walk(value)
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError:
            raise ValueError('invalid JSON Schema') from None
        return value
