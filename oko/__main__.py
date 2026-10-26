import argparse
import asyncio
from collections.abc import Callable
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import socket
import ssl
from urllib.parse import urlsplit
import webbrowser

import uvicorn
from oko.reader.app import create_app
from oko.collection.demo import DemoSource
from oko.collection.contracts import Source
from oko.collection.telegram.source import LocalTelegram


def existing(root: Path, browser: bool, *, api_only: bool = False, persistent: bool = False) -> None:
    import time
    for _ in range(100):
        try:
            data = json.loads((root / 'runtime.json').read_text())
            from oko.reader.control import status as control_status
            status = control_status(data, timeout=.2)
            if status:
                expected = 'explicit' if api_only or persistent else 'client_presence'
                actual = status['lifetime']['kind']
                if actual != expected:
                    raise SystemExit(f'Запущенный экземпляр использует {actual}; запрошен {expected}. Завершите его явно перед сменой профиля.')
                bundled = json.loads((Path(__file__).parent / 'reader/generated/openapi.json').read_text())
                version = status['api_version']
                if version.split('.')[0] != bundled['info']['version'].split('.')[0]:
                    raise SystemExit(f'Запущенный экземпляр использует API {version}; требуется {bundled["info"]["version"]}. Завершите его перед обновлением.')
                if browser:
                    webbrowser.open(data['url'] + ('/' if persistent else '/#token=' + data['token']))
                print(f'Открыт существующий экземпляр Око ({actual}, API {version}).')
                return
        except (OSError, ValueError):
            time.sleep(.05)
    raise SystemExit('Экземпляр Око занят или останавливается. Повторите запуск через несколько секунд.')


def run(*, fault: Callable[[str], None] = lambda _: None, source: Source | None = None) -> None:
    parser = argparse.ArgumentParser(description='Око — локальный читатель')
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--database-url-file', type=Path, required=True, help='Закрытый файл подключения PostgreSQL; схема устанавливается отдельной командой миграции')
    parser.add_argument('--telegram-env', type=Path, help='Путь к существующей .env с авторизацией Telegram')
    parser.add_argument('--telegram-project', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--demo', action='store_true', help='Подключить синтетический тестовый источник')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--api-only', action='store_true', help='API без веб-интерфейса, работа до явной остановки')
    parser.add_argument('--persistent', action='store_true', help='Постоянный веб-экземпляр с запоминаемым входом')
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--public-url', help='Внешний локальный origin читателя, например http://oko')
    parser.add_argument('--tls-cert', type=Path, help='PEM цепочка сертификатов для HTTPS в Uvicorn')
    parser.add_argument('--tls-key', type=Path, help='Закрытый PEM ключ для HTTPS в Uvicorn')
    parser.add_argument('--metrics-token-file', type=Path, help='Закрытый ключ только для чтения метрик Prometheus')
    parser.add_argument('--codex-url', help='Адрес установленного процесса Codex на Mac')
    parser.add_argument('--codex-socket', type=Path, help='Приватный Unix socket host-helper (Linux)')
    parser.add_argument('--codex-token-file', type=Path)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--lease', type=float, default=6, help='Срок клиентской сессии, секунд')
    parser.add_argument('--grace', type=float, default=15, help='Ожидание переподключения, секунд')
    args = parser.parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error('--tls-cert and --tls-key must be supplied together')
    if args.tls_cert:
        if not args.public_url or urlsplit(args.public_url).scheme != 'https':
            parser.error('Native TLS requires an HTTPS --public-url')
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(args.tls_cert, args.tls_key)
        except (OSError, ssl.SSLError):
            parser.error('TLS certificate and key could not be loaded')
    if args.codex_socket and args.codex_url:
        parser.error('--codex-url and --codex-socket are separate transports')
    codex_url = 'http://localhost' if args.codex_socket else args.codex_url
    if bool(codex_url) != bool(args.codex_token_file):
        parser.error('Codex transport and --codex-token-file must be supplied together')
    if args.persistent and args.api_only:
        parser.error('--persistent and --api-only are separate profiles')
    if not all(math.isfinite(value) and value > 0 for value in (args.lease, args.grace)):
        parser.error('Таймауты должны быть положительными')
    from oko.storage.credentials import private_database_url
    from oko.observability import private_metrics_token
    metrics_token = private_metrics_token(args.metrics_token_file) if args.metrics_token_file else None
    database_url = private_database_url(args.database_url_file) if args.database_url_file else None
    if database_url is not None:
        from oko.storage.readiness import wait_database
        wait_database(database_url)
    telegram_env = args.telegram_env or (args.telegram_project / '.env' if args.telegram_project else None)
    root = args.data.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    with (root / 'instance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            existing(root, not (args.no_browser or args.api_only), api_only=args.api_only, persistent=args.persistent)
            return
        codex_client = None
        if codex_url:
            from oko.processing.codex_bridge.client import RemoteCodex
            codex_client = RemoteCodex(codex_url, args.codex_token_file.read_text().strip(), socket_path=args.codex_socket)
        token = secrets.token_urlsafe(32)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((args.host, args.port))
            sock.listen(128)
            port = sock.getsockname()[1]
            control_url = f'http://127.0.0.1:{port}'
            if args.tls_cert:
                control_url = f'https://{urlsplit(args.public_url).hostname}:{port}'
            url = args.public_url.rstrip('/') if args.public_url else control_url
            server: uvicorn.Server | None = None

            def shutdown() -> None:
                if server:
                    server.should_exit = True

            app = create_app(root, source=source or (DemoSource() if args.demo else None),
                             token=token, fault=fault, shutdown=shutdown,
                             lease=args.lease, grace=args.grace, api_only=args.api_only, persistent=args.persistent,
                             public_url=args.public_url, codex_client=codex_client, rss_enabled=not args.demo, database_url=database_url,
                             metrics_token=metrics_token,
                             telegram_client=LocalTelegram(telegram_env, root) if telegram_env and not args.demo else None)
            config = uvicorn.Config(app, access_log=False, log_level='critical', timeout_graceful_shutdown=2, proxy_headers=False,
                                    ssl_certfile=str(args.tls_cert) if args.tls_cert else None,
                                    ssl_keyfile=str(args.tls_key) if args.tls_key else None)
            server = uvicorn.Server(config)

            async def serve() -> None:
                assert server is not None
                task = asyncio.create_task(server.serve(sockets=[sock]))
                while not server.started and not task.done():
                    await asyncio.sleep(.01)
                if server.started:
                    temporary = root / 'runtime.tmp'
                    version = json.loads((Path(__file__).parent / 'reader/generated/openapi.json').read_text())['info']['version']
                    temporary.write_text(json.dumps(dict(url=url, control_url=control_url, token=token, pid=os.getpid(),
                                                         lifetime='explicit' if args.api_only or args.persistent else 'client_presence', api_version=version)))
                    os.replace(temporary, root / 'runtime.json')
                    print(f'Око запущено: {url} (источник: {"тестовый" if args.demo else "RSS / Telegram" if telegram_env else "RSS"}).', flush=True)
                    if not (args.no_browser or args.api_only):
                        await asyncio.to_thread(webbrowser.open, url + ('/' if args.persistent else '/#token=' + token))
                await task

            # asyncio.run waits indefinitely for cancellation-resistant tasks.
            # This loop belongs solely to Oko; closing it bounds owned work.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda _loop, _context: None)
            try:
                loop.run_until_complete(serve())
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.wait(pending, timeout=.25))
                loop.close()
                asyncio.set_event_loop(None)
        finally:
            sock.close()
            for name in ('runtime.json', 'runtime.tmp'):
                (root / name).unlink(missing_ok=True)


if __name__ == '__main__':
    run()
