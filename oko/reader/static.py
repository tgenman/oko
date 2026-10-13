"""Deliver the checkout's frontend through the reader's existing HTTP origin."""
import mimetypes
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope
from fastapi.responses import FileResponse


FRONTEND_ROOT = Path(__file__).resolve().parents[2] / 'frontend'


class FrontendRoute(APIRoute):
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        # Data routes added by a caller after assembly must remain reachable.
        path = scope.get('path', '')
        if path == '/api' or path.startswith('/api/'):
            return Match.NONE, {}
        return super().matches(scope)


def install_frontend(app: FastAPI) -> None:
    """Register after data routes; unknown data paths never become static files."""
    root = FRONTEND_ROOT.resolve()
    router = APIRouter(route_class=FrontendRoute)

    @router.get('/')
    @router.get('/{asset:path}')
    async def frontend(asset: str = '') -> FileResponse:
        relative = Path(asset or 'index.html')
        if relative.is_absolute() or any(part.startswith('.') for part in relative.parts) or relative.parts[0] == 'api':
            raise HTTPException(404, 'Файл недоступен')
        try:
            file = (root / relative).resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(404, 'Файл недоступен') from None
        if not file.is_relative_to(root) or not file.is_file():
            raise HTTPException(404, 'Файл недоступен')
        mime = 'text/javascript' if file.suffix in ('.js', '.mjs') else mimetypes.guess_type(file.name)[0]
        return FileResponse(file, media_type=mime)

    app.include_router(router)
