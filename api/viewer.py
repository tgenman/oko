"""Read-only contract viewer: standard-library HTTP, no reader imports/state."""
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any

CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
HTML = b'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Oko API contract</title><link rel="stylesheet" href="/docs/swagger-ui.css"></head><body><div id="swagger-ui"></div><script src="/docs/swagger-ui-bundle.js"></script><script src="/docs/initializer.js"></script></body></html>'''
INITIALIZER = b'''"use strict";
window.addEventListener("load", () => {
  SwaggerUIBundle({url: "/openapi.json", dom_id: "#swagger-ui", deepLinking: true,
    supportedSubmitMethods: [], tryItOutEnabled: false, persistAuthorization: false,
    validatorUrl: null, defaultModelsExpandDepth: 0,
    plugins: [() => ({wrapComponents: {authorizeBtn: () => () => null}})]});
});
'''


class Viewer(BaseHTTPRequestHandler):
    def __init__(self, *args: Any, document: bytes, assets: Path, **kwargs: Any) -> None:
        self.document = document
        self.assets = assets
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            self.send_error(403)
            return
        resources = {'/': (HTML, 'text/html; charset=utf-8'), '/docs': (HTML, 'text/html; charset=utf-8'),
                     '/openapi.json': (self.document, 'application/json'),
                     '/docs/initializer.js': (INITIALIZER, 'application/javascript')}
        for name, mime in [('swagger-ui.css','text/css'), ('swagger-ui-bundle.js','application/javascript')]:
            resources['/docs/' + name] = ((self.assets / name).read_bytes(), mime)
        resource = resources.get(self.path.split('?')[0])
        if resource is None:
            self.send_error(404)
            return
        data, mime = resource
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def serve(document: dict[str, Any], assets: Path, port: int) -> None:
    for name in ['swagger-ui.css', 'swagger-ui-bundle.js']:
        if not (assets / name).is_file():
            raise FileNotFoundError(f'Swagger assets missing: {assets / name}. Install locked npm dependencies.')
    handler = partial(Viewer, document=json.dumps(document, ensure_ascii=False).encode(), assets=assets)
    with ThreadingHTTPServer(('127.0.0.1', port), handler) as server:
        print(f'OpenAPI viewer: http://127.0.0.1:{server.server_port}/docs', flush=True)
        server.serve_forever()
