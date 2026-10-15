#!/usr/bin/env python3
"""Build and install an unreviewed wheel inside its candidate evidence directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def serve(installed, fixture, data, ready, api_only):
    """Private child process: only Oko from the installed wheel is importable."""
    import importlib.util
    import socket
    import uvicorn
    sys.path.insert(0, str(installed))
    import oko
    if not Path(oko.__file__).resolve().is_relative_to(installed):
        raise ValueError('Oko was not imported from the installed wheel')
    spec = importlib.util.spec_from_file_location('synthetic_app', fixture)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.create_app(data, token='candidate-smoke', api_only=api_only,
                            shutdown=lambda: setattr(server, 'should_exit', True))
    for name, imported in tuple(sys.modules.items()):
        filename = getattr(imported, '__file__', None)
        if (name == 'oko' or name.startswith('oko.')) and filename and not Path(filename).resolve().is_relative_to(installed):
            raise ValueError('Oko module escaped installed wheel: ' + name)
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        ready.write_text(json.dumps({'origin': 'http://127.0.0.1:' + str(listener.getsockname()[1])}))
        server.run(sockets=[listener])


def installed_smoke(installed, fixture):
    import shutil
    import time
    from urllib.parse import quote
    import httpx
    from jsonschema import Draft202012Validator, FormatChecker
    installed = installed.resolve()
    document = json.loads((installed / 'oko/reader/generated/openapi.json').read_text())
    marker = json.loads((installed / 'oko/candidate.json').read_text())
    if marker.get('status') != 'UNREVIEWED' or marker.get('publication_allowed') is not False:
        raise ValueError('Installed candidate is missing its unreviewed publication marker')
    operations = {}
    for path, methods in document['paths'].items():
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            identity = operation['operationId']
            if identity in operations:
                raise ValueError('Ambiguous installed operation: ' + identity)
            operations[identity] = (method.upper(), path, operation)
    for identity in ('status', 'stop'):
        if identity not in operations:
            raise ValueError('Selected contract lacks required operation: ' + identity)
    major = int(document['info']['version'].split('.')[0])
    sessions = ('create_client_session', 'renew_client_session', 'close_client_session')
    if major >= 3:
        if not all(identity in operations for identity in sessions):
            raise ValueError('Selected v3 contract lacks client session operations')
        for identity in ('status', 'stop', *sessions):
            if not operations[identity][1].startswith(f'/api/v{major}/'):
                raise ValueError('Selected v3 operation uses unsupported API path: ' + identity + ' ' + operations[identity][1])
    evidence = {'status': 'UNREVIEWED', 'contract_version': document['info']['version']}
    with tempfile.TemporaryDirectory(prefix='installed-smoke-') as temporary:
        root = Path(temporary)
        private_fixture = root / 'synthetic_app.py'
        shutil.copy2(fixture, private_fixture)
        bare = root / 'api-only-installed'
        shutil.copytree(installed, bare)
        shutil.rmtree(bare / 'oko/frontend_build', ignore_errors=True)
        for mode, library in (('api_only', bare), ('client_presence', installed)):
            ready = root / (mode + '-ready.json')
            log_path = root / (mode + '.log')
            environment = {**os.environ, 'PATH': ''}
            environment.pop('PYTHONPATH', None)
            command = [sys.executable, '-I', str(Path(__file__).resolve()), '--serve-installed', str(library),
                       '--fixture', str(private_fixture), '--data', str(root / (mode + '-data')), '--ready', str(ready)]
            if mode == 'api_only':
                command.append('--api-only')
            with log_path.open('w') as output:
                process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise ValueError('Installed process failed readiness: ' + log_path.read_text())
                    time.sleep(.02)
                origin = json.loads(ready.read_text())['origin']
                with httpx.Client(base_url=origin, headers={'Authorization': 'Bearer candidate-smoke'}, timeout=2, trust_env=False) as client:
                    while True:
                        try:
                            client.get(operations['status'][1])
                            break
                        except httpx.TransportError:
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise ValueError('Installed HTTP server did not start: ' + log_path.read_text())
                            time.sleep(.02)
                    def call(identity, parameters=None):
                        method, path, operation = operations[identity]
                        for key, item in (parameters or {}).items():
                            path = path.replace('{' + key + '}', quote(item, safe=''))
                        response = client.request(method, path)
                        success = {str(code) for code in operation['responses'] if str(code).startswith('2')}
                        if str(response.status_code) not in success:
                            raise ValueError(f'Installed {method} {path} returned {response.status_code}: {response.text}')
                        schema = operation['responses'][str(response.status_code)].get('content', {}).get('application/json', {}).get('schema')
                        body = response.json()
                        if schema:
                            Draft202012Validator(dict(schema, components=document.get('components', {})), format_checker=FormatChecker()).validate(body)
                        return body
                    call('status')
                    if mode == 'api_only' and client.get('/').status_code != 404:
                        raise ValueError('API-only installed process exposed a frontend route')
                    checked = 'not_in_selected_contract'
                    if mode == 'client_presence':
                        if all(identity in operations for identity in sessions):
                            session = call('create_client_session')
                            call('renew_client_session', {'identity': session['id']})
                            call('close_client_session', {'identity': session['id']})
                            checked = 'create_renew_close'
                        elif major < 3 and all(identity in operations for identity in ('heartbeat', 'close_tab')):
                            call('heartbeat', {'tab': 'installed-smoke'})
                            call('close_tab', {'tab': 'installed-smoke'})
                            checked = 'legacy_contract_presence'
                    call('stop')
                if process.wait(timeout=5) != 0:
                    raise ValueError('Installed process did not stop cleanly: ' + log_path.read_text())
                evidence[mode] = {'http_status': 200, 'process_exit_code': process.returncode,
                                  'frontend_absent': not (library / 'oko/frontend_build').exists(), 'sessions': checked}
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
    print(json.dumps(evidence))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-installed', type=Path)
    parser.add_argument('--serve-installed', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--data', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--ready', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--api-only', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_installed:
        return serve(args.serve_installed.resolve(), args.fixture, args.data, args.ready, args.api_only)
    if args.smoke_installed:
        return installed_smoke(args.smoke_installed, args.fixture)
    from candidate import context
    value = context(ROOT)
    if value is None:
        raise ValueError('Candidate install smoke requires a candidate context')
    output = ROOT.parent / 'unreviewed-wheels'
    subprocess.run([sys.executable, str(ROOT / 'scripts/build-package.py'), '--output', str(output)], cwd=ROOT, check=True)
    wheels = list(output.glob('*.whl'))
    if len(wheels) != 1:
        raise ValueError('Expected exactly one candidate wheel')
    with tempfile.TemporaryDirectory(prefix='install-', dir=ROOT.parent) as temporary:
        installed = Path(temporary) / 'installed'
        subprocess.run([sys.executable, str(ROOT / 'tests/support/install_wheel.py'), str(wheels[0]), str(installed)], cwd=temporary, check=True)
        installed_smoke(installed, ROOT / 'tests/support/app.py')
    print(json.dumps({'status': 'UNREVIEWED', 'wheel': str(wheels[0]), 'publication_allowed': False}))


if __name__ == '__main__':
    main()
