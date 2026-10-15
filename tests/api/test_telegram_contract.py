"""Canonical v3 and generated DTO agreement; legacy HTTP adoption is #5/#21."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from oko.reader.generated.models import TelegramConnectRequest

SPEC = json.loads((Path(__file__).resolve().parents[2] / 'oko/reader/generated/openapi.json').read_text())


@pytest.mark.parametrize('body', [
    {'source_locator': '@fixture', 'folder': 'Inbox'},
    {'source_locator': '@fixture', 'folder': 'Inbox', 'access': {'kind': 'web'}},
    {'source_locator': '@fixture', 'folder': 'Inbox', 'access': {'kind': 'client', 'profile': 'personal'}, 'auto_read': False},
    {'source_locator': '@fixture', 'folder': 'Inbox', 'access': {'kind': 'client', 'profile': 'collection'}, 'auto_read': True},
])
def test_web_default_and_two_explicit_client_profiles_agree_with_generated_dto(body):
    schema = SPEC['components']['schemas']['TelegramConnectRequest']
    Draft202012Validator(dict(schema, components=SPEC['components'])).validate(body)
    dto = TelegramConnectRequest.model_validate(body)
    assert dto.model_dump(mode='json')['initial_history'] == {'count': '20', 'mode': 'count'}


@pytest.mark.parametrize('extra', [
    {'auto_read': False}, {'account': 'fixture'}, {'topic_id': 20},
    {'access': {'kind': 'client'}},
    {'access': {'kind': 'client', 'profile': 'other'}},
    {'access': {'kind': 'web', 'profile': 'personal'}},
])
def test_web_does_not_accept_client_settings_and_client_requires_role(extra):
    body = dict(source_locator='@fixture', folder='Inbox', **extra)
    schema = SPEC['components']['schemas']['TelegramConnectRequest']
    assert not Draft202012Validator(dict(schema, components=SPEC['components'])).is_valid(body)
    with pytest.raises(ValidationError):
        TelegramConnectRequest.model_validate(body)
