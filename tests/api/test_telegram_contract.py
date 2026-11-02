"""Canonical v3 and generated DTO agreement; legacy HTTP adoption is #5/#21."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from oko.reader.generated.models import TelegramConnectRequest

SPEC = json.loads((Path(__file__).resolve().parents[2] / 'oko/reader/generated/openapi.json').read_text())


@pytest.mark.parametrize('body', [
    {'source_locator': '@fixture', 'folder': 'Inbox'},
    {'source_locator': '@fixture', 'folder': 'Inbox', 'access': {'kind': 'web'}},
])
def test_web_default_and_explicit_access_agree_with_generated_dto(body):
    schema = SPEC['components']['schemas']['TelegramConnectRequest']
    Draft202012Validator(dict(schema, components=SPEC['components'])).validate(body)
    dto = TelegramConnectRequest.model_validate(body)
    assert dto.model_dump(mode='json')['initial_history'] == {'count': '20', 'mode': 'count'}
