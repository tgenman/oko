"""Embedded API documentation: no reader lease or implicit authentication."""
from pathlib import Path
from collections.abc import Awaitable, Callable
from fastapi import FastAPI
from fastapi.responses import FileResponse
from oko.reader.static import validate_frontend


def install_docs(app: FastAPI) -> None:
    @app.get('/openapi.json', include_in_schema=False)
    async def canonical_contract() -> FileResponse:
        return FileResponse(Path(__file__).parent / 'generated/openapi.json', media_type='application/json')

    @app.get('/docs', include_in_schema=False)
    async def swagger() -> FileResponse:
        return FileResponse(validate_frontend() / 'docs/index.html', media_type='text/html')

    for name, mime in [('initializer.js','text/javascript'), ('swagger-ui-bundle.js','text/javascript'), ('swagger-ui.css','text/css')]:
        def route(asset: str, content_type: str) -> Callable[[], Awaitable[FileResponse]]:
            async def resource() -> FileResponse:
                return FileResponse(validate_frontend() / 'docs' / asset, media_type=content_type)
            return resource
        app.add_api_route('/docs/' + name, route(name, mime), methods=['GET'], include_in_schema=False)
