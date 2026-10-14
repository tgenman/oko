"""Deliver the checkout's frontend through the reader's existing HTTP origin."""
import mimetypes
import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope
from fastapi.responses import FileResponse
from oko.reader.generated.revision import CONTRACT_DIGEST


FRONTEND_ROOT = Path(__file__).resolve().parents[1] / 'frontend_build'


def validate_frontend() -> Path:
    """Check the packaged generation, plus freshness when running a checkout."""
    try:
        manifest = json.loads((FRONTEND_ROOT / 'current.json').read_text())
        canonical = Path(__file__).parent / 'generated/openapi.json'
        if manifest.get('contract') != CONTRACT_DIGEST or hashlib.sha256(canonical.read_bytes()).hexdigest() != CONTRACT_DIGEST:
            raise ValueError('mixed contract revisions')
        generation = str(manifest['generation'])
        if not re.fullmatch(r'[a-f0-9]{64}', generation):
            raise ValueError('invalid generation')
        root = FRONTEND_ROOT / 'generations' / generation
        assets = manifest['assets']
        if 'index.html' not in assets:
            raise ValueError('index missing')
        for name, digest in assets.items():
            file = (root / name).resolve()
            if not file.is_relative_to(root.resolve()) or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                raise ValueError('asset integrity failed')
        checkout = Path(__file__).resolve().parents[2]
        if (checkout / 'frontend/index.html').is_file() and (checkout / 'package.json').is_file():
            source = hashlib.sha256()
            for name in ('frontend', 'api/openapi.yaml', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.mjs', 'scripts/build-frontend.mjs'):
                path = checkout / name
                for file in sorted((p for p in path.rglob('*') if p.is_file()), key=lambda p: p.relative_to(checkout).as_posix()) if path.is_dir() else [path]:
                    source.update(file.relative_to(checkout).as_posix().encode())
                    source.update(b'\0')
                    source.update(file.read_bytes())
                    source.update(b'\0')
            if source.hexdigest() != manifest['source']:
                raise ValueError('stale build')
        return root
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('Frontend build missing, stale or damaged. Run npm run build before starting Oko.') from error


class FrontendRoute(APIRoute):
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        # Data routes added by a caller after assembly must remain reachable.
        path = scope.get('path', '')
        if path == '/api' or path.startswith('/api/'):
            return Match.NONE, {}
        return super().matches(scope)


def install_frontend(app: FastAPI) -> None:
    """Register after data routes; unknown data paths never become static files."""
    router = APIRouter(route_class=FrontendRoute)

    @router.get('/')
    @router.get('/{asset:path}')
    async def frontend(asset: str = '') -> FileResponse:
        # The pointer changes atomically; old hashed assets remain available to
        # tabs opened before a watch rebuild.
        try:
            manifest = json.loads((FRONTEND_ROOT / 'current.json').read_text())
            root = (FRONTEND_ROOT / 'generations' / manifest['generation']).resolve()
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, 'Сборка интерфейса недоступна') from None
        relative = Path(asset or 'index.html')
        if not relative.parts or relative.is_absolute() or any(part.startswith('.') for part in relative.parts) or relative.parts[0] == 'api':
            raise HTTPException(404, 'Файл недоступен')
        try:
            file = (root / relative).resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(404, 'Файл недоступен') from None
        if not file.is_relative_to(root) or not file.is_file():
            if relative.parts[0] == 'assets':
                for previous in (FRONTEND_ROOT / 'generations').iterdir():
                    candidate = (previous / relative).resolve()
                    if candidate.is_relative_to(previous.resolve()) and candidate.is_file():
                        file = candidate
                        break
                else:
                    raise HTTPException(404, 'Файл недоступен')
            else:
                raise HTTPException(404, 'Файл недоступен')
        mime = 'text/javascript' if file.suffix in ('.js', '.mjs') else mimetypes.guess_type(file.name)[0]
        return FileResponse(file, media_type=mime)

    app.include_router(router)
