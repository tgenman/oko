"""Generated processor contracts backed by the existing Runtime owners."""
from oko.reader.http_schemas import SchemaRoute

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import ValidationError

from oko.processing.execution import CodexSettings
from oko.processing.llamacpp import ProcessorConfig
from oko.reader.generated import models as wire
from oko.reader import snapshots as facts
from oko.reader import projection_v3 as project
from oko.reader.runtime import Runtime, ActionError


def register(app: FastAPI, runtime: Runtime) -> None:
    from oko.reader.api_v3 import no_query
    router = APIRouter(route_class=SchemaRoute, dependencies=[Depends(no_query)])
    from oko.reader.api_v3 import ApiError, invalid
    from oko.reader.commands_v3 import track

    @router.get('/api/v3/processors/codex', response_model=wire.CodexProcessorState)
    async def state() -> wire.CodexProcessorState:
        return project.codex(facts.CodexState.model_validate(runtime.codex_status()))

    @router.get('/api/v3/processors/codex/models', response_model=wire.CodexModelCatalog)
    async def models() -> wire.CodexModelCatalog:
        try:
            result = await runtime.codex_models()
        except ActionError as error:
            if runtime.stopped or error.code == 'runtime_disabled':
                raise
            raise ApiError(503, wire.ErrorEnvelope(wire.ProcessorUnavailableError(code='processor_unavailable', message=str(error.detail), details=wire.ProcessorUnavailableErrorDetails(retry_at=None), diagnostic_id=None, command_id=None))) from None
        return wire.CodexModelCatalog.model_validate({'models': result})

    @router.put('/api/v3/processors/codex', response_model=wire.CodexProcessorState)
    async def save(request: Request, body: wire.CodexConfigurationRequest) -> wire.CodexProcessorState:
        async with track(runtime, request, wire.CommandContext(wire.CommandCodexSave(operation='codex_save'))):
            value = CodexSettings.model_validate(body.model_dump())
            await runtime.configure_codex(value)
            return project.codex(facts.CodexState.model_validate(runtime.codex_status()))

    @router.post('/api/v3/processors/codex/check', response_model=wire.CodexAvailability)
    async def check(request: Request) -> wire.CodexAvailability:
        async with track(runtime, request, wire.CommandContext(wire.CommandCodexCheck(operation='codex_check'))):
            await runtime.check_codex()
            return project.codex(facts.CodexState.model_validate(runtime.codex_status())).availability

    @router.get('/api/v3/processors/llamacpp', response_model=wire.LocalProcessorState)
    async def local_state() -> wire.LocalProcessorState:
        config = runtime.require_store().processor_config()
        return wire.LocalProcessorState(configuration=wire.LocalProcessorConfiguration.model_validate(config.model_dump()) if config else None)

    @router.put('/api/v3/processors/llamacpp', response_model=wire.LocalProcessorState)
    async def local_save(request: Request, body: wire.LocalProcessorConfigurationRequest) -> wire.LocalProcessorState:
        async with track(runtime, request, wire.CommandContext(wire.CommandProcessorSave(operation='processor_save'))):
            try:
                config = ProcessorConfig.model_validate(body.model_dump())
            except ValidationError:
                raise ApiError(422, invalid('body', 'endpoint')) from None
            await runtime.configure_processor(config)
            return wire.LocalProcessorState(configuration=wire.LocalProcessorConfiguration.model_validate(config.model_dump()))

    @router.post('/api/v3/processors/llamacpp/check', response_model=wire.LocalProcessorCheck)
    async def local_check(request: Request) -> wire.LocalProcessorCheck:
        async with track(runtime, request, wire.CommandContext(wire.CommandProcessorCheck(operation='processor_check'))):
            value = facts.ProcessorCheck.model_validate(await runtime.check_processor())
            return wire.LocalProcessorCheck(locality='local' if value.local else 'remote', model=value.model, models=value.models)

    app.include_router(router)
