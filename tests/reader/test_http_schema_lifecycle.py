"""Shared HTTP schemas must not share execution, payloads or reader state."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time
from datetime import datetime, timezone
import gc
import weakref

import pytest
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, Field, RootModel, computed_field, field_serializer
from pydantic_core import CoreSchema

from tests.support.app import create_app


class Reply(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    value: int


class Compilation:
    def __init__(self):
        self.calls = 0

    def __get_pydantic_core_schema__(self, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        self.calls += 1
        time.sleep(.01)  # Deliberately overlap first builds across threads.
        return handler(source)


def test_response_schema_compiles_once_across_direct_included_and_concurrent_apps(tmp_path):
    counter = Compilation()
    response = Annotated[Reply, counter]
    ready = Barrier(4)

    def build(index):
        app = create_app(tmp_path / str(index), api_only=True, token='test')
        router = APIRouter(route_class=app.router.route_class)
        ready.wait(timeout=10)
        async def reply():
            return {'value': index}
        app.add_api_route('/api/v3/probe-direct', reply, response_model=response)
        router.add_api_route('/probe-included', reply, response_model=response)
        app.include_router(router, prefix='/api/v3')
        return app

    with ThreadPoolExecutor(max_workers=4) as pool:
        apps = list(pool.map(build, range(8)))
    assert len({id(app.state.host) for app in apps}) == 8
    for index, app in enumerate(apps):
        with TestClient(app, headers={'Authorization':'Bearer test'}) as client:
            assert client.get('/api/v3/probe-direct').json() == {'value':index}
            assert client.get('/api/v3/probe-included').json() == {'value':index}
    assert counter.calls == 1


class SchemaRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    title: str = Field(alias='displayTitle')
    enabled: bool = True
    count: int = 0
    note: str | None = None
    created: datetime | None = None

    @field_serializer('note', when_used='json')
    def encode_note(self, value: str | None) -> str | None:
        return value.upper() if value else value

    @computed_field
    @property
    def description(self) -> str:
        return f'{self.title}:{self.count}'


class PrivateRecord(SchemaRecord):
    secret: str


class SchemaRecords(RootModel[list[SchemaRecord]]):
    pass


@pytest.mark.parametrize('model', [list[SchemaRecord], SchemaRecords])
def test_response_validation_filters_serializers_and_openapi_match_fastapi(tmp_path, model):
    actual = create_app(tmp_path, api_only=True, token='test')
    reference = FastAPI()
    options = [{}, {'response_model_by_alias': False}, {'response_model_exclude_none': True},
               {'response_model_exclude_unset': True}, {'response_model_exclude_defaults': True},
               {'response_model_include': {1: {'title', 'count'}}},
               {'response_model_exclude': {0: {'note'}, 1: True}}]
    paths = []
    for app in (actual, reference):
        router = APIRouter(route_class=app.router.route_class)
        async def reply(request: Request):
            return request.app.state.payload
        for index, config in enumerate(options):
            direct, included = '/api/v3/schema-direct/'+str(index), '/schema-included/'+str(index)
            app.add_api_route(direct, reply, response_model=model, **config)
            router.add_api_route(included, reply, response_model=model, **config)
            if app is actual:
                paths.extend([direct, '/api/v3'+included])
        app.include_router(router, prefix='/api/v3')

    valid = [{'displayTitle': 'Первый', 'count': 2, 'note': 'note',
              'created': datetime(2026, 10, 22, tzinfo=timezone.utc)}, {'displayTitle': 'Второй'}]
    with TestClient(actual, headers={'Authorization':'Bearer test'}, raise_server_exceptions=False) as a, TestClient(reference, raise_server_exceptions=False) as b:
        variants = [valid, [SchemaRecord.model_validate(value) for value in valid],
                    [PrivateRecord.model_validate(dict(value, secret='private-marker')) for value in valid]]
        for values in variants:
            actual.state.payload = reference.state.payload = values
            for path in paths:
                left, right = a.get(path), b.get(path)
                assert left.status_code == right.status_code == 200, left.text
                assert left.content == right.content, (path, left.text, right.text)
                assert 'private-marker' not in left.text
        expected_schema, actual_schema = reference.openapi(), actual.openapi()
        for path in paths:
            assert expected_schema['paths'][path] == actual_schema['paths'][path]
        for name, schema in expected_schema['components']['schemas'].items():
            assert actual_schema['components']['schemas'][name] == schema
        for invalid in [{'displayTitle':'x','enabled':'false'}, {'displayTitle':'x','count':True},
                        {'displayTitle':'x','unexpected':'private-marker'}, {'count':1},
                        {'displayTitle':'x','created':'2026-10-22T00:00:00Z'}]:
            actual.state.payload = reference.state.payload = [invalid]
            assert a.get(paths[0]).status_code == b.get(paths[0]).status_code == 500
        actual.state.payload = reference.state.payload = valid
        assert a.get(paths[0]).content == b.get(paths[0]).content


def test_live_readers_keep_tokens_settings_diagnostics_and_shutdown_independent(tmp_path):
    first = create_app(tmp_path/'first', api_only=True, token='first')
    second = create_app(tmp_path/'second', api_only=True, token='second')
    with TestClient(first, headers={'Authorization':'Bearer first'}) as a, TestClient(second, headers={'Authorization':'Bearer second'}) as b:
        original = b.get('/api/v3/settings/oko/global').json()
        assert a.patch('/api/v3/settings/oko/global', json={'prompt_theses':'first only'}).status_code == 200
        assert b.get('/api/v3/settings/oko/global').json() == original
        assert a.post('/api/v3/diagnostics', json={'source':'schema probe','message':'first only'}).status_code == 200
        assert b.get('/api/v3/diagnostics').json()['groups'] == []
        assert b.get('/api/v3/status', headers={'Authorization':'Bearer first'}).status_code == 403
        assert a.post('/api/v3/stop').status_code == 200
        assert a.get('/api/v3/status').json()['runtime_state'] == 'stopped'
        assert b.get('/api/v3/status').json()['runtime_state'] == 'running'
        assert b.patch('/api/v3/settings/oko/global', json={'prompt_theses':'second only'}).status_code == 200


def test_prepared_schemas_do_not_retain_application_or_runtime(tmp_path):
    def build():
        app = create_app(tmp_path, api_only=True, token='test')
        return weakref.ref(app), weakref.ref(app.state.host)
    application, runtime = build()
    # FastAPI itself memoizes endpoint callables (which close over Runtime).
    # Release only that unrelated reflection cache; keep prepared schemas warm.
    from fastapi.dependencies import models as dependency_models
    dependency_models._is_gen_callable_cached.cache_clear()
    dependency_models._is_async_gen_callable_cached.cache_clear()
    dependency_models._is_coroutine_callable_cached.cache_clear()
    gc.collect()
    assert application() is None
    assert runtime() is None
