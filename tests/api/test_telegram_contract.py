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
])
def test_web_default_and_explicit_access_agree_with_generated_dto(body):
    schema = SPEC['components']['schemas']['TelegramConnectRequest']
    Draft202012Validator(dict(schema, components=SPEC['components'])).validate(body)
    dto = TelegramConnectRequest.model_validate(body)
    assert dto.model_dump(mode='json')['initial_history'] == {'count': '20', 'mode': 'count'}


@pytest.mark.parametrize('extra', [
    {'auto_read': False}, {'account': 'fixture'}, {'topic_id': 20},
    {'access': {'kind': 'client', 'profile': 'personal'}},
    {'access': {'kind': 'client', 'profile': 'collection'}},
    {'access': {'kind': 'client'}},
    {'access': {'kind': 'client', 'profile': 'other'}},
    {'access': {'kind': 'web', 'profile': 'personal'}},
])
def test_web_does_not_accept_client_settings(extra):
    body = dict(source_locator='@fixture', folder='Inbox', **extra)
    schema = SPEC['components']['schemas']['TelegramConnectRequest']
    assert not Draft202012Validator(dict(schema, components=SPEC['components'])).is_valid(body)
    with pytest.raises(ValidationError):
        TelegramConnectRequest.model_validate(body)


def test_telegram_connect_contract_has_no_account_errors():
    schemas = SPEC['components']['schemas']
    for name in ('TelegramConnect409Errors', 'TelegramConnect503Errors'):
        references = [part['$ref'] for part in schemas[name]['oneOf']]
        assert all('Profile' not in ref and 'AccountIdentity' not in ref for ref in references)
