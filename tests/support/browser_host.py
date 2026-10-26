"""Reuse imports, never application state: one thread/event loop per HTTP fixture."""
import json
import secrets
from pathlib import Path
import signal
import socket
import sys
import threading
import time

import uvicorn

from tests.support.browser_server import build_app


class Scenario:
    def __init__(self, data, token, persistent=False, port=0, hostname='127.0.0.1',collection=True,processing_delay=0,database_url_file=None):
        self.data, self.persistent, self.hostname = data, persistent, hostname
        self.database_url_file = database_url_file
        from oko.storage.credentials import private_database_url
        database_url = private_database_url(Path(database_url_file)) if database_url_file else None
        if persistent and not (Path(data) / 'access.json').exists():
            from oko.reader.password_auth import PasswordAccess
            PasswordAccess(Path(data)).set_user('reader', 'synthetic-password')
        self.collection,self.processing_delay=collection,processing_delay
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('127.0.0.1', port))
        self.origin = f'http://{hostname}:{self.socket.getsockname()[1]}'
        self.error = None
        try:
            # Browser aborts can leave an unfinished HTTP request. Bound its
            # drain time before the normal application lifespan shutdown.
            self.server = uvicorn.Server(uvicorn.Config(build_app(Path(data), token, persistent=persistent, public_url=self.origin,collection=collection,processing_delay=processing_delay,database_url=database_url), log_level='error',
                                                       timeout_graceful_shutdown=1))
        except BaseException:
            self.socket.close()
            raise
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                self.close()
                raise RuntimeError(f'HTTP fixture startup failed: {self.error}')
            time.sleep(.001)

    def run(self):
        try:
            # Uvicorn installs no process signal handlers outside the main thread.
            self.server.run(sockets=[self.socket])
        except BaseException as error:
            self.error = error

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=3)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError('HTTP fixture did not shut down')
        if self.error:
            raise RuntimeError('HTTP fixture failed') from self.error


def main():
    scenarios = {}

    def terminate(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    try:
        for line in sys.stdin:
            command = json.loads(line)
            key = command['id']
            try:
                if command['op'] == 'prepare':
                    from tests.support.performance_seed import seed
                    seed(Path(command['data']), command['per_feed'], bool(command['active']))
                    result = {}
                elif command['op'] == 'prepare-publications':
                    from tests.support.storage import Store
                    store=Store(Path(command['data']),lambda _:None)
                    store.db.close()
                    result = {}
                elif command['op'] == 'start':
                    scenario = Scenario(command['data'], command['token'], bool(command.get('persistent')), hostname=command.get('hostname', '127.0.0.1'),collection=bool(command.get('collection',1)),processing_delay=command.get('processing_delay',0),database_url_file=command.get('database_url_file'))
                    scenarios[key] = scenario
                    result = {'origin': scenario.origin}
                elif command['op'] == 'stop':
                    finished = scenarios.pop(command['scenario'])
                    finished.close()
                    from tests.support.storage import release_database
                    release_database(Path(finished.data))
                    result = {}
                elif command['op'] == 'restart':
                    old = scenarios.pop(command['scenario'])
                    port = int(old.origin.rsplit(':', 1)[1])
                    old.close()
                    scenario = Scenario(old.data, secrets.token_urlsafe(24), old.persistent, port, old.hostname,old.collection,old.processing_delay,old.database_url_file)
                    scenarios[command['scenario']] = scenario
                    result = {'origin': scenario.origin}
                else:
                    raise ValueError('Unknown fixture command')
                print(json.dumps({'id': key, **result}), flush=True)
            except Exception as error:
                print(json.dumps({'id': key, 'error': str(error)}), flush=True)
                # A broken lifespan must not contaminate another scenario.
                raise
    finally:
        errors = []
        for scenario in scenarios.values():
            try:
                scenario.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup('HTTP fixture cleanup failed', errors)


if __name__ == '__main__':
    main()
